"""表格处理通用工具
提供按空行切分区块、表头嗅探、BM25行级过滤以及转换为 Markdown 的功能。
"""

import re
from typing import List, Tuple, Optional

# ── 行分析工具 ────────────────────────────────────────────────────────────────

def _fill_count(row: list) -> int:
    """行中非空 cell 数。"""
    if not row:
        return 0
    return sum(1 for c in row if c is not None and str(c).strip() != "")

def _filled_cols(row: list) -> set:
    """行中非空 cell 的列索引集合。"""
    if not row:
        return set()
    return {j for j, c in enumerate(row) if c is not None and str(c).strip() != ""}

def _is_empty_row(row: list) -> bool:
    return _fill_count(row) == 0

def _coverage_ratio(header_cols: set, data_cols: set) -> float:
    """数据行非空列被表头覆盖的比例。"""
    if not data_cols:
        return 0.0
    return len(header_cols & data_cols) / len(data_cols)

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
            if row[j] is not None and str(row[j]).strip() != "":
                max_col = max(max_col, j + 1)
                break
    return max_col


# ── Section 拆分 ──────────────────────────────────────────────────────────────

def split_sections(rows: List[list]) -> List[Tuple[int, int]]:
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


# ── 表头检测 ──────────────────────────────────────────────────────────────────

def find_header_in_section(
    section_rows: List[list],
    merges: List[dict] = None,
    section_start_global: int = 0,
) -> Optional[int]:
    """在 section 内找表头行索引（section-local）。
    规则：
    1. 跳过 fill=1 的行（标题/标签）
    2. 跳过 merge 非首行（数据行，不是表头）
    3. 第一个 fill >= 3 的行 = 候选表头
    4. 确认：候选与后续 5 行 coverage >= 0.6
    """
    if merges is None:
        merges = []
        
    # 标记 merge 非首行
    merge_non_first = set()
    for m in merges:
        sr = m.get("start_row_index", 0)
        er = m.get("end_row_index", 0)
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
            return i  # 没有后续行，但 fill>=3，仍认为是表头
            
        avg_cov = sum(_coverage_ratio(header_cols, _filled_cols(r)) for r in subsequent) / len(subsequent)
        if avg_cov >= 0.6:
            return i
            
    # fallback: 如果没有找到 >=3 且 coverage高的，退而求其次找 fill>=2 的
    for i, row in enumerate(section_rows):
        global_row = section_start_global + i
        if global_row in merge_non_first:
            continue
        if _fill_count(row) >= 2:
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
    if ncols == 0:
        return ""
        
    header = [_cell(header_row[j]) if j < len(header_row) else "" for j in range(ncols)]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in range(ncols)) + " |",
    ]
    for row in data_rows:
        cells = [_cell(row[j]) if row and j < len(row) else "" for j in range(ncols)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


# ── BM25 局部浓缩 ────────────────────────────────────────────────────────────

def bm25_filter_rows(
    header_row: list,
    data_rows: List[list],
    query: str,
    max_output: int = 30,
) -> List[int]:
    """BM25 筛选与 query 相关的行，返回行索引列表。"""
    try:
        from tools.core.chunk_scorer import bm25_score
    except ImportError:
        # fallback 如果没有 bm25_score
        return list(range(min(len(data_rows), max_output)))

    header_text = " ".join(str(c).strip() for c in header_row if c is not None and str(c).strip() != "")
    scored: List[tuple] = []
    
    for i, row in enumerate(data_rows):
        row_text = " ".join(str(c).strip() for c in row if c is not None and str(c).strip() != "")
        if not row_text:
            continue
        combined = f"{header_text} {row_text}"
        score = bm25_score(query, combined)
        scored.append((score, i))

    # 按 BM25 分数降序，取 top-K
    scored.sort(key=lambda x: x[0], reverse=True)
    # 保留有分数的行（score > 0），最多 max_output 个
    result = [i for s, i in scored if s > 0][:max_output]
    
    if not result:
        result = list(range(min(len(data_rows), max_output)))
        
    # 为了保持阅读上下文，我们根据原行号排序一下输出
    return sorted(result)


# ── 主流程 ──────────────────────────────────────────────────────────────────

def process_section_to_markdown(
    section_rows: List[list],
    query: Optional[str] = None,
    merges: List[dict] = None,
    section_start_global: int = 0,
    bm25_threshold_rows: int = 40,
    bm25_output_max_rows: int = 30,
    max_cols: int = 20,
) -> Tuple[str, bool, int, float]:
    """处理单个区块，执行表头检测、BM25过滤并生成 Markdown。
    
    Returns:
        (markdown_text, is_bm25_filtered, effective_row_count, header_coverage_score)
    """
    if not section_rows or all(_is_empty_row(r) for r in section_rows):
        return "", False, 0, 0.0

    max_col = min(_effective_max_col(section_rows), max_cols)
    header_idx = find_header_in_section(section_rows, merges, section_start_global)
    
    cov_score = 0.0
    if header_idx is not None and header_idx < len(section_rows) - 1:
        # 计算覆盖率
        header_cols = _filled_cols(section_rows[header_idx])
        subsequent = [r for r in section_rows[header_idx + 1: header_idx + 6] if _fill_count(r) > 0]
        if subsequent:
            cov_score = sum(_coverage_ratio(header_cols, _filled_cols(r)) for r in subsequent) / len(subsequent)

    is_bm25_filtered = False
    effective_row_count = len(section_rows)
    
    if header_idx is not None:
        desc_rows = section_rows[:header_idx]
        header_row = section_rows[header_idx]
        data_rows = section_rows[header_idx + 1:]
        
        parts = []
        for row in desc_rows:
            text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip() != "")
            if text:
                parts.append(text)
                
        if query and len(data_rows) > bm25_threshold_rows:
            # 触发 BM25 浓缩
            selected_indices = bm25_filter_rows(header_row, data_rows, query, bm25_output_max_rows)
            selected_data = [data_rows[i] for i in selected_indices if i < len(data_rows)]
            is_bm25_filtered = True
            
            parts.append(_rows_to_markdown(header_row, selected_data, max_col))
            parts.append(f"\n[BM25筛选自 {len(data_rows)} 行数据中的 {len(selected_data)} 行]")
            effective_row_count = len(selected_data) + 1  # data + header
        else:
            parts.append(_rows_to_markdown(header_row, data_rows, max_col))
            effective_row_count = len(data_rows) + 1
            
        md = "\n".join(parts)
    else:
        # 无表头 -> 直接转 key-value 或平铺
        lines = []
        for row in section_rows:
            text = " | ".join(str(c).strip() for c in row if c is not None and str(c).strip() != "")
            if text:
                lines.append(text)
        md = "\n".join(lines)
        effective_row_count = len(section_rows)
        
    return md, is_bm25_filtered, effective_row_count, cov_score
