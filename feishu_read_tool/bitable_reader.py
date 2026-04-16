"""Bitable Reader — 飞书多维表格读取器

流程：
  1. Table 发现 & 筛选（URL 携带 table_id 直选 / 关键词匹配 table name）
  2. Field 匹配（关键词匹配 field_name，确认候选 table）
  3. 记录召回（contains filter 精确匹配 / fallback 取前 100 条）
  4. Records → 2D array → MD 输出（≤30 行直出，>30 行 LLM 筛选行索引）
"""

import concurrent.futures
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

from .basic_tool.bitable_read import list_tables, search_records, search_records_with_filter, list_fields
from .url_tool import parse_feishu_url
from .client import FeishuClient
import time
import random
from tools.models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────────

MAX_RECORDS = 200               # search_records 最大返回条数
FALLBACK_RECORDS = 100          # contains filter 无命中时 fallback 取前 N 条
MAX_TABLES_PER_BITABLE = 3     # 每个 bitable 最多处理的 tables
LLM_THRESHOLD_ROWS = 30        # >30 行触发 LLM 筛选
LLM_OUTPUT_MAX_ROWS = 20       # LLM 筛选后最多保留行数

# 可文本化的字段类型（参考 API 文档 /reference/bitable-v1/app-table-field/list）
_TEXT_FIELD_TYPES = {
    1,    # Text 文本
    2,    # Number 数字
    3,    # SingleSelect 单选
    4,    # MultiSelect 多选
    5,    # DateTime 日期（ms 时间戳，_flatten_value 自动转换）
    7,    # Checkbox 复选框
    11,   # Person 人员
    13,   # Phone 电话号码
    15,   # URL 超链接
    20,   # Formula 公式（文本/数字型）
    1001, # CreatedTime 创建时间
    1002, # ModifiedTime 最后更新时间
    1003, # CreatedUser 创建人
    1004, # ModifiedUser 修改人
}
# 排除：17(Attachment), 18(SingleLink), 21(DuplexLink), 22(Location),
#       23(GroupChat), 1005(AutoNumber)
# Text 类型字段（支持 contains 搜索）
_SEARCHABLE_FIELD_TYPES = {1}  # 只有纯文本字段支持 contains operator


# ── NLP 拆词（复用 sheet_reader 的逻辑）──────────────────────────────────────

def _extract_keywords(query: str) -> List[str]:
    cn = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    en = re.findall(r'[a-zA-Z]{2,}', query)
    return list(dict.fromkeys(cn + en))


# ── DateTime 格式化 ──────────────────────────────────────────────────────────

# 飞书 DateTime 字段以毫秒时间戳存储；用此阈值区分日期与普通数值
# 10^12 ms ≈ 2001-09-09，实际日期类数据均大于此值
_TS_THRESHOLD_MS = 1_000_000_000_000


def _format_timestamp(val: int) -> str:
    """将毫秒时间戳格式化为 'YYYY-MM-DD' 字符串。"""
    try:
        dt = datetime.fromtimestamp(val / 1000, tz=timezone.utc)
        # If time component is zero (midnight UTC), emit date only; otherwise include time.
        if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
            return dt.strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(val)


# ── Field 值展平 ──────────────────────────────────────────────────────────────

def _flatten_value(val) -> str:
    """将复杂字段值展平为纯文本。

    DateTime 字段（毫秒时间戳整数）自动转换为 YYYY-MM-DD 格式。
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return "是" if val else "否"
    if isinstance(val, int):
        # 毫秒时间戳 → 日期字符串
        if val >= _TS_THRESHOLD_MS:
            return _format_timestamp(val)
        return str(val)
    if isinstance(val, float):
        # 浮点数也可能是时间戳
        if val >= _TS_THRESHOLD_MS:
            return _format_timestamp(int(val))
        # 去掉不必要的 .0
        return str(int(val)) if val == int(val) else f"{val:.4g}"
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for v in val:
            if isinstance(v, dict):
                # text/name 优先，fallback 到 value 或 str
                text = v.get("text") or v.get("name") or v.get("value")
                if text is not None:
                    parts.append(_flatten_value(text))
                else:
                    parts.append(str(v)[:50])
            else:
                parts.append(_flatten_value(v))
        return ", ".join(p for p in parts if p)
    if isinstance(val, dict):
        text = val.get("text") or val.get("name") or val.get("value")
        if text is not None:
            return _flatten_value(text)
        # URL 字段 → 显示 link 文字
        if "link" in val:
            return val.get("text", val["link"])[:80]
        return str(val)[:80]
    return str(val)[:80]


# ── MD 输出 ──────────────────────────────────────────────────────────────────

def _records_to_markdown(field_names: List[str], records: List[dict]) -> str:
    """将字段名 + records 渲染为 Markdown table。"""
    def _cell(val):
        s = _flatten_value(val)
        return s.replace("|", "\\|").replace("\n", " ").strip()

    lines = [
        "| " + " | ".join(field_names) + " |",
        "| " + " | ".join("---" for _ in field_names) + " |",
    ]
    for rec in records:
        fields = rec.get("fields", {})
        cells = [_cell(fields.get(fn, "")) for fn in field_names]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ── LLM 行筛选 ──────────────────────────────────────────────────────────────

def _bm25_filter_records(
    field_names: List[str],
    records: List[dict],
    query: str,
    max_output: int = LLM_OUTPUT_MAX_ROWS,
) -> List[int]:
    """[P2.5] BM25 筛选与 query 相关的记录，返回索引列表。

    替代 LLM 筛选：BM25 评分 <5ms（vs LLM 2-3s），无 token 超限问题。
    """
    from tools.chunk_scorer import bm25_score

    header_text = " ".join(field_names)
    scored: List[tuple] = []
    for i, rec in enumerate(records):
        fields = rec.get("fields", {})
        row_text = " ".join(_flatten_value(fields.get(fn, "")) for fn in field_names)
        if not row_text.strip():
            continue
        combined = f"{header_text} {row_text}"
        score = bm25_score(query, combined)
        scored.append((score, i))

    scored.sort(key=lambda x: x[0], reverse=True)
    result = [i for s, i in scored if s > 0][:max_output]
    if not result:
        result = list(range(min(len(records), max_output)))
    return result


# ── URL 解析辅助 ──────────────────────────────────────────────────────────────

def _extract_bitable_id_from_url(url: str) -> Tuple[str, Optional[str]]:
    """从 URL 提取 bitable_id 和可选的 table_id。"""
    try:
        parsed = parse_feishu_url(url)
        result = parsed.get("result", {})
        bitable_id = result.get("bitable_id", "")
        table_id = result.get("table_id")
        return bitable_id, table_id
    except Exception:
        return "", None


# Module-level helper: call external API with exponential backoff on rate-limit
def _api_call_with_backoff(func, *f_args, max_attempts: int = 5, **f_kwargs):
    last_exc = None
    for attempt in range(max_attempts):
        try:
            return func(*f_args, **f_kwargs)
        except Exception as e:
            last_exc = e
            s = str(e)
            if "99991400" in s or "rate limit" in s.lower() or "request trigger frequency" in s.lower() or "429" in s:
                backoff = min(10.0, (2 ** attempt) + random.random())
                logger.warning("bitable_reader: API rate-limited on %s (attempt %d/%d), backing off %.2fs",
                               getattr(func, "__name__", str(func)), attempt + 1, max_attempts, backoff)
                time.sleep(backoff)
                continue
            else:
                raise
    raise last_exc


# ── 主入口 ──────────────────────────────────────────────────────────────────

def read_bitables(
    query: str,
    candidates: List[dict],
    uat: str,
) -> List[UnifiedChunk]:
    """读取 bitable candidates 并返回 UnifiedChunk 列表。

    [P2.3-F3] 并行读取多个 bitable（每个 bitable 是不同 app_token，API 配额独立）。
    """
    keywords = _extract_keywords(query)
    all_chunks: List[UnifiedChunk] = []

    if len(candidates) <= 1:
        # 单个 bitable 直接读取，无需线程池开销
        for cand in candidates:
            try:
                chunks = _read_single_bitable(cand, keywords, query, uat)
                all_chunks.extend(chunks)
            except Exception as e:
                logger.warning("bitable_reader: failed to read %s: %s", cand.get("title", ""), e)
    else:
        # 多个 bitable 并行读取
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(candidates), 3)) as ex:
            futures = {
                ex.submit(_read_single_bitable, cand, keywords, query, uat): cand
                for cand in candidates
            }
            for fut in concurrent.futures.as_completed(futures):
                cand = futures[fut]
                try:
                    chunks = fut.result()
                    all_chunks.extend(chunks)
                except Exception as e:
                    logger.warning("bitable_reader: failed to read %s: %s", cand.get("title", ""), e)

    return all_chunks


def _read_single_bitable(
    candidate: dict,
    keywords: List[str],
    query: str,
    uat: str,
) -> List[UnifiedChunk]:
    """读取单个 bitable 的相关内容。"""
    bitable_id = candidate.get("id", "")
    bitable_title = candidate.get("title", "")
    bitable_url = candidate.get("url", "")

    # 从 URL 尝试提取 table_id（如果有）
    url_table_id = None
    if bitable_url:
        _, url_table_id = _extract_bitable_id_from_url(bitable_url)

    # Wiki URL 需要先解析 node → obj_token
    # 检查 URL 是否包含 /wiki/ 或 _source 为 wiki
    is_wiki = (candidate.get("_source") == "wiki" or
               ("/wiki/" in bitable_url and "/base/" not in bitable_url))
    # Prefer explicit node_id, fall back to candidate id or URL-extracted token.
    wiki_token = candidate.get("node_id") or candidate.get("id") or candidate.get("document_id") or ""
    if not wiki_token and is_wiki and bitable_url:
        # 从 URL 提取 wiki node token（更宽松的匹配，取 /wiki/ 后直到分隔符）
        import re as _re
        m = _re.search(r'/wiki/([^/?#]+)', bitable_url)
        if m:
            wiki_token = m.group(1)
    if is_wiki and wiki_token:
        try:
            client = FeishuClient(user_access_token=uat)
            node_info = client.get_wiki_node_info(wiki_token)
            obj_type = node_info.get("obj_type", "").lower()
            obj_token = node_info.get("obj_token", "")
            if obj_token and obj_type == "bitable":
                bitable_id = obj_token
            elif obj_token and obj_type != "bitable":
                logger.info("bitable_reader: wiki node is %s not bitable, skip", obj_type)
                return []
        except Exception as e:
            logger.warning("bitable_reader: wiki node resolve failed for %s: %s", wiki_token, e)

    if not bitable_id:
        return []

    # Step 1: List all tables
    try:
        tables = _api_call_with_backoff(list_tables, bitable_id, uat)
    except Exception as e:
        logger.warning("bitable_reader: list_tables failed for %s: %s", bitable_id, e)
        return []

    if not tables:
        return []

    # Step 2: Table 筛选
    if url_table_id:
        # URL 携带 table_id → 直接选中
        selected_tables = [t for t in tables if t.get("table_id") == url_table_id]
        if not selected_tables:
            selected_tables = tables[:MAX_TABLES_PER_BITABLE]
    else:
        # 关键词匹配 table name
        matched = []
        for t in tables:
            name = t.get("name", "").lower()
            for kw in keywords:
                if kw.lower() in name:
                    matched.append(t)
                    break
        selected_tables = matched if matched else tables
        selected_tables = selected_tables[:MAX_TABLES_PER_BITABLE]

    # Step 3: 每个 table → 读取 → chunks
    chunks = []
    for table in selected_tables:
        try:
            table_chunks = _read_single_table(
                bitable_id, table, keywords, query, uat,
                bitable_title, bitable_url,
            )
            chunks.extend(table_chunks)
        except Exception as e:
            logger.warning("bitable_reader: table %s failed: %s", table.get("name", ""), e)

    return chunks


def _read_single_table(
    bitable_id: str,
    table: dict,
    keywords: List[str],
    query: str,
    uat: str,
    bitable_title: str,
    bitable_url: str,
) -> List[UnifiedChunk]:
    """读取单个 bitable table 的数据。"""
    table_id = table.get("table_id", "")
    table_name = table.get("name", "")

    # Step 3a: 获取字段
    try:
        fields = _api_call_with_backoff(list_fields, bitable_id, table_id, uat)
    except Exception as e:
        logger.warning("bitable_reader: list_fields failed for %s.%s: %s", bitable_id, table_id, e)
        return []

    # 过滤字段：只保留可文本化的类型
    text_fields = [f for f in fields if f.get("type") in _TEXT_FIELD_TYPES]
    if not text_fields:
        text_fields = fields  # fallback: 保留所有

    field_names = [f["field_name"] for f in text_fields]
    # 找出支持 contains 的字段（纯文本类型）
    searchable_fields = [f["field_name"] for f in text_fields
                         if f.get("type") in _SEARCHABLE_FIELD_TYPES]

    # Step 3b: 记录召回 — contains filter（OR 逻辑：任一关键词在任一文本字段中出现）
    records = []
    if keywords and searchable_fields:
        conditions = []
        for fn in searchable_fields:
            for kw in keywords:
                conditions.append({"field_name": fn, "operator": "contains", "value": [kw]})
        # API 最多 50 个 conditions
        conditions = conditions[:50]
        try:
            filter_body = {"conjunction": "or", "conditions": conditions}
            records = _api_call_with_backoff(
                search_records_with_filter,
                bitable_id, table_id, uat,
                filter_body=filter_body,
                field_names=field_names,
                max_records=MAX_RECORDS,
            )
        except Exception as e:
            logger.warning("bitable_reader: contains search failed for %s.%s: %s", bitable_id, table_id, e)
            records = []

    # Fallback: 无 filter 取前 N 条
    if not records:
        try:
            records = _api_call_with_backoff(search_records, bitable_id, table_id, uat, conditions=None)
            records = records[:FALLBACK_RECORDS]
        except Exception as e:
            logger.warning("bitable_reader: fallback search failed for %s.%s: %s", bitable_id, table_id, e)
            return []

    if not records:
        return []

    # Step 4: Records → output
    if len(records) > LLM_THRESHOLD_ROWS:
        # [P2.5] BM25 筛选（替代 LLM，<5ms vs 2-3s）
        selected_indices = _bm25_filter_records(field_names, records, query)
        selected_records = [records[i] for i in selected_indices if i < len(records)]
        md = _records_to_markdown(field_names, selected_records)
        md += f"\n\n[BM25筛选自 {len(records)} 条记录中的 {len(selected_records)} 条]"
    else:
        md = _records_to_markdown(field_names, records)

    if not md.strip():
        return []

    heading = f"Bitable: {bitable_title} > {table_name}"
    # O3: 使用关键词泛化扩展的 BM25，与下游 RSF 对齐
    from tools.chunk_scorer import bm25_score
    from tools.kw_generalization import expand_keywords
    _raw_kw = set(re.findall(r'[\w\u4e00-\u9fff]{2,}', query.lower()))
    _expanded_q = " ".join(expand_keywords(_raw_kw))
    score_text = f"{bitable_title} {table_name} {md[:500]}"
    raw_bm25 = bm25_score(_expanded_q, score_text)
    chunk_score = min(1.0, raw_bm25 / 5.0)  # 归一化到 [0, 1]

    # O2: 丰富 metadata
    is_llm_filtered = len(records) > LLM_THRESHOLD_ROWS
    return [UnifiedChunk(
        text=md,
        source_name=bitable_title,
        source_url=bitable_url,
        source_tool="feishu",
        heading_path=heading,
        score=chunk_score,
        metadata={
            "doc_type": "BITABLE",
            "table": table_name,
            "table_id": table_id,
            "record_count": len(records),
            "is_llm_filtered": is_llm_filtered,
            "field_names": field_names[:10],
            "searchable_fields": searchable_fields[:5],
        },
    )]



