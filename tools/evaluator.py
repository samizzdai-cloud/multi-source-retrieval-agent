"""EvaluationLayer — 渐进式结果收集 + 双阈值熔断器

基于 asyncio.as_completed 实现：
- 结果到达即评估，满足条件立即早停
- 硬超时兜底，永远返回部分结果
- Fail-open: 任何源报错 → skip，不影响整体
- 进度可见性接口: 每次收到结果 print 当前已收集状态（后续可替换为 callback）

设计参考: Claude Code hookify stop-rule + ralph max_iterations pattern
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Set

from .models import UnifiedChunk

logger = logging.getLogger(__name__)


@dataclass
class EvalConfig:
    """统一配置入口 — 熔断器 + Reranker + Embedding 所有参数。

    在 root_agent.py 中统一设置，各层从此读取，避免参数散落。
    """
    # ── EvaluationLayer: 早停条件 ─────────────────────────────────
    # [P2] 早停由 per-focus 覆盖驱动，min_good_chunks 不再参与早停判断（保留用于日志）
    min_good_chunks: int = 8            # [P2] 仅用于日志/统计，不参与早停条件
    min_relevance_score: float = 0.45   # [P2] chunk.score >= 此值才算"高质量"（0.3→0.45，0.40 已验证会导致 lo_pct 超红线）
    hard_timeout_seconds: float = 45.0  # 绝对截止时间（秒）
    min_sources: int = 1                # 至少等到 N 个不同源有结果才允许早停
    min_per_focus: int = 2              # 每个 focus 至少需要 N 个 good chunk 命中才算满足
    early_stop_min_elapsed_s: float = 0.0  # [P2 新增] 早停最小等待时间（秒），0=立即允许早停
    # ── Reranker: 输出控制 ───────────────────────────────────────
    min_per_focus_select: int = 2       # 每个 focus select_by_focus 选出的代表 chunk 数
    max_per_doc_per_focus: int = 4      # 每个 focus 最多允许同一个 source 返回多少个 chunk
    top_k_chunks: int = 25              # [P1.1↑] 最终输出上限（原10，扩大到25）
    # ── Embedding: 语义匹配控制 ──────────────────────────────────
    use_embedding_for_select: bool = False  # True=语义embedding匹配, False=关键词匹配
    embedding_sim_threshold: float = 0.4   # focus-chunk cosine_sim 命中阈值（0-1）
    # 实测 sim 分布 0.49~0.75（中英混排），0.4 覆盖全场景
    embedding_dim: int = 512            # 向量维度（512=速度优先；1024=精度优先，延迟+50%）
    # ── 混合评分权重 ──────────────────────────────────────────────
    # base_score = source_weight × norm_src + (1-source_weight) × norm_rank
    # affinity = base_score × (1 + emb_weight × sim_emb + kw_weight × sim_kw)
    # RSF 侧已用 kw_generalization 扩展 BM25，select_by_focus 层侧重语义互补
    source_weight: float = 0.6         # RSF 权重 (0=纯RRF, 1=纯RSF)
    emb_weight: float = 0.55           # embedding 语义相似度权重（语义层主导）
    kw_weight: float = 0.10            # 泛化关键词 overlap 权重（RSF 侧已充分表达）


class EvaluationLayer:
    """渐进式结果收集器。替代 asyncio.gather，支持早停。

    Usage:
        evaluator = EvaluationLayer(EvalConfig())
        tasks = {asyncio.create_task(coro): "source_name" for ...}
        chunks, stats = await evaluator.collect(tasks)

    进度可见性:
        默认 print 每次收到结果的状态快照。
        后续可通过 on_progress callback 替换 print 为任意处理逻辑：
        evaluator = EvaluationLayer(config, on_progress=my_callback)
        # callback 签名: (source, chunks_from_source, all_chunks, source_stats, elapsed) -> None
    """

    def __init__(
        self,
        config: EvalConfig | None = None,
        on_progress: Optional[Callable] = None,
        verbose: bool = True,
    ):
        self.config = config or EvalConfig()
        self.on_progress = on_progress  # 未来扩展: 替换 print 的 callback 接口
        self.verbose = verbose          # False 时静默，不 print 进度

    def _print_progress(
        self,
        source: str,
        new_chunks: List[UnifiedChunk],
        all_chunks: List[UnifiedChunk],
        source_stats: Dict[str, int],
        good_count: int,
        elapsed: float,
        focuses: Optional[List[str]] = None,
        focus_hits: Optional[Dict[str, int]] = None,
        all_focuses: Optional[List[str]] = None,
    ) -> None:
        """打印每个来源返回的内容快照，绑定 focus 意图标注。后续可替换为 callback。"""
        # 提取 focus 关键词用于标注匹配（复用 reranker 的共享停用词表）
        from .reranker import _extract_focus_words
        focus_words = _extract_focus_words(focuses) if focuses else set()

        stats_str = " | ".join(f"{s}={n}" for s, n in sorted(source_stats.items()))
        print(f"\n[RAG 进展] 来源: {source}  耗时: {elapsed:.1f}s  已收集: {stats_str}  高质量={good_count}/{len(all_chunks)}")

        if focuses:
            print(f"  意图: {focuses[0][:80]}")

        for i, c in enumerate(new_chunks[:5], 1):  # 最多展示 5 条
            preview = c.text[:100].replace("\n", " ").strip()
            chunk_words = set(re.findall(r'[\w\u4e00-\u9fff]+', preview.lower()))
            match_count = len(focus_words & chunk_words) if focus_words else 0
            focus_mark = f" [✓ focus匹配={match_count}词]" if match_count > 0 else ""
            src_label = c.metadata.get("index_name", c.source_name or c.source_tool)
            print(f"  [{i}] score={c.score:.2f}{focus_mark} | {src_label[:30]} | {preview[:80]}...")

        if len(new_chunks) > 5:
            print(f"  ... 及其他 {len(new_chunks)-5} 条")

        if self.on_progress is not None:
            try:
                self.on_progress(source, new_chunks, all_chunks, source_stats, elapsed,
                                 focus_hits=focus_hits, all_focuses=all_focuses)
            except Exception as e:
                logger.warning("on_progress callback raised: %s", e)

    def _update_focus_hits(
        self,
        new_chunks: List[UnifiedChunk],
        focus_keywords: Dict[str, set],
        focus_hits: Dict[str, int],
        min_score: float,
        focus_core_keywords: Optional[Dict[str, set]] = None,
        query_type: str = "structured",
    ) -> None:
        """对新到达的 chunks，逐个匹配每个 focus 的关键词，更新命中计数。

        命中条件（改动B）：
          核心词（不展开）至少 1 个命中 AND 泛化词 overlap >= min_overlap
          [P1.5] min_overlap 统一为 1（放宽 structured 型阈值，避免早停误判）
          防止任意一个泛化词命中即满足导致早停误判。
          focus_core_keywords 为 None 时降级为旧逻辑（向后兼容）。
        """
        # [P1.5] 统一 min_overlap=1，放宽 focus hit 门槛以扩大召回
        min_overlap = 1

        for c in new_chunks:
            if c.score < min_score:
                continue
            chunk_words = set(re.findall(r'[\w\u4e00-\u9fff]+', c.text[:500].lower()))
            for focus, expanded_kw in focus_keywords.items():
                if focus_core_keywords is not None:
                    # [改动B+E3] 双门槛：核心词命中 AND 展开词 overlap >= min_overlap
                    core_kw = focus_core_keywords.get(focus, set())
                    core_hit = bool(chunk_words & core_kw) if core_kw else True
                    overlap = len(chunk_words & expanded_kw)
                    if core_hit and overlap >= min_overlap:
                        focus_hits[focus] = focus_hits.get(focus, 0) + 1
                else:
                    # 降级：旧逻辑（任意一词命中即满足）
                    if chunk_words & expanded_kw:
                        focus_hits[focus] = focus_hits.get(focus, 0) + 1

    def _all_focuses_satisfied(self, focus_hits: Dict[str, int], all_focuses: List[str]) -> bool:
        """检查所有 focus 是否都达到 min_per_focus 阈值。"""
        if not all_focuses:
            return True  # 无 focus → 退化为全局计数
        return all(
            focus_hits.get(f, 0) >= self.config.min_per_focus
            for f in all_focuses
        )

    async def collect(
        self,
        task_map: Dict[asyncio.Task, str],
        focuses: Optional[List[str]] = None,
        task_group_map: Optional[Dict[asyncio.Task, int]] = None,
        embedding_cache: "Optional[object]" = None,
        focus_groups: Optional[List] = None,
        query_type: str = "structured",
    ) -> tuple[List[UnifiedChunk], Dict[str, int], Dict[str, int], Dict[int, int]]:
        """收集所有任务的结果，支持 per-focus 早停和硬超时。

        Args:
            task_map: {asyncio.Task: source_tool_name}
            focuses: 所有 focus 描述的 flat list
            task_group_map: {asyncio.Task: group_index}
            embedding_cache: EmbeddingCache 实例（可选，用于语义 focus 命中判断）
            focus_groups: 完整 FocusGroup 列表（含 required_targets），用于 forced_done 判断
            query_type: 查询类型 "structured" | "concept"，影响 focus 命中判断松紧

        Returns:
            (all_chunks, source_stats, focus_hits, task_results)
            task_results: {task_id: chunk_count} 用于 sub_query 级别分析
        """
        from .reranker import _extract_focus_words

        # 预处理 required_targets：构建 {task: bool} 表示该 task 是否被强制等待
        # task 属于某个 FG 且该 FG 的 required_targets 包含该 source_tool → 强制等待
        forced_task_ids: set = set()
        if focus_groups and task_group_map:
            for task, gi in task_group_map.items():
                if gi < len(focus_groups):
                    fg = focus_groups[gi]
                    src = task_map.get(task, "")
                    if src and src in getattr(fg, 'required_targets', []):
                        forced_task_ids.add(id(task))

        all_chunks: List[UnifiedChunk] = []
        source_stats: Dict[str, int] = {}
        sources_completed: Set[str] = set()
        good_count = 0
        # Per-task results tracking for sub_query analysis
        task_results: Dict[int, int] = {}  # task_id -> chunk_count

        # Per-focus 命中追踪
        # 分两层关键词：核心词（不展开）+ 泛化展开词
        # 命中条件：核心词至少 1 个 AND 展开词 overlap ≥ 2，防止泛化词误判
        all_focuses = focuses or []
        focus_core_keywords: Dict[str, set] = {}    # 不展开，focus 原生关键词
        focus_exp_keywords: Dict[str, set] = {}     # 泛化展开，包含同义词
        focus_keywords = focus_exp_keywords         # 兼容旧引用
        for f in all_focuses:
            from .reranker import _extract_focus_words, _extract_focus_words_expanded
            core = _extract_focus_words([f])
            expanded = _extract_focus_words_expanded([f])
            if core:
                focus_core_keywords[f] = core
            if expanded:
                focus_exp_keywords[f] = expanded
        focus_hits: Dict[str, int] = {f: 0 for f in all_focuses}

        # EmbeddingCache 可用性
        use_cache = embedding_cache is not None and getattr(embedding_cache, 'enabled', False)

        id_to_source: Dict[int, str] = {id(task): src for task, src in task_map.items()}
        # task → focus_group index（用于 rrf_group 注入）
        id_to_gi: Dict[int, int] = {
            id(task): gi
            for task, gi in (task_group_map or {}).items()
        }
        # [BUG-FIX-A] 用 task id 追踪已处理 task，避免同名 source_tool 第 2 个 task 被跳过导致 source="unknown"
        completed_task_ids: Set[int] = set()

        start = time.monotonic()
        deadline = start + self.config.hard_timeout_seconds
        # [P2.6] 用 asyncio.wait + 周期检查替代 as_completed
        # 解决: as_completed 只在 task 完成时检查早停，无法在两次完成之间触发
        pending = set(task_map.keys())
        early_stopped = False

        try:
            while pending:
                elapsed = time.monotonic() - start
                if elapsed >= self.config.hard_timeout_seconds:
                    logger.warning(
                        "EvalLayer: HARD TIMEOUT (%.1fs) — returning %d chunks from %d sources",
                        elapsed, len(all_chunks), len(sources_completed),
                    )
                    break

                # 等待任一 task 完成，最多等 1s（周期检查间隔）
                wait_timeout = min(1.0, deadline - time.monotonic())
                if wait_timeout <= 0:
                    break
                done_set, pending = await asyncio.wait(
                    pending, timeout=wait_timeout, return_when=asyncio.FIRST_COMPLETED,
                )

                # 处理所有刚完成的 tasks
                for future in done_set:
                    try:
                        chunks: List[UnifiedChunk] = future.result()
                    except Exception as e:
                        logger.warning("Retrieval task failed (skipping): %s", e)
                        continue

                    source = "unknown"
                    gi = 0
                    task_id = None
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    for task in task_map:
                        if task.done() and id(task) in id_to_source and id(task) not in completed_task_ids:
                            source = id_to_source[id(task)]
                            gi = id_to_gi.get(id(task), 0)
                            task_id = id(task)
                            completed_task_ids.add(task_id)
                            task_results[task_id] = {
                                "chunk_count": len(chunks),
                                "elapsed_ms": elapsed_ms,
                            }
                            break

                    rrf_group = f"g{gi}:{source}"
                    for c in chunks:
                        c.metadata["rrf_group"] = rrf_group

                    sources_completed.add(source)
                    all_chunks.extend(chunks)
                    source_stats[source] = source_stats.get(source, 0) + len(chunks)

                    good_count = sum(
                        1 for c in all_chunks
                        if c.score >= self.config.min_relevance_score
                    )

                    if use_cache:
                        embedding_cache.submit_chunks(chunks)

                    if use_cache:
                        focus_hits = embedding_cache.get_focus_hits(
                            all_chunks, self.config.min_relevance_score, focus_keywords,
                        )
                    elif focus_exp_keywords:
                        self._update_focus_hits(
                            chunks, focus_exp_keywords, focus_hits,
                            self.config.min_relevance_score,
                            focus_core_keywords=focus_core_keywords if focus_core_keywords else None,
                            query_type=query_type,
                        )

                    elapsed = time.monotonic() - start
                    logger.info(
                        "EvalLayer: +%d chunks from %s (total=%d, good=%d, sources=%d, %.1fs)",
                        len(chunks), source, len(all_chunks), good_count,
                        len(sources_completed), elapsed,
                    )

                    if self.verbose:
                        self._print_progress(source, chunks, all_chunks, source_stats, good_count, elapsed, focuses,
                                             focus_hits=focus_hits, all_focuses=all_focuses)
                        if all_focuses:
                            satisfied = sum(1 for f in all_focuses if focus_hits.get(f, 0) >= self.config.min_per_focus)
                            print(f"  [focus 覆盖] {satisfied}/{len(all_focuses)} 已满足")
                    elif self.on_progress is not None:
                        # verbose=False: still fire the callback (skip print-only progress)
                        try:
                            self.on_progress(source, chunks, all_chunks, source_stats, elapsed,
                                             focus_hits=focus_hits, all_focuses=all_focuses)
                        except Exception as e:
                            logger.warning("on_progress callback raised: %s", e)

                # [P2.6] 周期性早停检查 — 每次 wait 超时(1s)或有 task 完成时都检查
                elapsed = time.monotonic() - start
                all_satisfied = self._all_focuses_satisfied(focus_hits, all_focuses)
                if forced_task_ids:
                    forced_done = all(
                        t.done() for t in task_map if id(t) in forced_task_ids
                    )
                else:
                    forced_done = True
                elapsed_ok = elapsed >= self.config.early_stop_min_elapsed_s

                if (all_satisfied
                        and elapsed_ok
                        and len(sources_completed) >= self.config.min_sources
                        and forced_done):
                    logger.info(
                        "EvalLayer: EARLY STOP — good=%d, sources=%d, %d/%d focuses satisfied, "
                        "elapsed=%.1fs (min_elapsed=%.1fs), forced_done=%s",
                        good_count, len(sources_completed),
                        sum(1 for f in all_focuses if focus_hits.get(f, 0) >= self.config.min_per_focus),
                        len(all_focuses), elapsed, self.config.early_stop_min_elapsed_s, forced_done,
                    )
                    early_stopped = True
                    break
                elif all_satisfied and not elapsed_ok:
                    logger.debug(
                        "EvalLayer: focuses satisfied, waiting for min_elapsed (%.1fs < %.1fs)",
                        elapsed, self.config.early_stop_min_elapsed_s,
                    )

        except Exception as e:
            logger.warning("EvalLayer: unexpected error: %s", e)

        finally:
            cancelled = 0
            for task in task_map:
                if not task.done():
                    task.cancel()
                    cancelled += 1
            if cancelled:
                await asyncio.gather(*task_map.keys(), return_exceptions=True)
                logger.info("EvalLayer: cancelled %d remaining tasks", cancelled)

        # 最终 focus 覆盖度日志
        if focus_keywords:
            unsatisfied = [f for f in all_focuses if focus_hits.get(f, 0) < self.config.min_per_focus]
            if unsatisfied:
                logger.info("EvalLayer: %d/%d focuses unsatisfied: %s",
                            len(unsatisfied), len(all_focuses),
                            [f[:60] for f in unsatisfied])

        return all_chunks, source_stats, focus_hits, task_results
