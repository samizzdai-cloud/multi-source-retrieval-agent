"""Planner Runner — v2.2 Global Planner 与各 SourceAgent 之间的校验/转译层。

职责：
  1. 把 ``GlobalPlan``（planner 直出，瘦身）翻译成各源运行时所需的
     ``agents.source_agent_base.SubQuery``（含 sub_query_id / target_focus /
     google_enable_fulltext / google_order_by）。
  2. 跑每条 ``PlannedSubQuery`` 的源专属 Validator —— 能修就修，修不了进
     ``replan_needed``，交给 retrieve_agent 走该源的老 plan_only_LLM 局部修复。
  3. 输出三元组：
     - ``focuses``: runtime ``Focus`` 列表（dispatch / FocusBoard 都依赖）
     - ``pre_planned_by_source``: dict[src, List[SubQuery]] —— 校验通过的预规划
     - ``replan_needed``: dict[src, List[Focus]] —— 该源该 focus 全部 sub_query
       都被 reject，需走老 plan_only_LLM 修复（局部 replan）

设计约束（与重构计划对齐）：
  - Validator 永不抛异常：能修复则修复，最差返 None 表示丢弃单条 sub_query
  - "整个 focus 在该源被全部 reject" 才进 ``replan_needed``，不是"任一条被
    reject"——避免无谓的 LLM 调用
  - Bailian indexes：planner 给的是 symbolic id 列表（如 ["nubia"]）；
    runner 校验"是否在 allowed 集合"，非法 → 丢；全空 → fallback 到全 allowed
  - Bailian tags：fix_tag 取最相近枚举 + enforce_tag_caps（L2≤1 + 时效≤1）
  - Google: 关键词宽度截到 ≤4；query_type=="personal_recent" 自动写
    google_enable_fulltext=False + google_order_by="viewedByMeTime desc"
  - Feishu: query_type 枚举校验；personal_recent 自动写 author_filter="me"
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

from agents.source_agent_base import Focus, SubQuery
from tools.core.models import BailianIndexCatalog, GlobalPlan, PlannedFocus, PlannedSubQuery
from tools.core.source_query_config import enforce_tag_caps, fix_tag

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Validator 接口
# ─────────────────────────────────────────────────────────────────────
class SourceSubQueryValidator(Protocol):
    """每个源一个 Validator；normalize 返 None 即视为该条 sub_query 被丢弃。"""

    source: str

    def normalize(
        self,
        psq: PlannedSubQuery,
        focus_id: str,
        sub_query_id: str,
    ) -> Optional[SubQuery]: ...


# ─────────────────────────────────────────────────────────────────────
# Bailian Validator
# ─────────────────────────────────────────────────────────────────────
class BailianValidator:
    source = "bailian"

    def __init__(self, catalog: Optional[BailianIndexCatalog] = None):
        self._catalog = catalog or BailianIndexCatalog()

    def normalize(
        self,
        psq: PlannedSubQuery,
        focus_id: str,
        sub_query_id: str,
    ) -> Optional[SubQuery]:
        query = (psq.query or "").strip()
        if not query:
            logger.info("[validator.bailian] drop: empty query (focus=%s)", focus_id)
            return None

        # tags 修复：fix_tag 取最相近 + enforce_tag_caps（L2≤1 + 时效≤1）
        tags_fixed = enforce_tag_caps(list(psq.tags or []))

        # indexes：symbolic 校验。非 allowed 的丢；全丢则 fallback 全 allowed
        allowed = list(self._catalog.allowed or [])
        if allowed:
            allowed_lower = {x.lower(): x for x in allowed}
            indexes_fixed: List[str] = []
            for sym in psq.indexes or []:
                if not isinstance(sym, str):
                    continue
                key = sym.strip().lower()
                if key in allowed_lower:
                    indexes_fixed.append(allowed_lower[key])
            if not indexes_fixed:
                logger.info(
                    "[validator.bailian] focus=%s sq=%s: indexes %s 全部非法 → fallback 全 allowed",
                    focus_id, sub_query_id, list(psq.indexes or []),
                )
                indexes_fixed = list(allowed)
        else:
            # 未限制时直接放行（execute 层会查全部 configured indexes）
            indexes_fixed = [x for x in (psq.indexes or []) if isinstance(x, str) and x.strip()]

        return SubQuery(
            query=query,
            target_focus=focus_id,
            sub_query_id=sub_query_id,
            tags=tags_fixed,
            indexes=indexes_fixed,
            time_filter=None,            # bailian 时效走 tags
            query_type=None,
            google_enable_fulltext=None,
            google_order_by=None,
            author_filter=None,
        )


# ─────────────────────────────────────────────────────────────────────
# Feishu Validator
# ─────────────────────────────────────────────────────────────────────
_FEISHU_QUERY_TYPES = {"structured", "concept", "personal_recent"}
_FEISHU_QUERY_TYPE_ALIASES = {
    "structure": "structured",
    "data": "structured",
    "fact": "structured",
    "concepts": "concept",
    "explain": "concept",
    "personal": "personal_recent",
    "recent": "personal_recent",
}
_FEISHU_TIME_FILTER_RE = re.compile(r"^(>?\d{4}-\d{2}-\d{2}|last_\d+_days?)$")


class FeishuValidator:
    source = "feishu"

    def normalize(
        self,
        psq: PlannedSubQuery,
        focus_id: str,
        sub_query_id: str,
    ) -> Optional[SubQuery]:
        query = (psq.query or "").strip()
        if not query:
            logger.info("[validator.feishu] drop: empty query (focus=%s)", focus_id)
            return None

        # query_type 枚举校验 + fuzzy
        qt_raw = (psq.query_type or "").strip().lower()
        if qt_raw in _FEISHU_QUERY_TYPES:
            query_type = qt_raw
        elif qt_raw in _FEISHU_QUERY_TYPE_ALIASES:
            query_type = _FEISHU_QUERY_TYPE_ALIASES[qt_raw]
        elif not qt_raw:
            # 未给 → fallback 到 structured（与现 SourceAgent default 一致）
            query_type = "structured"
        else:
            logger.info(
                "[validator.feishu] focus=%s sq=%s: 非法 query_type=%r → fallback structured",
                focus_id, sub_query_id, qt_raw,
            )
            query_type = "structured"

        # time_filter 格式校验：保留合法格式或 None
        time_filter = psq.time_filter
        if time_filter:
            tf = str(time_filter).strip()
            if not _FEISHU_TIME_FILTER_RE.match(tf):
                logger.info(
                    "[validator.feishu] focus=%s sq=%s: 非法 time_filter=%r → 置空",
                    focus_id, sub_query_id, tf,
                )
                time_filter = None
            else:
                time_filter = tf

        # personal_recent 自动注入 author_filter="me"
        author_filter = psq.author_filter
        if query_type == "personal_recent" and not author_filter:
            author_filter = "me"

        # feishu tags/indexes 始终为空
        return SubQuery(
            query=query,
            target_focus=focus_id,
            sub_query_id=sub_query_id,
            tags=[],
            indexes=[],
            time_filter=time_filter,
            query_type=query_type,
            google_enable_fulltext=None,
            google_order_by=None,
            author_filter=author_filter,
        )


# ─────────────────────────────────────────────────────────────────────
# Google Validator
# ─────────────────────────────────────────────────────────────────────
_GOOGLE_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[一-鿿]+")
_GOOGLE_QUERY_TYPES = {"structured", "concept", "personal_recent"}
_GOOGLE_KEYWORD_MAX = 4


def _truncate_google_keywords(query: str, max_kw: int = _GOOGLE_KEYWORD_MAX) -> str:
    """Bound query width while retaining high-information anchors first.

    Acronyms, product-like tokens, numbers and dates survive before generic
    words.  Stable original order is restored after selection so the compiler
    does not turn the query into an arbitrary bag of terms.
    """
    toks = _GOOGLE_TOKEN_RE.findall(query or "")
    if not toks:
        return query.strip()
    if len(toks) <= max_kw:
        return query.strip()
    def priority(item: tuple[int, str]) -> tuple[int, int]:
        index, token = item
        is_anchor = bool(
            re.search(r"\d", token)
            or (re.search(r"[A-Za-z]", token) and token.upper() == token and len(token) > 1)
            or re.search(r"[-./_]", token)
        )
        return (0 if is_anchor else 1, index)

    selected = sorted(sorted(enumerate(toks), key=priority)[:max_kw])
    return " ".join(token for _, token in selected)


class GoogleValidator:
    source = "google"

    def normalize(
        self,
        psq: PlannedSubQuery,
        focus_id: str,
        sub_query_id: str,
    ) -> Optional[SubQuery]:
        raw_query = (psq.query or "").strip()
        if not raw_query:
            logger.info("[validator.google] drop: empty query (focus=%s)", focus_id)
            return None

        # 关键词宽度 ≤4，否则截断
        query = _truncate_google_keywords(raw_query, _GOOGLE_KEYWORD_MAX)
        if not query:
            logger.info(
                "[validator.google] drop: query truncated to empty (focus=%s raw=%r)",
                focus_id, raw_query,
            )
            return None
        if query != raw_query:
            logger.info(
                "[validator.google] focus=%s sq=%s: 关键词截断 %r -> %r",
                focus_id, sub_query_id, raw_query, query,
            )

        # query_type 枚举校验
        qt_raw = (psq.query_type or "").strip().lower()
        if qt_raw in _GOOGLE_QUERY_TYPES:
            query_type = qt_raw
        else:
            query_type = "structured" if not qt_raw else None
            if qt_raw and query_type is None:
                query_type = "structured"
                logger.info(
                    "[validator.google] focus=%s sq=%s: 非法 query_type=%r → fallback structured",
                    focus_id, sub_query_id, qt_raw,
                )

        # 由 query_type 翻译出 google_enable_fulltext / google_order_by
        # （这两个字段从 PlannedSubQuery 删除，runner 在此推断）
        if query_type == "personal_recent":
            google_enable_fulltext: Optional[bool] = False
            google_order_by: Optional[str] = "viewedByMeTime desc"
        else:
            # 留 None → execute 层走 cfg 默认（enable_fulltext_search=True）
            google_enable_fulltext = None
            google_order_by = None

        # author_filter：personal_recent 自动 "me"
        author_filter = psq.author_filter
        if query_type == "personal_recent" and not author_filter:
            author_filter = "me"

        # time_filter：保留 planner 给的字符串（execute 层有自己的解析）
        time_filter = psq.time_filter or None

        return SubQuery(
            query=query,
            target_focus=focus_id,
            sub_query_id=sub_query_id,
            tags=[],
            indexes=[],
            time_filter=time_filter,
            query_type=query_type,
            google_enable_fulltext=google_enable_fulltext,
            google_order_by=google_order_by,
            author_filter=author_filter,
        )


# ─────────────────────────────────────────────────────────────────────
# Notion Validator（passthrough）
# ─────────────────────────────────────────────────────────────────────
class NotionValidator:
    source = "notion"

    def normalize(
        self,
        psq: PlannedSubQuery,
        focus_id: str,
        sub_query_id: str,
    ) -> Optional[SubQuery]:
        query = (psq.query or "").strip()
        if not query:
            logger.info("[validator.notion] drop: empty query (focus=%s)", focus_id)
            return None
        return SubQuery(
            query=query,
            target_focus=focus_id,
            sub_query_id=sub_query_id,
            tags=[],
            indexes=[],
            time_filter=None,
            query_type=None,
            google_enable_fulltext=None,
            google_order_by=None,
            author_filter=None,
        )


# ─────────────────────────────────────────────────────────────────────
# 装配 / 归一化主流程
# ─────────────────────────────────────────────────────────────────────
@dataclass
class NormalizedPlan:
    """planner_runner.normalize_plan 的产物。

    - ``focuses``: runtime Focus 列表（focus.targets 由本 focus 下所有
      sub_query.target_source 去重得出，dispatcher 仍按 targets 路由）
    - ``pre_planned_by_source``: 已通过 validator 的 SubQuery，按源分桶
    - ``replan_needed``: 该源该 focus 全部 sub_query 都被 reject 的 focus，
      retrieve_agent 会把它们送回该源的 plan_only_LLM 做局部修复
    - ``rejected_count``: 合计被丢弃的 sub_query 条数（用于监控）
    """
    focuses: List[Focus] = field(default_factory=list)
    pre_planned_by_source: Dict[str, List[SubQuery]] = field(default_factory=dict)
    replan_needed: Dict[str, List[Focus]] = field(default_factory=dict)
    rejected_count: int = 0
    validation_warnings: List[str] = field(default_factory=list)
    profile_resolution: Any = None


_VALIDATOR_FACTORIES: Dict[str, "Callable[..., SourceSubQueryValidator]"] = {
    "bailian": lambda catalog=None: BailianValidator(catalog=catalog),
    "feishu":  lambda catalog=None: FeishuValidator(),
    "google":  lambda catalog=None: GoogleValidator(),
    "notion":  lambda catalog=None: NotionValidator(),
}


def build_default_validators(
    bailian_catalog: Optional[BailianIndexCatalog] = None,
    enabled_sources: Optional[List[str]] = None,
) -> Dict[str, SourceSubQueryValidator]:
    """按需装配 Validator：只造 ``enabled_sources`` 里的，没启用的源不浪费对象。

    新增源时在 ``_VALIDATOR_FACTORIES`` 注册一行即可；``enabled_sources=None``
    时退化为造全部（向后兼容老调用）。
    """
    sources = enabled_sources if enabled_sources is not None else list(_VALIDATOR_FACTORIES.keys())
    out: Dict[str, SourceSubQueryValidator] = {}
    for src in sources:
        factory = _VALIDATOR_FACTORIES.get(src)
        if factory is None:
            logger.info("[planner_runner] no validator registered for source=%r, skip", src)
            continue
        out[src] = factory(bailian_catalog)
    return out


def normalize_plan(
    plan: GlobalPlan,
    validators: Dict[str, SourceSubQueryValidator],
    enabled_sources: List[str],
) -> NormalizedPlan:
    """把 ``GlobalPlan`` 翻译为可投递给各 SourceAgent 的 runtime 形态。

    Args:
        plan: planner 直出
        validators: 源名 → Validator，未启用源不会被处理
        enabled_sources: 仅在此列表内的源会被翻译（planner 偶尔会越权产
            未启用源的 sub_query；runner 一并丢弃 + 记日志）

    Returns:
        NormalizedPlan（见类 docstring）
    """
    out = NormalizedPlan(
        pre_planned_by_source={s: [] for s in enabled_sources},
        replan_needed={s: [] for s in enabled_sources},
        validation_warnings=list(getattr(plan, "validation_warnings", []) or []),
    )
    enabled_set = set(enabled_sources)
    sq_seq = 0

    for pf in plan.focuses:
        focus_id = (pf.focus_id or "").strip() or f"focus_{len(out.focuses)}"
        focus_text = (pf.focus_content or "").strip()
        if not focus_text:
            logger.info("[planner_runner] skip focus with empty content (id=%s)", focus_id)
            continue

        # 1) 按 target_source 分桶
        bucket: Dict[str, List[PlannedSubQuery]] = {s: [] for s in enabled_sources}
        for psq in pf.sub_queries or []:
            src = (psq.target_source or "").strip().lower()
            if src not in enabled_set:
                logger.info(
                    "[planner_runner] focus=%s: drop sub_query for unknown/disabled source=%r",
                    focus_id, src,
                )
                out.rejected_count += 1
                continue
            bucket[src].append(psq)

        # 2) 每桶过 validator
        focus_target_sources: List[str] = []
        for src, psqs in bucket.items():
            if not psqs:
                continue
            validator = validators.get(src)
            if validator is None:
                logger.warning("[planner_runner] no validator for source=%s, drop %d sq", src, len(psqs))
                out.rejected_count += len(psqs)
                continue

            normalized_list: List[SubQuery] = []
            for psq in psqs:
                sq_seq += 1
                sub_query_id = f"{src}:{focus_id}:sq{sq_seq}"
                sq = validator.normalize(psq, focus_id=focus_id, sub_query_id=sub_query_id)
                if sq is None:
                    out.rejected_count += 1
                    continue
                # Program-owned post-parse metadata. It never expands the
                # Planner JSON contract or becomes a normal primary query.
                for variant in psq.fallback_variants:
                    if variant and variant not in sq.variants:
                        sq.variants.append(variant)
                sq.variant_origins.update(psq.fallback_variant_origins)
                normalized_list.append(sq)

            if normalized_list:
                out.pre_planned_by_source[src].extend(normalized_list)
                focus_target_sources.append(src)
            else:
                # 该 focus 在该源全部被 reject → 标记 replan（局部回老路径）
                out.replan_needed[src].append(Focus(focus_id=focus_id, text=focus_text, targets=[src]))
                # 仍把 src 计入 focus.targets，下游知道这个 focus 该走该源
                focus_target_sources.append(src)
                logger.info(
                    "[planner_runner] focus=%s source=%s: 全部 sub_query 被 reject → replan",
                    focus_id, src,
                )

        # 3) 该 focus 没产任何 target_source？兜底为全部 enabled（保留与老路径行为一致）
        if not focus_target_sources:
            focus_target_sources = list(enabled_sources)
            logger.info(
                "[planner_runner] focus=%s no target_source from sub_queries → broadcast %s",
                focus_id, enabled_sources,
            )
            # 全广播 → 全部源都需要 replan（plan_only_LLM 兜底）
            for src in enabled_sources:
                out.replan_needed[src].append(Focus(focus_id=focus_id, text=focus_text, targets=[src]))

        out.focuses.append(
            Focus(focus_id=focus_id, text=focus_text, targets=sorted(set(focus_target_sources)))
        )

    return out
