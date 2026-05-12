"""Sheet Reader — 飞书电子表格读取器

流程：
  1. Tab 发现 & 筛选（关键词匹配 tab title，兜底取全部）
  2. 关键词定位（小表全量读取，大表 Find API 定位）
  3. Section 扫描（空行分割 / 命中坐标上下扩展）
  4. Merge cell 填充
  5. 表头检测（coverage ratio 确认）
  6. 输出 MD（≤30 行直出，>30 行 LLM 筛选行索引）
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

from .client import FeishuClient
import time
import random
from .url_tool import parse_feishu_url
from .basic_tool.sheet_utils import number_to_column
from tools.core.models import UnifiedChunk

logger = logging.getLogger(__name__)


# Module-level helper: call external API with exponential backoff on rate-limit
def _api_call_with_backoff(
    func, *f_args,
    max_attempts: int = 5,
    per_call_timeout_s: float | None = None,
    **f_kwargs,
):
    """带 exp-backoff 的 API 调用 wrapper。

    重试触发条件：
      · ``99991400``：飞书 app-level 限流
      · ``90235`` / ``"data not ready"``：暂态错误（cache 未刷新、跨机房同步 lag），
        本地实测 1-2s 后大概率成功，作为可恢复错误处理
      · HTTP ``429`` / ``"rate limit"`` / ``"request trigger frequency"``

    backoff 策略：
      · 90235 暂态：固定快速（0.5 + 0.5·attempt + jitter），最大 1.5s——这种错误
        通常 1s 就好，不需要长 backoff
      · 限流类：指数 backoff (min(10, 2^n + jitter))

    Args:
        func: 待调用函数（必须接受 ``timeout`` kwarg 才能用 per_call_timeout_s）
        per_call_timeout_s: 注入到 func 的 ``timeout`` kwarg（仅当 caller 没显式传
            timeout 时生效）。配合上层 asyncio.wait_for 的总预算用——例如总预算 8s
            时设 3.0，单次 3s 内失败留时间给 2-3 次重试。
        max_attempts: 最大尝试次数（含首次），默认 5
    """
    last_exc = None
    if per_call_timeout_s is not None and "timeout" not in f_kwargs:
        f_kwargs["timeout"] = per_call_timeout_s

    for attempt in range(max_attempts):
        try:
            return func(*f_args, **f_kwargs)
        except Exception as e:
            last_exc = e
            s = str(e)
            sl = s.lower()
            is_rate_limit = (
                "99991400" in s
                or "rate limit" in sl
                or "request trigger frequency" in sl
                or "429" in s
            )
            is_transient = (
                "90235" in s
                or "data not ready" in sl
            )
            if not (is_rate_limit or is_transient) or attempt == max_attempts - 1:
                raise

            if is_transient:
                # 暂态错误：固定快速 backoff（实测 0.5-1.5s 就好）
                backoff = min(1.5, 0.5 + 0.5 * attempt + random.random() * 0.3)
                err_kind = "transient(90235)"
            else:
                # 限流：指数 backoff
                backoff = min(10.0, (2 ** attempt) + random.random())
                err_kind = "rate-limited"
            logger.warning(
                "sheet_reader: %s on %s (attempt %d/%d), backing off %.2fs (err=%.60s)",
                err_kind, getattr(func, "__name__", str(func)),
                attempt + 1, max_attempts, backoff, s,
            )
            time.sleep(backoff)
    raise last_exc

# ── 配置 ──────────────────────────────────────────────────────────────────────

SMALL_TAB_ROW_THRESHOLD = 50     # ≤50 行直接全量读取
MAX_TABS_PER_SHEET = 5           # 每个 spreadsheet 最多处理的 tabs
SECTION_MAX_ROWS = 100           # 单个 section 上限
BM25_FILTER_THRESHOLD_ROWS = 40  # >40 行触发 BM25 筛选
BM25_OUTPUT_MAX_ROWS = 30         # BM25 筛选后最多保留行数
READ_MAX_ROWS = 500              # 单次 read_range 最大行数


# ── NLP 拆词 ──────────────────────────────────────────────────────────────────

def extract_keywords(query: str) -> List[str]:
    """从 query 中提取搜索关键词（简单 NLP：中文 2+ 字词 + 英文词）。"""
    cn = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    en = re.findall(r'[a-zA-Z]{2,}', query)
    return list(dict.fromkeys(cn + en))  # 去重保序


# ── Cell 坐标解析 ──────────────────────────────────────────────────────────────

def _parse_cell_ref(cell_ref: str) -> Tuple[int, int]:
    """解析 cell 引用如 'A12' → (row=11, col=0)（0-indexed）。"""
    m = re.match(r'^([A-Z]+)(\d+)$', cell_ref.upper())
    if not m:
        return (0, 0)
    col_str, row_str = m.group(1), m.group(2)
    col = 0
    for ch in col_str:
        col = col * 26 + (ord(ch) - ord('A') + 1)
    return (int(row_str) - 1, col - 1)


# ── 行分析工具 ────────────────────────────────────────────────────────────────

def _fill_count(row) -> int:
    """行中非空 cell 数。"""
    if not row:
        return 0
    return sum(1 for c in row if c is not None and str(c).strip())


def _filled_cols(row) -> set:
    """行中非空 cell 的列索引集合。"""
    if not row:
        return set()
    return {j for j, c in enumerate(row) if c is not None and str(c).strip()}


def _is_empty_row(row) -> bool:
    return _fill_count(row) == 0


def _coverage_ratio(header_cols: set, data_cols: set) -> float:
    """数据行非空列被表头覆盖的比例。"""
    if not data_cols:
        return 0.0
    return len(header_cols & data_cols) / len(data_cols)


# ── Merge cell 填充 ──────────────────────────────────────────────────────────

def _fill_merge_cells(rows: List[list], merges: List[dict], row_offset: int = 0):
    """用 merge metadata 将左上角值填充到 merge 范围内所有 null cell（原地修改）。"""
    for m in merges:
        sr = m["start_row_index"] - row_offset
        er = m["end_row_index"] - row_offset
        sc = m["start_column_index"]
        ec = m["end_column_index"]
        if sr < 0 or sr >= len(rows):
            continue
        # 左上角值
        src_row = rows[sr] if sr < len(rows) else []
        val = src_row[sc] if sc < len(src_row) else None
        if val is None:
            continue
        for r in range(max(sr, 0), min(er + 1, len(rows))):
            for c in range(sc, ec + 1):
                if r == sr and c == sc:
                    continue  # 跳过源 cell
                # 确保行长度足够
                while len(rows[r]) <= c:
                    rows[r].append(None)
                if rows[r][c] is None or str(rows[r][c]).strip() == "":
                    rows[r][c] = val


# ── Section 检测 ──────────────────────────────────────────────────────────────

def _split_sections(rows: List[list]) -> List[Tuple[int, int]]:
    """按空行分割 sections，返回 [(start_idx, end_idx), ...]（闭区间）。"""
    sections = []
    section_start = None
    for i, row in enumerate(rows):
        if _is_empty_row(row):
            if section_start is not None:
                sections.append((section_start, i - 1))
                section_start = None
        else:
            if section_start is None:
                section_start = i
    if section_start is not None:
        sections.append((section_start, len(rows) - 1))
    return sections


def _find_sections_around_hits(rows: List[list], hit_rows: List[int]) -> List[Tuple[int, int]]:
    """从命中行向上/下扫描到空行，获取所在 section 范围。"""
    all_sections = _split_sections(rows)
    hit_set = set(hit_rows)
    selected = []
    for start, end in all_sections:
        for h in hit_set:
            if start <= h <= end:
                selected.append((start, end))
                break
    # 去重
    return list(dict.fromkeys(selected))


# ── 表头检测 ──────────────────────────────────────────────────────────────────

def _find_header_in_section(
    section_rows: List[list],
    merges: List[dict],
    section_start_global: int,
) -> Optional[int]:
    """在 section 内找表头行索引（section-local）。

    规则：
    1. 跳过 fill=1 的行（标题/标签）
    2. 跳过 merge 非首行（数据行，不是表头）
    3. 第一个 fill ≥ 3 的行 = 候选表头
    4. 确认：候选与后续 5 行 coverage ≥ 0.6
    """
    # 标记 merge 非首行
    merge_non_first = set()
    for m in merges:
        sr = m["start_row_index"]
        er = m["end_row_index"]
        for r in range(sr + 1, er + 1):
            merge_non_first.add(r)

    for i, row in enumerate(section_rows):
        global_row = section_start_global + i
        if global_row in merge_non_first:
            continue
        fc = _fill_count(row)
        if fc < 3:
            continue
        # 候选表头，检查 coverage
        header_cols = _filled_cols(row)
        subsequent = [r for r in section_rows[i + 1:i + 8] if _fill_count(r) > 0][:5]
        if not subsequent:
            return i  # 没有后续行，但 fill≥3，仍认为是表头
        avg_cov = sum(_coverage_ratio(header_cols, _filled_cols(r)) for r in subsequent) / len(subsequent)
        if avg_cov >= 0.6:
            return i
    return None


# ── MD 输出 ──────────────────────────────────────────────────────────────────

def _rows_to_markdown(header_row: list, data_rows: List[list], max_cols: int = 20) -> str:
    """将表头 + 数据行渲染为 Markdown table。"""
    def _cell(val):
        if val is None:
            return ""
        return str(val).replace("|", "\\|").replace("\n", " ").strip()

    ncols = min(len(header_row), max_cols)
    header = [_cell(header_row[j]) if j < len(header_row) else "" for j in range(ncols)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(ncols)) + " |",
    ]
    for row in data_rows:
        cells = [_cell(row[j]) if row and j < len(row) else "" for j in range(ncols)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _section_to_markdown(
    section_rows: List[list],
    header_idx: Optional[int],
    max_cols: int = 20,
) -> str:
    """将 section 转为 MD。有表头则分 header + data；无表头则逐行输出 key-value。"""
    if header_idx is not None:
        desc_rows = section_rows[:header_idx]
        header_row = section_rows[header_idx]
        data_rows = section_rows[header_idx + 1:]
        parts = []
        # 说明文字
        for row in desc_rows:
            text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
            if text:
                parts.append(text)
        # 表格
        parts.append(_rows_to_markdown(header_row, data_rows, max_cols))
        return "\n".join(parts)
    else:
        # 无表头 → key-value 描述
        lines = []
        for row in section_rows:
            text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
            if text:
                lines.append(text)
        return "\n".join(lines)


# ── LLM 行筛选（>30 行 section）──────────────────────────────────────────────

def _bm25_filter_rows(
    header_row: list,
    data_rows: List[list],
    query: str,
    max_output: int = BM25_OUTPUT_MAX_ROWS,
) -> List[int]:
    """[P2.5] BM25 筛选与 query 相关的行，返回行索引列表。

    替代 LLM 筛选：BM25 评分 <5ms（vs LLM 2-3s），且不存在 token 超限问题。
    测试验证：BM25 对行级文本有足够区分度（score 0-5.34 范围）。
    """
    from tools.core.chunk_scorer import bm25_score

    header_text = " ".join(str(c).strip() for c in header_row if c is not None)
    scored: List[tuple] = []
    for i, row in enumerate(data_rows):
        # 将表头字段名拼入行文本以提升匹配率
        row_text = " ".join(str(c).strip() for c in row if c is not None and str(c).strip())
        if not row_text:
            continue
        combined = f"{header_text} {row_text}"
        score = bm25_score(query, combined)
        scored.append((score, i))

    # 按 BM25 分数降序，取 top-K
    scored.sort(key=lambda x: x[0], reverse=True)
    # 保留有分数的行（score > 0），最多 max_output 个
    result = [i for s, i in scored if s > 0][:max_output]
    # 如果 BM25 全部为 0（关键词无命中），fallback 取前 max_output 行
    if not result:
        result = list(range(min(len(data_rows), max_output)))
    return result


# ── 裁剪工具 ──────────────────────────────────────────────────────────────────

def _trim_rows(rows: List[list]) -> Tuple[List[list], int]:
    """裁剪尾部空行，返回 (trimmed_rows, effective_row_count)。"""
    last_nonempty = -1
    for i in range(len(rows) - 1, -1, -1):
        if not _is_empty_row(rows[i]):
            last_nonempty = i
            break
    if last_nonempty < 0:
        return [], 0
    trimmed = rows[:last_nonempty + 1]
    return trimmed, last_nonempty + 1


def _effective_max_col(rows: List[list]) -> int:
    """找到所有行中最大的非空列索引 + 1。"""
    max_col = 0
    for row in rows:
        if not row:
            continue
        for j in range(len(row) - 1, -1, -1):
            if row[j] is not None and str(row[j]).strip():
                max_col = max(max_col, j + 1)
                break
    return max_col


# ── 主入口 ──────────────────────────────────────────────────────────────────

def read_sheets(
    query: str,
    candidates: List[dict],
    uat: str,
) -> List[UnifiedChunk]:
    """读取 sheet candidates 并返回 UnifiedChunk 列表。

    Args:
        query:       搜索子查询
        candidates:  搜索返回的 sheet candidate list [{id, title, url, doc_type, ...}]
        uat:         用户访问令牌

    Returns:
        List[UnifiedChunk]
    """
    client = FeishuClient(user_access_token=uat)
    keywords = extract_keywords(query)
    all_chunks: List[UnifiedChunk] = []

    # 并行读取多个 spreadsheet candidates
    with ThreadPoolExecutor(max_workers=min(len(candidates), 3)) as executor:
        future_to_cand = {
            executor.submit(_read_single_sheet, client, cand, keywords, query): cand
            for cand in candidates
        }
        for future in as_completed(future_to_cand):
            cand = future_to_cand[future]
            try:
                all_chunks.extend(future.result())
            except Exception as e:
                logger.warning("sheet_reader: failed to read %s: %s", cand.get("title", ""), e)

    return all_chunks


def _read_single_sheet(
    client: FeishuClient,
    candidate: dict,
    keywords: List[str],
    query: str,
) -> List[UnifiedChunk]:
    """读取单个 spreadsheet 的相关内容。"""
    spreadsheet_token = candidate.get("id", "")
    sheet_title = candidate.get("title", "")
    sheet_url = candidate.get("url", "")

    # 快速路径：table_pool 已注入缓存 + selected_sheet_ids 已知
    # 直接跳过 wiki token 解析和 query_all_sheets_metadata API 调用
    cached_tabs = candidate.get("_cached_tabs")
    selected_sheet_ids = set(candidate.get("selected_sheet_ids") or [])
    if cached_tabs is not None and selected_sheet_ids:
        candidate_tabs = [t for t in cached_tabs if t.get("sheet_id") in selected_sheet_ids]
        if not candidate_tabs:
            # selected_sheet_ids 有但在缓存里找不到，fallback 到全部可见 tab
            candidate_tabs = [t for t in cached_tabs if not t.get("hidden")][:MAX_TABS_PER_SHEET]
        if not candidate_tabs:
            return []
        chunks = []
        with ThreadPoolExecutor(max_workers=min(len(candidate_tabs), 5)) as executor:
            future_to_tab = {
                executor.submit(
                    _read_single_tab,
                    client, spreadsheet_token, tab, keywords, query,
                    sheet_title, sheet_url,
                ): tab
                for tab in candidate_tabs
            }
            for future in as_completed(future_to_tab):
                tab = future_to_tab[future]
                try:
                    chunks.extend(future.result())
                except Exception as e:
                    logger.warning("sheet_reader: tab %s failed: %s", tab.get("title", ""), e)
        return chunks

    # 慢速路径：无缓存，需要解析 wiki token 并拉取 tab 列表
    # Wiki-hosted sheet: prefer node_id or candidate id and resolve to obj_token
    is_wiki = (candidate.get("_source") == "wiki" or (sheet_url and "/wiki/" in sheet_url and "/sheets/" not in sheet_url))
    if is_wiki:
        wiki_token = candidate.get("node_id") or candidate.get("id") or candidate.get("document_id") or ""
        if not wiki_token and sheet_url:
            import re as _re
            m = _re.search(r'/wiki/([^/?#]+)', sheet_url)
            if m:
                wiki_token = m.group(1)
        if wiki_token:
            try:
                node_info = _api_call_with_backoff(client.get_wiki_node_info, wiki_token)
                obj_token = node_info.get("obj_token", "")
                obj_type = node_info.get("obj_type", "").lower()
                if obj_token and obj_type in ("sheet", "sheets"):
                    spreadsheet_token = obj_token
                elif obj_token and obj_type and obj_type not in ("sheet", "sheets"):
                    logger.info("sheet_reader: wiki node is %s not sheet, skip", obj_type)
                    return []
            except Exception as e:
                logger.warning("sheet_reader: wiki node resolve failed for %s: %s", wiki_token, e)

    if not spreadsheet_token:
        return []

    # Step 1: 获取所有 tabs 元数据（有缓存但无 selected_sheet_ids 时也走这里）
    if cached_tabs is not None:
        visible_tabs = [t for t in cached_tabs if not t.get("hidden")]
    else:
        try:
            tabs = _api_call_with_backoff(
                client.query_all_sheets_metadata, spreadsheet_token,
                per_call_timeout_s=3.0,
            )
        except Exception as e:
            logger.warning("sheet_reader: metadata failed for %s: %s", spreadsheet_token, e)
            return []
        visible_tabs = [t for t in tabs if not t.get("hidden") and t.get("resource_type") == "sheet"]
    if not visible_tabs:
        return []

    if selected_sheet_ids:
        candidate_tabs = [t for t in visible_tabs if t.get("sheet_id") in selected_sheet_ids]
    else:
        # Tab 筛选 — 关键词匹配 tab title
        matched_tabs = []
        for t in visible_tabs:
            tab_title = t.get("title", "").lower()
            for kw in keywords:
                if kw.lower() in tab_title:
                    matched_tabs.append(t)
                    break
        candidate_tabs = matched_tabs if matched_tabs else visible_tabs
        candidate_tabs = candidate_tabs[:MAX_TABS_PER_SHEET]

    if not candidate_tabs:
        return []

    # 并行读取所有候选 tabs
    chunks = []
    with ThreadPoolExecutor(max_workers=min(len(candidate_tabs), 5)) as executor:
        future_to_tab = {
            executor.submit(
                _read_single_tab,
                client, spreadsheet_token, tab, keywords, query,
                sheet_title, sheet_url,
            ): tab
            for tab in candidate_tabs
        }
        for future in as_completed(future_to_tab):
            tab = future_to_tab[future]
            try:
                chunks.extend(future.result())
            except Exception as e:
                logger.warning("sheet_reader: tab %s failed: %s", tab.get("title", ""), e)

    return chunks


def _read_single_tab(
    client: FeishuClient,
    spreadsheet_token: str,
    tab: dict,
    keywords: List[str],
    query: str,
    sheet_title: str,
    sheet_url: str,
) -> List[UnifiedChunk]:
    """读取单个 tab，返回 UnifiedChunk 列表。"""
    sheet_id = tab["sheet_id"]
    tab_title = tab.get("title", "")
    row_count = min(tab.get("row_count", 200), READ_MAX_ROWS)
    col_count = tab.get("column_count", 20)
    merges = tab.get("merges", [])

    # Step 2a: 读取数据
    if row_count <= SMALL_TAB_ROW_THRESHOLD:
        # 小表：全量读取
        range_str = f"{sheet_id}!A1:{number_to_column(col_count - 1)}{row_count}"
        try:
            rows = _api_call_with_backoff(
                client.read_sheet_range, spreadsheet_token, range_str,
                per_call_timeout_s=3.0,
            )
        except Exception:
            return []
        rows, effective_rows = _trim_rows(rows)
        if not rows:
            return []
        # 空行分割 section
        sections = _split_sections(rows)
    else:
        # 大表：Find API 定位
        # find_cells 走的是 sheets v2 端点（与 docx 99991400 桶**不共享**，
        # 服务端有独立 QPS）。多个 keyword 并行调用，wall-clock 由 N×单次 降到 1×单次。
        # 实测：query='nubia DTC 服务' → 3 个 keyword，从串行 3×6s ≈ 18s 降到 ~6s。
        hit_rows_set = set()
        range_str = f"{sheet_id}!A1:{number_to_column(col_count - 1)}{row_count}"

        def _find_one(kw: str) -> List[str]:
            try:
                return _api_call_with_backoff(
                    client.find_cells, spreadsheet_token, sheet_id, range_str, kw,
                ) or []
            except Exception:
                return []

        if keywords:
            # 单进程内的轻量 worker pool，仅覆盖此 tab 的 find_cells 调用
            with ThreadPoolExecutor(max_workers=min(len(keywords), 4)) as kw_ex:
                future_map = {kw_ex.submit(_find_one, kw): kw for kw in keywords}
                for fut in as_completed(future_map):
                    for cell_ref in fut.result():
                        r, _c = _parse_cell_ref(cell_ref)
                        hit_rows_set.add(r)

        if not hit_rows_set:
            # Find 无命中 → 读取前 50 行
            range_str = f"{sheet_id}!A1:{number_to_column(col_count - 1)}{min(row_count, 50)}"
            try:
                rows = _api_call_with_backoff(
                    client.read_sheet_range, spreadsheet_token, range_str,
                    per_call_timeout_s=3.0,
                )
            except Exception:
                return []
            rows, _ = _trim_rows(rows)
            sections = _split_sections(rows) if rows else []
        else:
            # 读取全量（大表也需全量来正确分割 section）
            try:
                rows = _api_call_with_backoff(
                    client.read_sheet_range, spreadsheet_token, range_str,
                    per_call_timeout_s=3.0,
                )
            except Exception:
                return []
            rows, _ = _trim_rows(rows)
            sections = _find_sections_around_hits(rows, list(hit_rows_set))

    if not rows or not sections:
        return []

    # Step 3: Merge cell 填充
    _fill_merge_cells(rows, merges, row_offset=0)

    # Step 4: 每个 section → chunk
    max_col = _effective_max_col(rows)
    chunks = []
    for sec_idx, (section_start, section_end) in enumerate(sections):
        # O6: 去掉 section_max_rows 硬截断，改用 LLM 筛选控制输出行数
        # section_end = min(section_end, section_start + SECTION_MAX_ROWS - 1)  # 已去掉
        section_rows = rows[section_start:section_end + 1]
        if not section_rows or all(_is_empty_row(r) for r in section_rows):
            continue

        # 表头检测
        header_idx = _find_header_in_section(section_rows, merges, section_start)

        # 计算 header_coverage 分数（O2）
        cov_score = 0.0
        if header_idx is not None and header_idx < len(section_rows) - 1:
            from feishu_read_tool.table_handler import header_coverage as _hc
            try:
                cov_score = _hc(
                    section_rows[header_idx],
                    section_rows[header_idx + 1:],
                )
            except Exception:
                cov_score = 0.0

        is_bm25_filtered = False
        # 输出
        if header_idx is not None and len(section_rows) - header_idx - 1 > BM25_FILTER_THRESHOLD_ROWS:
            # [P2.5] >30 行数据 → BM25 筛选（替代 LLM，<5ms vs 2-3s）
            header_row = section_rows[header_idx]
            data_rows = section_rows[header_idx + 1:]
            desc_rows = section_rows[:header_idx]

            selected_indices = _bm25_filter_rows(header_row, data_rows, query)
            selected_data = [data_rows[i] for i in selected_indices if i < len(data_rows)]
            is_bm25_filtered = True

            parts = []
            for row in desc_rows:
                text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip())
                if text:
                    parts.append(text)
            parts.append(_rows_to_markdown(header_row, selected_data, max_col))
            parts.append(f"\n[筛选自 {len(data_rows)} 行数据中的 {len(selected_data)} 行]")
            md = "\n".join(parts)
        else:
            md = _section_to_markdown(section_rows, header_idx, max_col)

        if not md.strip():
            continue

        heading = f"Sheet: {sheet_title} > {tab_title}"
        # 加上 section 描述（表头前的说明文字）
        first_text = ""
        if header_idx and header_idx > 0:
            for row in section_rows[:header_idx]:
                t = " ".join(str(c).strip() for c in row if c is not None and str(c).strip())
                if t:
                    first_text = t
                    break
            if first_text:
                heading += f" > {first_text[:50]}"

        # O3: 修正分数公式——使用关键词泛化扩展的 BM25，与下游 RSF 对齐
        from tools.core.chunk_scorer import bm25_score
        from tools.core.kw_generalization import expand_keywords
        _raw_kw = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
        _expanded_q = " ".join(expand_keywords(_raw_kw))
        score_text = f"{sheet_title} {tab_title} {first_text} {md[:500]}"
        raw_bm25 = bm25_score(_expanded_q, score_text)
        chunk_score = min(1.0, raw_bm25 / 5.0)  # 归一化到 [0, 1]

        # O2: 丰富 metadata
        chunks.append(UnifiedChunk(
            text=md,
            source_name=sheet_title,
            source_url=sheet_url,
            source_tool="feishu",
            heading_path=heading,
            score=chunk_score,
            metadata={
                "doc_type": "SHEET",
                "tab": tab_title,
                "sheet_id": sheet_id,
                "section_id": f"{spreadsheet_token}_{sheet_id}_s{sec_idx}",
                "row_start": section_start,
                "row_end": section_end,
                "table_rows": len(section_rows) - (1 if header_idx is not None else 0),
                "header_coverage": round(cov_score, 3),
                "is_llm_filtered": is_bm25_filtered,  # [P2.5] now BM25, not LLM
            },        ))

    return chunks
