"""Source-agent 层 query 扩展工具:从 sub_query + focus_text 生成多个变体。

用于 dual-path 召回的 Path B:Path A 用原 sub_query,Path B 用这里生成的变体并行
搜索与 RRF 合并后的兼容扩展工具。

设计原则:
  - 不调用任何外部 API(纯字符串变换 + 已有 kw_generalization 映射表)
  - 输入是 planner-produced 的 sub_query + focus_text (user 原始意图),不依赖 planner 改造
  - 输出是 1-3 个 variant 字符串,与原 sub_query 不重复
"""
from __future__ import annotations

import re
from typing import List, Set

from tools.core.kw_generalization import expand_keywords


# ── 锚点抽取:从 focus_text 抽 user 真实说的高信息量名词 ──────────────────────
_USER_ANCHOR_PATTERNS = [
    re.compile(r"\d+\s?(?:Pro|Plus|Ultra|Max|Mini|Lite|SE)\b"),    # "11 Pro", "8 Plus"
    re.compile(r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*"),         # "X Ultra", "Paid Media"
    re.compile(r"[A-Z]{2,}[0-9]*"),                                # "UGC", "EDM", "ROAS"
    re.compile(r"[A-Z][0-9]+(?:\s+[A-Za-z]+)*"),                    # "Z80 Ultra", "Z70"
    re.compile(r"\d{4}-\d{1,2}-\d{1,2}"),                          # "2026-01-15"
    re.compile(r"\d{1,2}月\d{1,2}日"),                              # "1月15日"
    re.compile(r"[一-鿿]{2,4}(?:确定|准备|规划|跟进|追踪|审核|配置|管理|计划|总结)"),  # "活动规则确定"
    re.compile(r"[\"「『《]([^\"」』》]{2,15})[\"」』》]"),            # 引号包裹的精确项
    re.compile(r"Q[1-4]"),                                          # "Q4"
]


def _extract_user_anchors(focus_text: str, max_anchors: int = 3) -> List[str]:
    """从 focus_text 抽取高信息量的 user 原词。"""
    if not focus_text:
        return []
    anchors: List[str] = []
    for pat in _USER_ANCHOR_PATTERNS:
        for m in pat.findall(focus_text):
            m_str = m if isinstance(m, str) else (m[0] if m else "")
            m_str = m_str.strip()
            if m_str and m_str.lower() not in {a.lower() for a in anchors}:
                anchors.append(m_str)
                if len(anchors) >= max_anchors:
                    return anchors
    return anchors


# ── 双语翻译:把中文 token 映射到等价英文(反之亦然) ────────────────────────
# 优先选择另一种语言的同义词,实现中英文 query 互查
_CN_CHAR = re.compile(r"[一-鿿]")
_EN_CHAR = re.compile(r"[A-Za-z]")


def _is_chinese(token: str) -> bool:
    return bool(_CN_CHAR.search(token))


def _is_english(token: str) -> bool:
    return bool(_EN_CHAR.search(token)) and not _is_chinese(token)


def _pick_cross_language_synonym(token: str) -> str:
    """对 token 调用 kw_generalization,优先返回另一语言的同义词。

    例: '会议' → 'meeting',  'meeting' → '会议',  'q4' → '第四季度'

    选词偏好:
      - 中→英: 偏长(英文长词通常是名词,更像 doc title 用词,如 "meeting" > "meet")
      - 英→中: 偏短(中文短词更常用)
    """
    syns = expand_keywords({token.lower()})
    syns.discard(token.lower())
    if not syns:
        return ""
    want_english = _is_chinese(token)
    candidates = [s for s in syns if _is_english(s) == want_english]
    if not candidates:
        return ""
    if want_english:
        return max(candidates, key=len)   # 中→英 偏长(meeting > meet)
    return min(candidates, key=len)       # 英→中 偏短(会议 < 会议室)


def _build_bilingual_variant(sub_query: str) -> str:
    """sub_query 整句翻译到另一语言（token 级）。无可翻 token 时返回空串。"""
    tokens = sub_query.strip().split()
    if not tokens:
        return ""
    translated = []
    any_translated = False
    for tok in tokens:
        alt = _pick_cross_language_synonym(tok)
        if alt:
            translated.append(alt)
            any_translated = True
        else:
            translated.append(tok)
    if not any_translated:
        return ""
    return " ".join(translated)


# ── 短而精的 anchored 变体构造(目标 5-15 字,适配 feishu search 严格限制) ─────
def _build_anchored_variant(sub_query: str, focus_text: str) -> str:
    """生成 1 条短精 Path B 变体:brand_token(若有) + 1 个新 anchor,目标 5-15 字。

    去重策略:
      - 丢弃 token 集已被 sub_query 完全覆盖的 anchor(避免重复)
      - 子串去重保留更长(如同时有 '11 Pro' 与 'Pro' 时丢 'Pro')
      - 用 brand + top1 anchor 试; 不行用 top1+top2; 再不行单 anchor
    """
    raw = _extract_user_anchors(focus_text or "", max_anchors=4)
    if not raw:
        return ""
    sq_toks = set(re.findall(r"[\w一-鿿]+", sub_query.lower()))
    candidates = [
        a for a in raw
        if not set(re.findall(r"[\w一-鿿]+", a.lower())).issubset(sq_toks)
    ]
    deduped: List[str] = []
    for a in candidates:
        if any(a != b and a.lower() in b.lower() for b in candidates):
            continue
        deduped.append(a)
    if not deduped:
        return ""
    bm = re.search(r"[A-Za-z][A-Za-z0-9_-]+", sub_query)
    brand = bm.group(0) if bm else ""
    tries: List[str] = []
    if brand:
        tries.append(f"{brand} {deduped[0]}")
    if len(deduped) >= 2:
        tries.append(f"{deduped[0]} {deduped[1]}")
    tries.append(deduped[0])
    for v in tries:
        v = v.strip()
        if 5 <= len(v) <= 15:
            return v
    return ""


# ── 主入口:综合 anchors + 双语 + brand-only,生成 Path B variants ─────────────
def build_query_variants(
    sub_query: str,
    focus_text: str,
    max_variants: int = 2,
) -> List[str]:
    """Path B 变体生成。返回最多 2 条"与原 sub_query 不同"的短精搜索 query。

    设计意图(配合 Path A 宽召回):
      - Path A = sub_query 本身(planner 写的泛化短词,兜底"项目/月会"类模糊标题文档)
      - Path B = 短精变体(brand+anchor,目标 5-15 字,补"精确标题"文档)
      - RRF 合并 → 模糊与精确两类目标都能召回
    """
    variants: List[str] = []
    seen = {sub_query.strip().lower()}

    def _push(v: str) -> None:
        v = v.strip()
        if not v or v.lower() in seen:
            return
        variants.append(v)
        seen.add(v.lower())

    # V1: anchored 短变体 (5-15 字)
    anchored = _build_anchored_variant(sub_query, focus_text)
    if anchored:
        _push(anchored)

    # V2: 无 anchored 变体时,bilingual 兜底(语言桥接)
    if not variants:
        bilingual = _build_bilingual_variant(sub_query)
        if bilingual:
            _push(bilingual)

    # V3: brand-only 兜底
    if len(variants) < max_variants:
        brand_tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]+", sub_query)
        if brand_tokens:
            _push(" ".join(brand_tokens[:2]))

    return variants[:max_variants]
