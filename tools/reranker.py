"""轻量级 Rerank — RRF 合并 + 去重 + 置信度分层 + Deny-list 过滤

纯算法实现，零 LLM 调用，延迟 <50ms。
"""

import hashlib
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from .models import UnifiedChunk

logger = logging.getLogger(__name__)

# ── Deny-list: 已知低质量 chunk 模式 ─────────────────────────────

_DENY_PATTERNS = [
    re.compile(r"^[\s\n]*$"),                          # 纯空白
    re.compile(r"^目录\s*[\n|]", re.MULTILINE),         # 纯目录页
    re.compile(r"^(待填写|待补充|TBD|TODO)", re.I),     # 占位符
    re.compile(r"^\s*!\[.*\]\(.*\)\s*$"),               # 纯图片引用（无文本）
]


def _text_hash(text: str) -> str:
    """对 chunk text 取 MD5 用于去重。"""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.md5(normalized.encode()).hexdigest()


# ── 去重 ─────────────────────────────────────────────────────────

def dedup_chunks(chunks: List[UnifiedChunk]) -> List[UnifiedChunk]:
    """按 text hash 去重，保留 score 更高的版本。"""
    seen: Dict[str, UnifiedChunk] = {}
    for c in chunks:
        h = _text_hash(c.text)
        if h not in seen or c.score > seen[h].score:
            seen[h] = c
    return list(seen.values())


# ── Deny-list 过滤 ───────────────────────────────────────────────

def deny_list_filter(chunks: List[UnifiedChunk]) -> List[UnifiedChunk]:
    """过滤匹配 deny-list 模式的低质量 chunks。"""
    result = []
    for c in chunks:
        text = c.text.strip()
        if len(text) < 10:
            continue
        if any(p.search(text) for p in _DENY_PATTERNS):
            logger.debug("Deny-list filtered: %.50s...", text)
            continue
        result.append(c)
    return result


# ── RRF (Reciprocal Rank Fusion) ─────────────────────────────────

def rrf_merge(
    chunks: List[UnifiedChunk],
    k: int = 60,
    multi_hit_boost: float = 1.09,
    source_weight: float = 0.7,
) -> List[UnifiedChunk]:
    """RSF + RRF 混合评分（Relative Score Fusion + Reciprocal Rank Fusion）。

    参考 Weaviate relativeScoreFusion（v1.24 默认算法），改进为全局归一化：
      - 对所有 chunks 按 source_tool 做全局 min-max 归一化
      - 与传统 RRF rank score 做加权融合

    多 group 命中 boost:
      同一 chunk 被 N 个 rrf_group 命中时，取 max(base_scores) 作为基础分，
      再乘以 multi_hit_boost^(min(N-1, 2))。即：
        2 groups → ×1.09
        3+ groups → ×1.09² ≈ ×1.1881（封顶）

    Args:
        chunks: 已去重的 UnifiedChunk 列表
        k: RRF 常数，默认 60
        multi_hit_boost: 多 group 命中加成倍数，默认 1.09
        source_weight: RSF 权重 (0=纯 RRF, 1=纯 RSF)，默认 0.7

    Returns:
        按混合分数降序排列的 UnifiedChunk 列表（score 更新为混合分数，source_score 保存在 metadata）
    """
    if not chunks:
        return []

    rank_weight = 1.0 - source_weight
    max_rrf = 1.0 / (k + 1)  # rank=0 的 RRF 分数，用于归一化

    # ── Step 1: 全局 per-source_tool min-max 统计 ────────────────
    # 按 source_tool 收集所有 scores（不是 per rrf_group，避免小组噪声放大）
    source_scores: Dict[str, List[float]] = defaultdict(list)
    for c in chunks:
        source_scores[c.source_tool].append(c.score)
    source_min: Dict[str, float] = {}
    source_max: Dict[str, float] = {}
    for src, scores in source_scores.items():
        source_min[src] = min(scores)
        source_max[src] = max(scores)

    # ── Step 2: 按 rrf_group 分组排序（用于 RRF rank 计算）─────────
    groups: Dict[str, List[UnifiedChunk]] = defaultdict(list)
    for c in chunks:
        group_key = c.metadata.get("rrf_group", c.source_tool)
        groups[group_key].append(c)

    text_hash_to_scores: Dict[str, List[float]] = defaultdict(list)
    text_hash_to_chunk: Dict[str, UnifiedChunk] = {}
    text_hash_to_groups: Dict[str, set] = defaultdict(set)

    for group_key, group in groups.items():
        group.sort(key=lambda c: c.score, reverse=True)

        for rank, c in enumerate(group):
            h = _text_hash(c.text)
            text_hash_to_groups[h].add(group_key)

            # RSF: 全局 per-source min-max normalize（稳定，不受小组大小影响）
            s_min = source_min.get(c.source_tool, 0)
            s_max = source_max.get(c.source_tool, 1)
            s_range = s_max - s_min + 1e-9
            norm_src = (c.score - s_min) / s_range

            # RRF: rank-based score within group, normalized to [0,1]
            norm_rank = (1.0 / (k + rank + 1)) / max_rrf

            # Hybrid base score
            base = source_weight * norm_src + rank_weight * norm_rank
            text_hash_to_scores[h].append(base)

            # Keep chunk with highest original source score
            if h not in text_hash_to_chunk or c.score > text_hash_to_chunk[h].score:
                text_hash_to_chunk[h] = c

    # ── Step 3: Aggregate + multi-group boost ────────────────────
    result = []
    for h, scores in text_hash_to_scores.items():
        chunk = text_hash_to_chunk[h]
        group_keys = text_hash_to_groups[h]
        n_groups = len(group_keys)

        agg_score = max(scores)
        if n_groups >= 2:
            # 2 groups → ×1.09, 3+ groups → ×1.09² (capped)
            boost_exp = min(n_groups - 1, 2)
            agg_score *= multi_hit_boost ** boost_exp
        agg_score = min(1.0, agg_score)  # clamp to [0, 1]

        # Preserve original source score in metadata for debugging
        chunk.metadata["source_score"] = round(chunk.score, 4)
        chunk.metadata["hit_groups"] = n_groups
        chunk.score = round(agg_score, 6)
        result.append(chunk)

    result.sort(key=lambda c: c.score, reverse=True)
    return result


# ── 置信度分层 ───────────────────────────────────────────────────

def apply_confidence_tiers(
    chunks: List[UnifiedChunk],
    critical_threshold: float = 0.7,
    important_threshold: float = 0.3,
) -> List[UnifiedChunk]:
    """基于分数划分置信度层级。

    select_by_focus 输出的 chunk.score 为 hybrid_score (0~1):
    - critical ≥ 0.55 → 高质量（embedding + keyword 双重确认）
    - important ≥ 0.33 → 可用内容（通过 noise_threshold）
    - noise < 0.33 → 低相关噪声（理论上 select_by_focus 已过滤，此层为兜底）

    若无 focuses（chunk.score 来自 rrf_merge），使用旧阈值:
    - critical ≥ 0.7, important ≥ 0.3
    """
    for c in chunks:
        if c.score >= critical_threshold:
            c.confidence_tier = "critical"
        elif c.score >= important_threshold:
            c.confidence_tier = "important"
        else:
            c.confidence_tier = "noise"
    return chunks


# ── 共享停用词表（A1 + A2 共用）────────────────────────────────

# 英文：介词/冠词/连词/代词/助动词/常见功能词
_EN_STOP_WORDS = {
    # 冠词
    "a", "an", "the",
    # 介词
    "in", "of", "to", "for", "with", "by", "at", "on", "from", "about",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "under", "along", "across", "behind", "beyond", "among",
    "around", "via", "per", "vs", "as",
    # 连词
    "and", "or", "but", "nor", "so", "yet", "if", "than", "that",
    "which", "when", "while", "although", "because", "since", "unless",
    "until", "though", "whether", "either", "neither", "both",
    # 代词
    "it", "its", "this", "that", "these", "those",
    "i", "we", "you", "he", "she", "they",
    "me", "us", "him", "her", "them",
    "my", "our", "your", "his", "their",
    "what", "who", "whom", "whose", "where", "why", "how",
    # 助动词
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "must",
    "shall", "can", "need", "dare",
    # 其他高频功能词
    "not", "no", "all", "any", "each", "every", "some", "more", "most",
    "other", "such", "here", "there", "now", "then", "also", "just",
    "only", "very", "too", "one", "two", "new", "non",
}

# 中文：介词/连词/助词/代词/副词/量词/位置词/常用功能词
_ZH_STOP_WORDS = {
    # 结构助词
    "的", "地", "得",
    # 时态助词
    "了", "着", "过",
    # 语气助词
    "吗", "呢", "吧", "啊", "哦", "嗯", "呀",
    # 介词
    "在", "从", "到", "向", "对", "于", "被", "把", "让", "叫", "给",
    "按", "随", "由", "经", "为", "以", "用", "据", "与", "及",
    # 连词
    "和", "与", "及", "或", "而", "且", "并", "但", "虽", "然", "而且",
    "但是", "然而", "虽然", "如果", "以及", "还是", "所以", "因此",
    "因为", "不过", "另外", "此外", "同时", "并且",
    # 代词
    "我", "你", "他", "她", "它", "我们", "你们", "他们", "她们",
    "这", "那", "哪", "什么", "这些", "那些", "这个", "那个",
    # 副词
    "很", "非常", "比较", "更", "最", "也", "都", "还", "又", "已经",
    "将", "会", "可以", "应该", "需要", "能够", "不", "没有",
    "可能", "一般", "通常", "基本", "主要", "重要", "相关",
    # 量词/数词
    "一", "二", "三", "个", "些", "种", "项", "条", "篇",
    "一个", "一些", "各种", "多个",
    # 位置词/时间词
    "中", "上", "下", "前", "后", "内", "外", "里", "之间",
    "时候", "时间", "之后", "之前", "目前", "当前", "今年",
    # 常见业务文档功能词（无区分度）
    "通过", "包括", "关于", "进行", "实现", "根据", "基于",
    "对于", "针对", "方面", "情况", "方式", "方法", "过程",
    # 查询动词（在 focus 描述中频繁出现但对 chunk 命中无区分度）
    "获取", "查找", "了解", "分析", "调查", "检索", "查询",
    # 无区分度业务名词（真正的维度词需保留：流量、渠道、转化率等）
    "数据", "指标", "信息", "内容", "结果", "报告", "文档",
}

_STOP_WORDS = _EN_STOP_WORDS | _ZH_STOP_WORDS


def _extract_focus_words(focuses: List[str], min_len: int = 2) -> set:
    """从 focus 描述列表中提取有语义价值的关键词集合（去除停用词）。"""
    words: set = set()
    for focus in focuses:
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', focus.lower())
        words.update(w for w in tokens if len(w) >= min_len and w not in _STOP_WORDS)
    return words


def _extract_focus_words_expanded(focuses: List[str], min_len: int = 2) -> set:
    """从 focus 描述列表中提取关键词 + 泛化扩展词（中英同义词）。

    在 _extract_focus_words 基础上，通过 kw_generalization 加入等价词：
      "q1" → 第一季度/一季度/jan/feb/mar
      "eu" → 欧洲/europe
      "流量" → traffic/session/会话/uv
    """
    from .kw_generalization import expand_keywords
    base = _extract_focus_words(focuses, min_len)
    return expand_keywords(base)


# ── Focus-aware boost (A1) ────────────────────────────────────────

def focus_boost(
    chunks: List[UnifiedChunk],
    focuses: List[str],
    boost_weight: float = 0.3,
    preview_chars: int = 500,
) -> List[UnifiedChunk]:
    """A1: 对匹配 focus 关键词的 chunk 做 score boost，所有数据源均受益。

    重叠度越高，boost 越大（最大 score × (1 + boost_weight)）。
    chunk.metadata["focus_overlap"] 记录匹配词数，供调试。
    """
    if not focuses or not chunks:
        return chunks

    focus_words = _extract_focus_words(focuses)
    if not focus_words:
        return chunks

    for c in chunks:
        chunk_words = set(re.findall(r'[\w\u4e00-\u9fff]+', c.text[:preview_chars].lower()))
        overlap = len(focus_words & chunk_words)
        if overlap > 0:
            c.score *= (1.0 + (overlap / len(focus_words)) * boost_weight)
            c.metadata["focus_overlap"] = overlap

    chunks.sort(key=lambda c: c.score, reverse=True)
    return chunks


# ── Table Chunk Normalization ────────────────────────────────────

_TABLE_CELL_SPLIT = re.compile(r'\s*\|\s*')
# 百炼 PDF 解析后的表格通常是单行 pipe-separated，如：
# "| A | B | C | | A | val1 | val2 |"（多行被合并成一行，以 "| |" 或 "\t" 分隔行）
_INLINE_TABLE_RE = re.compile(r'(?:\|[^|]+){3,}\|')  # 行内至少3列的 pipe 模式


def _is_table_chunk(text: str) -> bool:
    """判断 chunk 是否主要为 pipe-separated 表格内容（含单行多行两种形式）。"""
    stripped = text.strip()
    # 多行表格
    multiline_rows = [l for l in stripped.split('\n') if l.strip().startswith('|') and l.strip().endswith('|')]
    if len(multiline_rows) >= 2:
        return True
    # 单行内联表格（百炼 PDF 解析常见）：行内含 3+ 个 pipe 分隔段
    pipe_count = stripped.count('|')
    return pipe_count >= 6 and '|' in stripped[:200]


def _normalize_table_to_markdown(text: str, max_rows: int = 15) -> str:
    """将 pipe-separated 碎片表格转换为标准 Markdown 表格格式。

    支持两种输入格式：
    A. 多行：每行以 | 开头和结尾
    B. 单行内联：百炼 PDF 解析合并行，以 "| |" 或相邻 "|" 分隔行
    """
    stripped = text.strip()
    non_table_prefix = []

    # 尝试多行格式
    lines = stripped.split('\n')
    table_rows_raw = []
    for line in lines:
        s = line.strip()
        if s.startswith('|') and s.endswith('|'):
            cells = [c.strip() for c in _TABLE_CELL_SPLIT.split(s)[1:-1]]
            if cells:
                table_rows_raw.append(cells)
        elif s and not table_rows_raw:
            non_table_prefix.append(s)

    # 如果多行解析失败，尝试单行分割（以 "| |" 或连续空单元格为行分隔符）
    if len(table_rows_raw) < 2:
        # 将 "| |" 替换为换行符再解析
        normalized_text = re.sub(r'\|\s*\|', '|\n|', stripped)
        table_rows_raw = []
        for line in normalized_text.split('\n'):
            s = line.strip()
            if s.startswith('|') and s.endswith('|'):
                cells = [c.strip() for c in _TABLE_CELL_SPLIT.split(s)[1:-1]]
                if any(c for c in cells):  # 跳过全空行
                    table_rows_raw.append(cells)

    if len(table_rows_raw) < 2:
        return text  # 无法解析，保持原文

    # 统一列数
    max_cols = max(len(r) for r in table_rows_raw)
    normalized_rows = []
    for r in table_rows_raw:
        while len(r) < max_cols:
            r.append('')
        normalized_rows.append(r)

    # 去重相邻完全相同的行（PDF 解析常见重复）
    deduped = [normalized_rows[0]]
    for row in normalized_rows[1:]:
        if row != deduped[-1]:
            deduped.append(row)

    deduped = deduped[:max_rows]

    # 构建 Markdown 表格
    header = deduped[0]
    separator = ['---'] * max_cols
    data_rows = deduped[1:]

    md_lines = []
    if non_table_prefix:
        md_lines.extend(non_table_prefix[:2])
        md_lines.append('')
    md_lines.append('| ' + ' | '.join(header) + ' |')
    md_lines.append('| ' + ' | '.join(separator) + ' |')
    for row in data_rows:
        md_lines.append('| ' + ' | '.join(row) + ' |')

    return '\n'.join(md_lines)


def normalize_table_chunks(chunks: List[UnifiedChunk]) -> List[UnifiedChunk]:
    """对识别为表格的 chunk 进行 Markdown 格式重组，提升 LLM 可读性。

    - 检测 pipe-separated 表格 chunk
    - 重组为标准 Markdown 表格（| header | ... | \n | --- | ... |）
    - 标记 metadata["table_normalized"] = True
    - 保留原文在 metadata["original_text"]（若未压缩）
    """
    for c in chunks:
        if not _is_table_chunk(c.text):
            continue
        original = c.text
        normalized = _normalize_table_to_markdown(c.text)
        if normalized != original:
            if "original_text" not in c.metadata:
                c.metadata["original_text"] = original
            c.metadata["table_normalized"] = True
            c.text = normalized
            logger.debug("normalize_table_chunks: restructured table in '%s'", c.source_name[:40])
    return chunks


# ── Contextual Compression (A2) ──────────────────────────────────

def compress_chunks(
    chunks: List[UnifiedChunk],
    focuses: List[str],
    window: int = 200,
    max_chars: int = 600,
) -> List[UnifiedChunk]:
    """A2: 对 top-K chunk 做关键词定位压缩，保留相关上下文，减少噪声。

    算法：
      1. 提取 focus 关键词在 chunk.text 中的所有命中位置
      2. 对每个命中位置保留前后 window 字符的上下文窗口
      3. 合并重叠区间，拼接为压缩文本
      4. 无命中时保留前 max_chars 字符（fail-open）

    chunk.metadata["original_text"] 保存原文，供上游随时访问。
    chunk.metadata["compressed"] = True 标记已压缩。
    """
    if not focuses or not chunks:
        return chunks

    focus_words = _extract_focus_words(focuses)
    if not focus_words:
        return chunks

    for c in chunks:
        text = c.text
        if len(text) <= max_chars:
            continue  # 短 chunk 不需要压缩

        # 已 normalize 的表格 chunk：跳过关键词窗口压缩
        # _normalize_table_to_markdown 已按 max_rows=15 控制长度，压缩会截断行结构
        if c.metadata.get("table_normalized"):
            continue

        # 找所有关键词的命中位置
        hit_positions = []
        for word in focus_words:
            start = 0
            lower_text = text.lower()
            while True:
                pos = lower_text.find(word, start)
                if pos == -1:
                    break
                hit_positions.append(pos)
                start = pos + 1

        if not hit_positions:
            # 无命中：保留前 max_chars 字符
            compressed = text[:max_chars]
        else:
            # 构建保留区间 [lo, hi)，合并重叠
            intervals = []
            for pos in hit_positions:
                lo = max(0, pos - window)
                hi = min(len(text), pos + window)
                intervals.append((lo, hi))
            intervals.sort()

            merged = [intervals[0]]
            for lo, hi in intervals[1:]:
                if lo <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
                else:
                    merged.append((lo, hi))

            # 截取并拼接，超过 3 段用 ... 连接
            parts = [text[lo:hi] for lo, hi in merged]
            compressed = " ... ".join(parts)

        # 保存原文，标记压缩
        c.metadata["original_text"] = text
        c.metadata["compressed"] = True
        c.text = compressed

    return chunks


# ── Per-Focus 选择 ───────────────────────────────────────────────

@dataclass
class FocusCoverage:
    """Per-focus coverage stats for analysis and iteration."""
    focus: str
    hits: int              # chunks selected for this focus
    target: int            # min_per_focus target
    satisfied: bool        # hits >= target
    avg_sim_emb: float     # average embedding similarity
    avg_sim_kw: float      # average keyword similarity
    top_sources: List[str] # top 3 source documents
    confidence_level: str = "high"  # "high" (emb≥0.5) | "medium" (0.4-0.5) | "low" (<0.4)

    def __post_init__(self):
        """Compute confidence_level based on avg_sim_emb."""
        if self.avg_sim_emb >= 0.5:
            self.confidence_level = "high"
        elif self.avg_sim_emb >= 0.4:
            self.confidence_level = "medium"
        else:
            self.confidence_level = "low"


def select_by_focus(
    chunks: List[UnifiedChunk],
    focuses: List[str],
    min_per_focus: int = 2,
    max_per_doc_per_focus: int = 4,
    top_k: int = 25,
    preview_chars: int = 500,
    precomputed_sim_matrix: "List[List[float]] | None" = None,
    emb_weight: float = 0.4,
    kw_weight: float = 0.15,
    noise_threshold: float = 0.33,
) -> Tuple[List[UnifiedChunk], List[FocusCoverage]]:
    """Weighted round-robin focus 选择：embedding 主导评分 + 按候选量加权轮转。

    评分公式 (embedding 语义主导):
      hybrid_score = 0.7 × sim_emb + 0.3 × sim_kw
      噪声过滤: hybrid_score < noise_threshold → 排除

    轮转策略:
      Phase 1 (保障轮): 均匀 1:1:1 轮转，直到每个 focus 达到 min_per_focus
      Phase 2 (加权轮): 按有效候选量归一化 ×3 取整的权重比例轮转，直到 top_k
        例: focus 有效候选 12:6:3 → 权重 2:1:1 → 每轮 f0 选 2, f1 选 1, f2 选 1

    chunk.score 更新为 max(hybrid_scores across assigned focuses)，供下游 AnswerAgent 使用。

    纯算法，零外部 IO 调用。
    """
    if not focuses or not chunks:
        return chunks, []

    sim_matrix = precomputed_sim_matrix
    has_emb = sim_matrix is not None
    mode = "emb+kw" if has_emb else "keyword"
    # keyword-only 模式下降低噪声阈值（纯 kw 分数偏低，0.33 太严格）
    if not has_emb:
        noise_threshold = min(noise_threshold, 0.1)
    logger.info("select_by_focus [%s]: %d focuses × %d chunks, top_k=%d, min_per_focus=%d, noise_thr=%.2f",
                mode, len(focuses), len(chunks), top_k, min_per_focus, noise_threshold)

    # ── 预计算：关键词 + chunk 词缓存 ──────────────────────────────
    focus_kw_map: Dict[int, set] = {}
    for fi, f in enumerate(focuses):
        kw = _extract_focus_words_expanded([f])
        if kw:
            focus_kw_map[fi] = kw
    chunk_word_cache: Dict[int, set] = {}
    for ci, c in enumerate(chunks):
        chunk_word_cache[ci] = set(re.findall(r'[\w\u4e00-\u9fff]+', c.text[:preview_chars].lower()))

    # ── 为每个 focus 生成候选排名列表 (embedding 主导混合评分) ─────
    # focus_ranked[fi] = [(hybrid_score, sim_emb, sim_kw, chunk_index, chunk), ...] 降序
    # 已过滤 hybrid_score < noise_threshold 的噪声候选
    focus_ranked: List[List[tuple]] = []
    for fi, focus in enumerate(focuses):
        scored = []
        for ci, c in enumerate(chunks):
            sim_emb = max(0.0, sim_matrix[fi][ci]) if has_emb else 0.0
            keywords = focus_kw_map.get(fi, set())
            if keywords:
                overlap = len(keywords & chunk_word_cache.get(ci, set()))
                sim_kw = min(1.0, overlap / max(1, len(keywords)))
            else:
                sim_kw = 0.0
            # 混合评分：embedding 语义主导 + 关键词辅助（权重由 query_type 控制）
            total_w = emb_weight + kw_weight or 1.0
            hybrid = (emb_weight * sim_emb + kw_weight * sim_kw) / total_w
            if hybrid >= noise_threshold:
                scored.append((hybrid, sim_emb, sim_kw, ci, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        focus_ranked.append(scored)

    # ── 计算加权轮转比例 ──────────────────────────────────────────
    # 有效候选数 → 归一化 → ×3 → 取整 (最小 1)
    valid_counts = [len(ranked) for ranked in focus_ranked]
    total_valid = sum(valid_counts) or 1
    raw_weights = [cnt / total_valid * 3 for cnt in valid_counts]
    # 取整：至少 1（保障每个 focus 有机会）
    focus_weights = [max(1, round(w)) for w in raw_weights]
    logger.info("select_by_focus weights: valid_counts=%s → weights=%s", valid_counts, focus_weights)

    # ── Round-robin 轮转选择 ──────────────────────────────────────
    selected_hashes: set = set()
    result_ordered: List[UnifiedChunk] = []
    chunk_best_hybrid: Dict[str, float] = {}   # text_hash → max hybrid_score
    # per-focus 状态
    focus_cursor: List[int] = [0] * len(focuses)
    focus_count: List[int] = [0] * len(focuses)
    focus_doc_counts: List[Dict[str, int]] = [{} for _ in focuses]
    focus_sim_embs: List[List[float]] = [[] for _ in focuses]
    focus_sim_kws: List[List[float]] = [[] for _ in focuses]
    focus_sources: List[List[str]] = [[] for _ in focuses]

    def _pick_next(fi: int) -> bool:
        """为 focus fi 选择下一个未选中的 chunk。"""
        ranked = focus_ranked[fi]
        while focus_cursor[fi] < len(ranked):
            hybrid, sim_emb, sim_kw, ci, c = ranked[focus_cursor[fi]]
            focus_cursor[fi] += 1
            h = _text_hash(c.text)
            doc_key = c.source_name or c.source_url or "unknown"
            if focus_doc_counts[fi].get(doc_key, 0) >= max_per_doc_per_focus:
                continue
            is_new = h not in selected_hashes
            if is_new:
                selected_hashes.add(h)
                result_ordered.append(c)
            # 记录 chunk 的最佳 hybrid_score（跨 focus 取 max）
            chunk_best_hybrid[h] = max(chunk_best_hybrid.get(h, 0.0), hybrid)
            # metadata 更新
            overlap_val = max(1, int(hybrid * 10))
            c.metadata["focus_overlap"] = max(c.metadata.get("focus_overlap", 0), overlap_val)
            c.metadata["sim_emb"] = round(max(c.metadata.get("sim_emb", 0.0), sim_emb), 3)
            c.metadata["sim_kw"] = round(max(c.metadata.get("sim_kw", 0.0), sim_kw), 3)
            # per-focus 统计
            focus_count[fi] += 1
            focus_doc_counts[fi][doc_key] = focus_doc_counts[fi].get(doc_key, 0) + 1
            focus_sim_embs[fi].append(sim_emb)
            focus_sim_kws[fi].append(sim_kw)
            if doc_key not in focus_sources[fi]:
                focus_sources[fi].append(doc_key)
            return True
        return False

    # Phase 1: 保障轮 — 均匀 1:1:1 轮转，每个 focus 达到 min_per_focus
    for _round in range(min_per_focus):
        for fi in range(len(focuses)):
            if focus_count[fi] > _round:
                continue
            if len(selected_hashes) >= top_k:
                break
            _pick_next(fi)

    # Phase 2: 加权轮转 — 按权重比例分配
    max_iterations = 50
    for _ in range(max_iterations):
        if len(selected_hashes) >= top_k:
            break
        any_picked = False
        for fi in range(len(focuses)):
            if len(selected_hashes) >= top_k:
                break
            # 该 focus 在本轮的配额 = focus_weights[fi]
            for _ in range(focus_weights[fi]):
                if len(selected_hashes) >= top_k:
                    break
                if _pick_next(fi):
                    any_picked = True
        if not any_picked:
            break

    # ── 更新 chunk.score 为 hybrid_score（供下游使用）─────────────
    for c in result_ordered:
        h = _text_hash(c.text)
        best = chunk_best_hybrid.get(h, 0.0)
        if best > 0:
            c.score = round(best, 4)

    # ── 构建 FocusCoverage ──────────────────────────────────────
    focus_coverages: List[FocusCoverage] = []
    for fi, focus in enumerate(focuses):
        focus_coverages.append(FocusCoverage(
            focus=focus[:80],
            hits=focus_count[fi],
            target=min_per_focus,
            satisfied=focus_count[fi] >= min_per_focus,
            avg_sim_emb=round(sum(focus_sim_embs[fi]) / max(1, len(focus_sim_embs[fi])), 3),
            avg_sim_kw=round(sum(focus_sim_kws[fi]) / max(1, len(focus_sim_kws[fi])), 3),
            top_sources=focus_sources[fi][:3],
        ))

    satisfied_count = sum(1 for fc in focus_coverages if fc.satisfied)
    per_focus_dist = ", ".join(f"f{fi}={focus_count[fi]}" for fi in range(len(focuses)))
    logger.info(
        "select_by_focus [%s weighted-rr]: %d focuses → %d unique chunks "
        "(satisfied: %d/%d, dist: %s, weights: %s)",
        mode, len(focuses), len(selected_hashes), satisfied_count, len(focuses),
        per_focus_dist, focus_weights,
    )

    return result_ordered, focus_coverages


# ── 主入口 ───────────────────────────────────────────────────────

def rerank_and_filter(
    chunks: List[UnifiedChunk],
    top_k: int = 10,
    focuses: List[str] | None = None,
    min_per_focus: int = 2,
    max_per_doc_per_focus: int = 4,
    precomputed_sim_matrix: "List[List[float]] | None" = None,
    emb_weight: float = 0.4,
    kw_weight: float = 0.15,
    query_type: str = "mixed",
    source_weight: float = 0.7,
) -> Tuple[List[UnifiedChunk], List[FocusCoverage]]:
    """完整 rerank pipeline：dedup → deny-list → RSF+RRF → table bonus → per-focus select → tier → top_k → compress。

    纯算法层，零外部 IO。Embedding 由上游 EmbeddingCache 预计算后传入 sim_matrix。

    Args:
        focuses: 所有 focus 描述（flat list）。None 则跳过 focus 相关逻辑。
        min_per_focus: 每个 focus select_by_focus 选出的代表 chunk 数。
        top_k: 最终输出的硬上限。
        precomputed_sim_matrix: N×M 语义相似度矩阵（EmbeddingCache 提供）。None 则用关键词。
        query_type: "data" 时增大 kw_weight（精确词命中更重要），"concept" 时增大 emb_weight。
        source_weight: RSF 权重 (0=纯 RRF, 1=纯 RSF)，默认 0.7。

    Returns:
        Tuple[chunks, focus_coverages]: 最终 chunks + 每个 focus 的覆盖率统计（用于迭代分析）
    """
    # focus_type-aware 权重调整
    # RSF 侧已用扩展关键词评分，这里调的是 select_by_focus 内语义 vs 关键词比例
    if query_type == "data":
        emb_weight = max(emb_weight, 0.35)   # data: embedding 仍有价值（表格语义）
        kw_weight = max(kw_weight, 0.25)     # data: 精确关键词命中更重要
    elif query_type == "concept":
        emb_weight = max(emb_weight, 0.65)   # concept: 语义理解主导
        kw_weight = min(kw_weight, 0.05)     # concept: 关键词弱化
    n_input = len(chunks)
    chunks = dedup_chunks(chunks)
    n_dedup = len(chunks)
    chunks = deny_list_filter(chunks)
    n_deny = len(chunks)

    chunks = rrf_merge(chunks, source_weight=source_weight)

    # Table bonus: data/mixed 查询时 table_md chunks 得分 ×1.3
    # 表格天然包含精确数据，但关键词覆盖度低于叙述性文本，需要补偿
    if query_type in ("data", "mixed"):
        from feishu_read_tool.pipeline_models import ChunkContentType
        for c in chunks:
            ct = c.metadata.get("content_type", "")
            if ct in ("table_md", "TABLE_MD", ChunkContentType.TABLE_MD.value if hasattr(ChunkContentType, "TABLE_MD") else ""):
                c.score *= 1.3
                c.metadata["table_bonus"] = True
        chunks.sort(key=lambda c: c.score, reverse=True)

    logger.debug(
        "rerank funnel: input=%d → dedup=%d → deny_filter=%d → rrf=%d",
        n_input, n_dedup, n_deny, len(chunks),
    )

    focus_coverages: List[FocusCoverage] = []
    if focuses:
        # Per-focus 独立选择（混合 embedding + keyword 评分）
        chunks, focus_coverages = select_by_focus(
            chunks, focuses, min_per_focus=min_per_focus,
            max_per_doc_per_focus=max_per_doc_per_focus, top_k=top_k,
            precomputed_sim_matrix=precomputed_sim_matrix,
            emb_weight=emb_weight, kw_weight=kw_weight,
        )

        # 零命中兜底
        if not chunks:
            logger.info("rerank: select_by_focus returned empty — no relevant chunks")
            return [], focus_coverages

        overlaps = [c.metadata.get("focus_overlap", 0) for c in chunks]
        if not any(x > 0 for x in overlaps):
            logger.info(
                "rerank: focus zero-match on all %d chunks — marking all as noise", len(chunks)
            )
            return [], focus_coverages

        logger.info(
            "rerank focus_overlap: zero=%d  low(1-3)=%d  high(4+)=%d",
            sum(1 for x in overlaps if x == 0),
            sum(1 for x in overlaps if 1 <= x <= 3),
            sum(1 for x in overlaps if x >= 4),
        )

        # Log detailed focus coverage for iteration analysis
        unsatisfied = [fc for fc in focus_coverages if not fc.satisfied]
        if unsatisfied:
            logger.warning(
                "focus_coverage: %d/%d unsatisfied — %s",
                len(unsatisfied), len(focus_coverages),
                [(fc.focus[:30], fc.hits, fc.target) for fc in unsatisfied[:3]]
            )

    # 置信度分层阈值：select_by_focus 输出的 score 是 hybrid_score (0~1)
    if focuses:
        chunks = apply_confidence_tiers(chunks, critical_threshold=0.55, important_threshold=0.33)
    else:
        chunks = apply_confidence_tiers(chunks)
    tier_dist: dict = {}
    for c in chunks:
        tier_dist[c.confidence_tier] = tier_dist.get(c.confidence_tier, 0) + 1
    logger.info("rerank tier_dist: %s", tier_dist)

    chunks = [c for c in chunks if c.confidence_tier != "noise"]

    chunks = chunks[:top_k]  # 硬上限：无论 focuses 多少，最终不超过 top_k
    logger.info("rerank output: %d chunks (top_k=%d)", len(chunks), top_k)

    # 表格 chunk 格式重组（pipe→markdown，提升 LLM 可读性）
    chunks = normalize_table_chunks(chunks)

    if focuses:
        chunks = compress_chunks(chunks, focuses)
    return chunks, focus_coverages
