"""SourceAgent 基类——通用 loop 骨架（plan → execute → eval）。

设计要点（spec_plan.md v1 §2 / §11 Phase 1）：
- 不是 LlmAgent 子类，而是一个"编排器"，内部调用 3 个 sub_agents（pydantic output_schema）
  拿到结构化 JSON，再结合外部 execute 函数真正跑检索
- chunk_pool: 本源 loop 间累积的 id → ChunkEntry 字典；同 id 冲突走 ``ChunkEntry.merge``
- loop 边界由 ``needs_eval`` / ``enforce_loop_bounds`` 硬约束（见 ``tools/llm_contracts``）
- 单 loop 路径（min=max=1）：plan_only → execute → eval_only，不调 plan_eval
- 多 loop 路径（max>1）：plan_only → execute → plan_eval(→continue/replan → execute)*
                            → 最后一轮 eval_only 收尾
- 子类只需提供 ``execute_sub_query(sub_query, focus_id) -> list[ChunkEntry]`` 适配器

已实现单 loop（min=max=1）和多 loop（max>1）完整路径。
LLM 调用走 ``google.adk.Runner`` 单例 + ``InMemorySessionService``（零持久化）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional

from google.adk.agents.llm_agent import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from tools.core.llm_contracts import (
    enforce_loop_bounds,
    needs_eval,
    tiers_list_to_map,
    validate_plan_eval_output,
)
from tools.core.models import ChunkEntry, SourceAgentConfig
from tools.core.source_profiles import SourceProfile


logger = logging.getLogger(__name__)

# ── 类型别名 ────────────────────────────────────────────────────────
ExecuteFn = Callable[[str, str], Coroutine[Any, Any, List[ChunkEntry]]]
"""子类提供的 execute 适配器：(sub_query, target_focus) -> List[ChunkEntry]"""


@dataclass
class Focus:
    """全局 planner 产出的单个 focus 维度（v2 精简：只带路由，不带专业 hints）。

    ``targets``: dispatcher 读该字段决定往哪些源派活；SourceAgent 内部忽略。
    其余专业参数（tags / indexes / time_filter）由各 SourceAgent 的 plan_only
    LLM 根据 focus.text 自行产出，不在 Focus 里承载。
    """
    focus_id: str
    text: str
    targets: List[str] = field(default_factory=list)


@dataclass
class SubQuery:
    """plan_only / plan_eval 产出的一条子查询（v2：支持源特有参数）。

    ``tags`` / ``indexes`` / ``time_filter`` / ``query_type`` 等为可选字段：
    - bailian 的 plan_only LLM 会填 tags (L2 + 时效) 和 indexes
    - feishu 的 plan_only LLM 会填 time_filter（"last_N_days" 或 None）和
      query_type（"structured"/"concept"，驱动 Feishu reader 路由与召回数量）
    - google 的 plan_only LLM 可以动态产出 google_enable_fulltext (bool) 与 google_order_by (str)
    """
    query: str
    target_focus: str
    tags: List[str] = field(default_factory=list)
    indexes: List[str] = field(default_factory=list)
    time_filter: Optional[str] = None
    query_type: Optional[str] = None
    google_enable_fulltext: Optional[bool] = None
    google_order_by: Optional[str] = None
    author_filter: Optional[str] = None
    sub_query_id: str = ""


@dataclass
class SourceAgentResult:
    """SourceAgent 收尾产物（供 MergeStage 使用）。"""
    source: str
    top_chunks: List[ChunkEntry]              # 已打 tier，按重要度排序
    loops_used: int
    elapsed_s: float


# ─────────────────────────────────────────────────────────────────────
# LlmAgent 运行小工具
# ─────────────────────────────────────────────────────────────────────
async def _run_llm_agent(
    agent: LlmAgent,
    prompt_text: str,
    app_name: str = "retrieve_v1",
) -> Dict[str, Any]:
    """用 InMemoryRunner 单发 LlmAgent，返回 output_schema 校验后的 dict。

    流程：
    1. 每次调用新建一个独立 session（InMemorySessionService 单进程内）
    2. user content = prompt_text
    3. 遍历 events，取 author==agent.name 且含 text part 的最终 model 回复
    4. json.loads 结果（LlmAgent 配了 output_schema，返回体是 JSON 文本）
    """
    runner = InMemoryRunner(agent=agent, app_name=app_name)
    user_id = f"u_{uuid.uuid4().hex[:8]}"
    session = await runner.session_service.create_session(
        app_name=app_name, user_id=user_id,
    )

    content = genai_types.Content(
        role="user", parts=[genai_types.Part(text=prompt_text)],
    )

    final_text: Optional[str] = None
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content,
    ):
        if event.author != agent.name:
            continue
        if not event.content or not event.content.parts:
            continue
        for part in event.content.parts:
            if part.text:
                final_text = part.text

    if not final_text:
        raise RuntimeError(f"LlmAgent {agent.name} returned no text output")

    try:
        return json.loads(final_text)
    except json.JSONDecodeError as e:
        logger.error("LlmAgent %s JSON 解析失败: %s\nraw=%s", agent.name, e, final_text[:500])
        raise


# ─────────────────────────────────────────────────────────────────────
# Prompt 拼装（与 sub_agent 的 instruction 模板占位符保持一致）
# ─────────────────────────────────────────────────────────────────────
def _focuses_block(focuses: List[Focus]) -> str:
    return "\n".join(f"- {f.focus_id}: {f.text}" for f in focuses)


def _chunks_block(
    chunks: List[ChunkEntry],
    max_chars: int = 300,
    focuses: Optional[List["Focus"]] = None,
) -> str:
    """渲染候选 chunks 列表供 LLM 评估。

    表格类 chunk（content 含 markdown 表格）使用更大的摘要窗口（500 字），
    让 LLM 能看到实际数据行，而不只是表头。普通 chunk 使用 300 字。
    """
    if not chunks:
        return "(empty)"

    _TABLE_MARKERS = ("| --- |", "| --- ", "Bitable:", "Sheet:")

    def _is_table_chunk(c: ChunkEntry) -> bool:
        content = c.content or ""
        return any(m in content for m in _TABLE_MARKERS)

    def _render_one(c: ChunkEntry) -> str:
        effective_max = 500 if _is_table_chunk(c) else max_chars
        snippet = (c.content or "").replace("\n", " ").strip()
        if len(snippet) > effective_max:
            snippet = snippet[:effective_max] + "..."
        meta = c.metadata or {}
        route = meta.get("route") or meta.get("route_hint") or ""
        source_label = meta.get("source_name") or ""
        heading = meta.get("heading_path") or ""
        extra = f" | route={route}" if route else ""
        extra += f" | src={source_label[:30]}" if source_label else ""
        extra += f" | heading={heading[:40]}" if heading else ""
        return (
            f"- id={c.id} | focuses={sorted(c.target_focuses)} "
            f"| score={c.score}{extra} | {snippet}"
        )

    # 扁平渲染（兼容老路径）
    if not focuses:
        return "\n".join(_render_one(c) for c in chunks)

    # 分组渲染：按 focus 顺序，每个 chunk 只归属第一个命中的 focus
    placed: set = set()
    lines: List[str] = []
    for f in focuses:
        members = [c for c in chunks if f.focus_id in c.target_focuses and c.id not in placed]
        if not members:
            continue
        lines.append(f"## {f.focus_id}: {f.text}")
        for c in members:
            lines.append(_render_one(c))
            placed.add(c.id)

    # 兜底组：没匹配任何 focus 的 chunk
    leftover = [c for c in chunks if c.id not in placed]
    if leftover:
        lines.append("## Unassigned (target_focuses 为空或未在 focuses 列表)")
        for c in leftover:
            lines.append(_render_one(c))

    return "\n".join(lines)


def _sub_query_budget(n_focuses: int, source_top_k: int) -> int:
    """v2.1: sub_queries 总数上限 = min(n_focuses*2, source_top_k*2)，保底 2。

    取代旧的 ``max(3, source_top_k)``：
    - 旧公式 n_focuses=1 时允许 5 条 sub_q（过度分配）
    - 旧公式 n_focuses=6 时仍只有 5 条（过度收紧）
    新公式随 focus 数线性增长，天花板由 source_top_k 决定。
    """
    return max(2, min(n_focuses * 2, source_top_k * 2))


def _build_plan_only_prompt(
    focuses: List[Focus],
    profile: SourceProfile,
    source_top_k: int,
    extra_rules_block: str = "",
) -> str:
    """拼 plan_only 用户消息。``extra_rules_block`` 供子类注入源专业规则。"""
    sq_budget = _sub_query_budget(len(focuses), source_top_k)
    parts = [
        f"focuses_block:\n{_focuses_block(focuses)}",
        f"source_profile_block:\n{profile.to_prompt_block()}",
        f"source_top_k: {source_top_k}",
        f"sub_query_total_cap: {sq_budget}  (sub_queries 总数不得超过该上限)",
    ]
    if extra_rules_block:
        parts.append(f"source_query_rules_block:\n{extra_rules_block}")
    return "\n\n".join(parts)


def _build_eval_only_prompt(
    focuses: List[Focus], chunks: List[ChunkEntry], source_top_k: int,
) -> str:
    return (
        f"focuses_block:\n{_focuses_block(focuses)}\n\n"
        f"chunks_block:\n{_chunks_block(chunks, focuses=focuses)}\n\n"
        f"source_top_k: {source_top_k}"
    )


def _build_plan_eval_prompt(
    focuses: List[Focus],
    chunks: List[ChunkEntry],
    loop_n: int,
    min_loops: int,
    max_loops: int,
    source_top_k: int,
) -> str:
    return (
        f"focuses_block:\n{_focuses_block(focuses)}\n\n"
        f"chunks_block:\n{_chunks_block(chunks, focuses=focuses)}\n\n"
        f"loop_info: loop_n={loop_n}, min_loops={min_loops}, max_loops={max_loops}\n"
        f"source_top_k: {source_top_k}"
    )


# ─────────────────────────────────────────────────────────────────────
# SourceAgent 基类
# ─────────────────────────────────────────────────────────────────────
class SourceAgent:
    """SourceAgent 抽象基类——管理单源 loop 生命周期。

    子类需实现 ``execute_sub_query``，或在构造时传入 ``execute_fn``。
    """

    source: str = ""                          # 子类覆盖："feishu" / "bailian" / ...

    def __init__(
        self,
        profile: SourceProfile,
        cfg: SourceAgentConfig,
        execute_fn: Optional[ExecuteFn] = None,
    ):
        """构造 SourceAgent 基类。

        子类需要覆写 _build_xxx_agent 钩子来提供源特定的 LLM Agents。
        若 _build_plan_only_agent 返回 None，此时子类必须覆写
        ``_plan_sub_queries(focuses)`` 用确定性逻辑直接把 focus 映射成 sub_queries。
        """
        self.profile = profile
        self.cfg = cfg
        self.plan_only = self._build_plan_only_agent()
        self.eval_only = self._build_eval_only_agent()
        self.plan_eval = self._build_plan_eval_agent()
        self._execute_fn = execute_fn
        # 运行时注入：execute_sub_query 通过 target_focus 回查对应 Focus 对象
        self._current_focus_lookup: Dict[str, Focus] = {}
        self._sub_query_seq: int = 0

    def _build_plan_only_agent(self) -> Optional[LlmAgent]:
        """构造用于第一轮规划的 LLM Agent，子类覆写。"""
        return None

    def _build_eval_only_agent(self) -> LlmAgent:
        """构造用于结果收尾评估的 LLM Agent，子类必须覆写。"""
        raise NotImplementedError("SourceAgent must implement _build_eval_only_agent")

    def _build_plan_eval_agent(self) -> Optional[LlmAgent]:
        """构造用于多轮中间评估的 LLM Agent，子类按需覆写。"""
        return None

    async def execute_sub_query(
        self, sub_query: str, target_focus: str,
    ) -> List[ChunkEntry]:
        """子类 override 或通过 execute_fn 注入。默认抛错。

        ⚠️ 这是**旧版**两参签名。v2 起建议子类改用 ``execute_sub_query_full`` 接收
        ``SubQuery`` 整个对象（可读 tags / indexes / time_filter）。基类会自动
        dispatch：子类若实现 ``execute_sub_query_full``，基类只走它。
        """
        if self._execute_fn is None:
            raise NotImplementedError(f"{type(self).__name__} must implement execute_sub_query")
        return await self._execute_fn(sub_query, target_focus)

    async def execute_sub_query_full(self, sq: "SubQuery") -> List[ChunkEntry]:
        """v2 执行入口：接收完整 SubQuery（可读 tags / indexes / time_filter）。

        默认实现 fallback 到老的 ``execute_sub_query(query, target_focus)``，
        子类想消费源专属参数时覆写此方法即可。
        """
        return await self.execute_sub_query(sq.query, sq.target_focus)

    # ── 子类覆写点：focus → sub_queries 的确定性映射（可选） ────────
    def _plan_sub_queries(self, focuses: List[Focus]) -> List[SubQuery]:
        """默认返空——走 plan_only LLM 路径。

        子类若想跳过 LLM（极少情况）可覆写返回非空列表。大多数场景保持默认，
        让 plan_only LLM 基于 focus.text + 源专业规则自主产出 sub_queries。
        """
        return []

    # ── 子类覆写点：注入源专业规则到 plan_only prompt ────────────
    def _source_query_rules_block(self) -> str:
        """返回该源的 query 编写规则文本（供 plan_only prompt 拼接）。

        子类覆写以注入：业务 tag 枚举、国家缩写规则、时效解析规则、indexes 白名单等。
        默认空——基类路径只依赖 SourceProfile。
        """
        return ""

    def _post_process_sub_queries(
        self,
        focuses: List[Focus],
        sub_queries: List[SubQuery],
    ) -> List[SubQuery]:
        """规划后 callback。

        设计意图：
        - 保持 ``_plan`` 主链简洁：focus -> plan_only -> assign ids -> callback
        - 允许子类做最小限度的收束、校验、去重，而不用覆写整段 ``_plan`` 流程
        - 默认不做任何处理
        """
        _ = focuses
        return sub_queries

    def _assign_sub_query_ids(self, sub_queries: List[SubQuery]) -> List[SubQuery]:
        """为运行时 sub_queries 分配稳定的本地 id。"""
        for sq in sub_queries:
            if sq.sub_query_id:
                continue
            self._sub_query_seq += 1
            sq.sub_query_id = f"{self.source}:{sq.target_focus}:sq{self._sub_query_seq}"
        return sub_queries

    async def _plan(self, focuses: List[Focus]) -> List[SubQuery]:
        """统一规划入口：优先子类确定性 _plan_sub_queries；空则回退 plan_only LLM。"""
        subs = self._plan_sub_queries(focuses)
        if subs:
            return self._post_process_sub_queries(
                focuses,
                self._assign_sub_query_ids(subs),
            )
        if self.plan_only is None:
            return []
        prompt = _build_plan_only_prompt(
            focuses, self.profile, self.cfg.source_top_k,
            extra_rules_block=self._source_query_rules_block(),
        )
        out = await _run_llm_agent(self.plan_only, prompt)
        planned = self._assign_sub_query_ids([
            SubQuery(
                query=q["query"],
                target_focus=q["target_focus"],
                tags=list(q.get("tags") or []),
                indexes=list(q.get("indexes") or []),
                time_filter=q.get("time_filter"),
                query_type=q.get("query_type"),
                google_enable_fulltext=q.get("google_enable_fulltext"),
                google_order_by=q.get("google_order_by"),
                author_filter=q.get("author_filter"),
            )
            for q in out.get("sub_queries", [])
        ])
        return self._post_process_sub_queries(focuses, planned)

    # ── 公开入口 ──────────────────────────────────────────────
    async def run(self, focuses: List[Focus]) -> SourceAgentResult:
        """跑完整个 SourceAgent loop，返回 top_chunks。"""
        start = asyncio.get_event_loop().time()
        chunk_pool: Dict[str, ChunkEntry] = {}
        # 注入 focus 回查表，供 execute_sub_query 读 focus 的完整字段（time_hint/entity_hint 等）
        self._current_focus_lookup = {f.focus_id: f for f in focuses}
        self._sub_query_seq = 0

        # 单 loop（min=max=1）走简化路径；其余走多 loop
        if self.cfg.min_loops == 1 and self.cfg.max_loops == 1:
            result = await self._run_single_loop(focuses, chunk_pool)
        else:
            if self.plan_eval is None:
                raise RuntimeError(
                    f"[{self.source}] max_loops={self.cfg.max_loops} > 1 but plan_eval agent is None"
                )
            result = await self._run_multi_loop(focuses, chunk_pool)

        elapsed = asyncio.get_event_loop().time() - start
        logger.info(
            "[%s] done: loops=%d, top_chunks=%d, elapsed=%.2fs",
            self.source, result.loops_used, len(result.top_chunks), elapsed,
        )
        result.elapsed_s = elapsed
        return result

    # ── 单 loop 路径：(_plan_sub_queries or plan_only) → execute → eval_only ─
    async def _run_single_loop(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry],
    ) -> SourceAgentResult:
        # A. 规划 sub_queries：优先子类确定性映射，其次 plan_only LLM 兜底
        sub_queries = await self._plan(focuses)
        if not sub_queries:
            logger.warning("[%s] produced 0 sub_queries", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)

        logger.info("[%s] plan produced %d sub_queries", self.source, len(sub_queries))

        # B. execute（并发跑所有 sub_query）
        await self._execute_batch(sub_queries, chunk_pool)

        if not chunk_pool:
            logger.warning("[%s] execute produced 0 chunks, skip eval_only", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)

        # C. eval_only 收尾（单 loop 场景下按 spec §2.4 必跑一次）
        top_chunks = await self._eval_only_finalize(focuses, chunk_pool)
        return SourceAgentResult(self.source, top_chunks, loops_used=1, elapsed_s=0.0)

    # ── 多 loop 路径：plan_only → execute → (plan_eval → execute)* → eval_only ──
    async def _run_multi_loop(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry],
    ) -> SourceAgentResult:
        """多 loop 检索路径（spec §2.4）。

        - loop_n=1: plan_only 产出首轮 sub_queries → execute
        - loop_n=2..: plan_eval 判 continue/stop
            - continue: 用 replan_queries 再跑一轮 execute
            - stop: 用 plan_eval 返回的 top_k_chunk_ids + tiers 组装结果；
              若返回为空则兜底跑 eval_only
        - loop_n == max_loops: 强制 stop；若 plan_eval 未给 top_k → 兜底跑 eval_only
        """
        min_loops, max_loops = self.cfg.min_loops, self.cfg.max_loops
        assert self.plan_eval is not None  # run() 已校验

        # loop 1: plan → execute
        sub_queries = await self._plan(focuses)
        if not sub_queries:
            logger.warning("[%s] loop=1 produced 0 sub_queries", self.source)
            return SourceAgentResult(self.source, [], loops_used=1, elapsed_s=0.0)
        logger.info("[%s] loop=1 plan produced %d sub_queries", self.source, len(sub_queries))
        await self._execute_batch(sub_queries, chunk_pool)

        loop_n = 1

        # loop 2..max_loops: plan_eval（中间轮决策）
        while loop_n < max_loops:
            loop_n += 1

            if not chunk_pool:
                logger.warning("[%s] loop=%d chunk_pool empty → stop early", self.source, loop_n)
                break

            pe_prompt = _build_plan_eval_prompt(
                focuses, list(chunk_pool.values()),
                loop_n=loop_n, min_loops=min_loops, max_loops=max_loops,
                source_top_k=self.cfg.source_top_k,
            )
            pe_out = await _run_llm_agent(self.plan_eval, pe_prompt)
            pe_out = validate_plan_eval_output(pe_out)

            raw_decision = pe_out.get("decision", "stop")
            decision = enforce_loop_bounds(raw_decision, loop_n, min_loops, max_loops)
            logger.info(
                "[%s] loop=%d plan_eval raw=%s enforced=%s",
                self.source, loop_n, raw_decision, decision,
            )

            if decision == "continue":
                replan = [
                    SubQuery(
                        query=q["query"],
                        target_focus=q["target_focus"],
                        tags=list(q.get("tags") or []),
                        indexes=list(q.get("indexes") or []),
                        time_filter=q.get("time_filter"),
                        query_type=q.get("query_type"),
                        google_enable_fulltext=q.get("google_enable_fulltext"),
                        google_order_by=q.get("google_order_by"),
                        author_filter=q.get("author_filter"),
                    )
                    for q in (pe_out.get("replan_queries") or [])
                ]
                replan = self._assign_sub_query_ids(replan)
                if not replan:
                    logger.warning("[%s] loop=%d continue with empty replan → stop", self.source, loop_n)
                    break
                logger.info("[%s] loop=%d replan %d sub_queries", self.source, loop_n, len(replan))
                await self._execute_batch(replan, chunk_pool)
                continue

            # decision == "stop" → 尝试直接用 plan_eval 的 top_k
            top_ids: List[str] = pe_out.get("top_k_chunk_ids") or []
            tiers_map: Dict[str, Dict] = tiers_list_to_map(pe_out.get("tiers"))
            if top_ids:
                top_chunks = self._assemble_top_chunks(top_ids, tiers_map, chunk_pool)
                return SourceAgentResult(self.source, top_chunks, loops_used=loop_n, elapsed_s=0.0)
            # plan_eval stop 但没给 top_k → 走 eval_only 兜底
            logger.info("[%s] loop=%d plan_eval stop without top_k → fallback eval_only", self.source, loop_n)
            break

        # 收尾：eval_only
        if not chunk_pool:
            return SourceAgentResult(self.source, [], loops_used=loop_n, elapsed_s=0.0)
        top_chunks = await self._eval_only_finalize(focuses, chunk_pool)
        return SourceAgentResult(self.source, top_chunks, loops_used=loop_n, elapsed_s=0.0)

    # ── 公用小工具：eval_only 收尾 + top_k 组装 ────────────────
    async def _eval_only_finalize(
        self, focuses: List[Focus], chunk_pool: Dict[str, ChunkEntry],
    ) -> List[ChunkEntry]:
        started = time.perf_counter()
        eval_prompt = _build_eval_only_prompt(
            focuses, list(chunk_pool.values()), self.cfg.source_top_k,
        )
        eval_out = await _run_llm_agent(self.eval_only, eval_prompt)
        top_ids: List[str] = eval_out.get("top_k_chunk_ids", [])
        tiers_map: Dict[str, Dict] = tiers_list_to_map(eval_out.get("tiers"))
        chunks = self._assemble_top_chunks(top_ids, tiers_map, chunk_pool)
        logger.info(
            "TIMING source.eval_only source=%s input_chunks=%d selected=%d ms=%.1f",
            self.source,
            len(chunk_pool),
            len(chunks),
            (time.perf_counter() - started) * 1000.0,
        )
        return chunks

    def _assemble_top_chunks(
        self,
        top_ids: List[str],
        tiers_map: Dict[str, Dict],
        chunk_pool: Dict[str, ChunkEntry],
    ) -> List[ChunkEntry]:
        """组装 LLM 选出的 top_k，带两层 call-back 校验（防 LLM 幻觉）。"""
        allowed_focus_ids = set(self._current_focus_lookup.keys())
        out: List[ChunkEntry] = []
        for cid in top_ids[: self.cfg.source_top_k]:
            entry = chunk_pool.get(cid)
            if entry is None:
                # 幻觉 chunk_id：LLM 编了个不存在的 id
                logger.warning("[%s] hallucinated chunk_id rejected: %s", self.source, cid)
                continue
            info = tiers_map.get(cid) or {}
            tier = info.get("tier")
            rel = info.get("relevance")
            raw_ans = info.get("answered_focuses") or []

            # answered_focuses 白名单：过滤非法 focus_id（LLM 偶发幻觉出不存在的 focus）
            valid_ans = [fid for fid in raw_ans if fid in allowed_focus_ids]
            if len(valid_ans) != len(raw_ans):
                dropped = [fid for fid in raw_ans if fid not in allowed_focus_ids]
                logger.warning(
                    "[%s] chunk=%s: dropped invalid answered_focuses=%s (allowed=%s)",
                    self.source, cid, dropped, sorted(allowed_focus_ids),
                )

            # 1) 打 tier
            entry = entry.with_tier(tier)
            # 2) 覆盖 score + 写 metadata
            new_meta = dict(entry.metadata or {})
            if valid_ans:
                new_meta["answered_focuses"] = list(valid_ans)
            entry = ChunkEntry(
                id=entry.id,
                source=entry.source,
                content=entry.content,
                metadata=new_meta,
                score=float(rel) if rel is not None else entry.score,
                sub_query=entry.sub_query,
                target_focuses=entry.target_focuses,
                tier=entry.tier,
            )
            out.append(entry)
        return out

    # ── 并发执行 sub_queries ──────────────────────────────────
    async def _execute_batch(
        self, sub_queries: List[SubQuery], chunk_pool: Dict[str, ChunkEntry],
    ) -> None:
        async def _one(sq: SubQuery) -> List[ChunkEntry]:
            try:
                return await self.execute_sub_query_full(sq)
            except Exception as e:
                logger.warning("[%s] sub_query %r failed: %s", self.source, sq.query, e)
                return []

        batches = await asyncio.gather(*[_one(sq) for sq in sub_queries])
        for sq, chunks in zip(sub_queries, batches):
            for c in chunks:
                # 保证 target_focus / sub_query 标记正确
                entry = ChunkEntry(
                    id=c.id,
                    source=c.source,
                    content=c.content,
                    metadata=dict(c.metadata),
                    score=c.score,
                    sub_query=c.sub_query or sq.query,
                    target_focuses=set(c.target_focuses) | {sq.target_focus},
                    tier=c.tier,
                )
                existing = chunk_pool.get(entry.id)
                chunk_pool[entry.id] = existing.merge(entry) if existing else entry

    # ── 静态引用（Phase 2 用） ──────────────────────────────────
    @staticmethod
    def _loop_should_continue(
        decision: str, loop_n: int, min_loops: int, max_loops: int,
    ) -> bool:
        return enforce_loop_bounds(decision, loop_n, min_loops, max_loops) == "continue"

    @staticmethod
    def _needs_eval(loop_n: int, min_loops: int, max_loops: int) -> bool:
        return needs_eval(loop_n, min_loops, max_loops)
