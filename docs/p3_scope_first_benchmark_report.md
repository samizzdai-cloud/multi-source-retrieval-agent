# P3 v2 Scope-first Benchmark：运行问题与优化建议

## 当前结论（更新至 2026-07-22）

P3 试冻结尚未通过，不能把当前结果解释为“Gold Chain 构建错误”。六条
`sufficient_evidence` case 中，只有单 chunk 基线 `v2_pilot_06` 达到稳定
门槛（2/3）；Case 01--05 都未恢复完整 required roles。正式数据不足控制运行中，
Case07 为 0/3、Case08 为 1/3，仍不能报告完整的 upgrade correctness。接口和 I/O
修复后的隔离复测中，两例各 1/1 正确输出 `upstream_evidence_needed`；这只能证明
修复方向，不能替代正式 2/3 稳定性门槛。

2026-07-22 的授权 live rerun 未能开始：环境 tenant policy 在请求发出前禁止将飞书
检索正文发送给 DeepSeek/Qwen。该事件不构成 P3 失败、也没有新的 Feishu/模型 artifact；
不得绕过该策略。当前本地合同基线为 107/107：Bitable/Sheet outer wall timeout 已写入
body-free operational trace，dispatcher 的 `cancel_drain_timeout_s` 默认 10 秒，未收敛同步
I/O 会使 runner 禁止 whole-case retry。真实 Case02 的 SDK/网络收敛仍需在策略恢复后 live 验证。

同日的本地链路合同已推进至 107/107：partial admission 采用一次冻结 generation，零 refs
只会标记 `admission_unavailable` 并 fail-open 给 plan-eval，不能重复提交或删除 chunk；global
rank 后还有只消费既有 `critical/important` judgment 的 Focus quota guard，避免 fixed cap 静默
丢失已判定 Focus。它不等同于 P3 Gold role 通过、更不等同于全局 replan；仍须以恢复后的 live
RoleFunnel 验证 Case01、03--05。

非 rerank 流式源现以 admission 的 chunk-count trigger 批量启动一次 partial eval，而不是逐
文档调用模型；ContextBudget 仅保留为压力保护。Feishu deep-dive 仍在跨 sub-query pool 完成
Qwen post-I/O rerank 后才进入 admission，因此“文档读完立即 eval”的增量 handoff 仍为待实现，
不能将本次基类优化写成 Feishu deep-dive 已 live streaming。

### 2026-07-22 Case01 authorized rerun

`p3_scope_first_v2_runs_admission_refs_live` 的 Case01 为 0/1、205.09s、无 source timeout。
它确认四个 Gold role 都已 candidate/read 命中（7 unique documents、185 unique chunks），排除
空 dispatch/read 缺失。Qwen rerank 是 transport timeout 后 fail-open，不是语义淘汰；旧
RoleFunnel 将该 fail-open 误记为 rerank 缺失，已在本地修复。随后 DeepSeek global eval 返回了
混合 list JSON，旧兼容器回退 RuleEvalLayer，最终 6/7 chunks。当前本地代码已增加 Qwen group
并发（上限 2）、fail-open 归因/冻结和 global JSON array recovery，112/112 通过；须重新运行
Case01 才能把新代码与 live latency、Gold chain 结果关联。

随后本地实现了 `deterministic_diversity_fallback`：当所有 partial-eval batch 都没有可用 refs，
每 Focus 最多保留 18 条、按稳定 document lineage 轮转的候选进入 source plan-eval（两 Focus 理论
上限 36，允许必要共享）。该动作不赋 tier、不制造 evidence，专门避免 185 条 fail-open chunk 把
admission 故障转嫁为 source plan-eval 的上下文/时延压力。当前本地 113/113；需要新的 live Case01
artifact 才能验证它没有损伤 Gold role 并实际降低 E2E。

## 统一入口与执行顺序

`docs/temp_plan.md` 是唯一的执行台账和入口索引。本报告只记录本轮诊断；
规范、当前 gate、命令和后续动作以台账为准。

```powershell
# 1. 构建/审计/冻结（已有 artifact 时会复用相应 checkpoint）
python tests/p3_scope_first_workflow.py --stage scope-registry
python tests/p3_scope_first_workflow.py --stage query-drafts
python tests/p3_scope_first_workflow.py --stage audit
python tests/p3_scope_first_workflow.py --stage blueprints
python tests/p3_scope_first_workflow.py --stage chains
python tests/p3_scope_first_workflow.py --stage promote
python tests/p3_scope_first_workflow.py --stage review-packet
python tests/p3_scope_first_workflow.py --stage report

# 2. 人工审核跨文档 case 后，断点运行 retrieve-only
python eval/feishu_scope_first_runner.py --preset deep_dive --runs 3 --retries-after-initial 2 --source-timeout 180 --planner-timeout 45 --resume
```

入口代码：`tests/p3_scope_first_workflow.py`；运行器：
`eval/feishu_scope_first_runner.py`。正式 manifest 为
`tests/artifacts/p3_scope_first_v2/07_promoted_benchmark.json`；运行结果在
`tests/artifacts/p3_scope_first_v2_runs/`。正文与原始模型响应不进入 Git。

## 已完成运行

| Case | 类型 | 三轮结果 | 主要现象 |
|---|---|---:|---|
| 01 | 跨文档 | 0/3 | 仅有 query trace，final chunks 为空；非 timeout |
| 02 | 跨文档 | 0/3 | 三轮均 trace timeout，约 189--191 秒，读取重试 3 次 |
| 03 | 跨 chunk | 0/3 | candidate/read/judgment 已发生，但全链未恢复 |
| 04 | 跨 chunk | 0/3 | 稳定缺失 early-bird incentive role |
| 05 | 跨 chunk | 0/3 | 稳定缺失 C18 的诊断/动作角色，第三轮还缺 C3 |
| 06 | 单 chunk | 2/3 | 两次恢复唯一 Gold role；一次仅 query、空结果 |
| 07 | 数据不足控制 | 0/3 | run2/3 暴露 Bitable 长读；修复后隔离复测 1/1 通过，尚未形成稳定结论 |
| 08 | 数据不足控制 | 1/3 | 非法/矛盾 plan_eval 枚举导致前两轮漏判；合同归一化后隔离复测 1/1 通过 |

### Control attempt 成本与修复后证据

| Case/run | 正式结果 | attempts | E2E / attempt 累计秒 | replan |
|---|---:|---:|---:|---|
| 07/1 | fail | 3 | 92.50 / 旧 artifact 未记录 | none |
| 07/2 | fail | 2 | 207.17 / 337.03 | none |
| 07/3 | fail | 1 | 207.19 / 207.19 | none |
| 08/1 | fail | 3 | 126.31 / 268.40 | none |
| 08/2 | fail | 3 | 78.20 / 217.73 | none |
| 08/3 | pass | 3 | 81.36 / 239.03 | upstream_evidence_needed |
| 07 postfix/1 | pass | 1 | 121.59 / 121.59 | upstream_evidence_needed |
| 08 postfix/1 | pass | 1 | 104.22 / 104.22 | upstream_evidence_needed |

Postfix 产物在 `tests/artifacts/p3_scope_first_v2_runs_postfix/`。Case07 落盘后进程曾
等待已取消的同步 reader 线程，之后自然退出；因此 I/O 收敛 Gate 仍未通过，不能把
“当前无残留进程”解释为已经证明所有 timeout 都可在 `timeout + 10s` 内退出。

### 2026-07-20 补充：LLM 枚举与 Bitable 下钻根因

- `partially_covered/evidence_gap` 来自 DeepSeek 原始 plan_eval。合同层现在显式记录
  raw→normalized 映射；benchmark 不再从 reason 自行猜测。
- Case07 历史错误命中的 `UFpwbPmsYaxPxBskcVfcW7pnn3b` 有 85 张 child tables；旧路径
  实际读取全部 child，并且 fallback records 会先遍历全部分页。这不是单纯 timeout 太短。
- Feishu/Google 已统一为 per-parent metadata rerank：child `>3` 才调用 Qwen、top3、5s、
  fail-open；records 上限下推到底层分页。
- 新版 Case07 隔离运行单次通过并正确输出 upstream，耗时 136.05s；该次 table search
  返回 0 parent，因此只验证了 eval/control，不构成 child rerank 的 live 效果证据。

### 2026-07-20 补充：Case01 judgment 输出漏斗

- 空 dispatch 已关闭：实时回归稳定产生 4--5 个 query、9--14 个 candidate 与完整 read trace。
- 缺失的 `hypershell 6,993 / 1,392 (+30.6%)` 来自完整 docx 读取中的嵌入表格；lite
  block-only 路径不包含该证据。
- 旧路径一次性把 151 个唯一 chunk 交给 plan_eval，最终只返回 8 个 judgment。目标区段已被
  post-I/O rerank 保留，因此责任层是 eval 输出形状/注意力，不是 search 或 rerank。
- 固定 24 条分批的诊断 run 恢复 165 个 judgment，并使目标搜索/联盟角色进入 final；但 9 个
  并发调用中 2 批无输出，且错误触发 upstream gap。120 条紧凑单批又发生整批漏判。
- 60 条穷举分区 + 二分诊断最终 Case01 通过，但耗时 213.06s，仍有 60 条主批和 30 条子批
  漏判，不能作为性能方案。当前改为每批最多 80 条的 sparse evidence shortlist：只输出
  plausible critical/important refs，未入选项记录为 `not_shortlisted` 而不是伪造 noise；随后由
  唯一 plan_eval 决定 tier/gap。该版本尚待 live Gate。

## 问题、证据与建议

1. **最高优先级：source 读取稳定性与超时可观测性。** Case 02 的三轮都
   在 read tier 结束于 trace `timeout`，每轮约 190 秒；此前运行器仅检查日志
   文本，遗漏 trace timeout，现已修正。先按 document token/read request 聚合
   timeout、耗时和成功率，确认是否某个文档或表格导致阻塞；不要用更多语义
   prompt 或 rerank 参数掩盖 I/O 问题。

2. **最高优先级：空结果的 planner/candidate 断路。** Case 01 三轮均只留
   `query` event 且无 candidate/read，说明堵塞位在 planner 输出、source dispatch
   或 early short-circuit，而非 rerank。应补一条每次请求必有的 trace summary：
   planner focus、发出的 Feishu 查询、source dispatch reason、空候选原因。

3. **次高优先级：跨 chunk 的最终保留丢链。** Case 03--05 已显示
   candidate/read/judgment，但 required roles 没有同时抵达 final chunks。这是
   post-I/O evidence compression 的问题：需要按 Gold role 标注每个 tier 的
   `candidate/read/judgment/final` presence，定位是没召回、读失败、还是 judgment/
   rerank 淘汰；现有最终 pass/fail 不足以确定责任层。

4. **重试成本异常高。** Case 02 的三次运行消耗约 571 秒；Case 05 虽有
   judgment，仍多次使用三次读取尝试。现有“仅 timeout 重试”方向正确，但应为
   已知失败 token/同一请求签名设置一次性熔断或 per-document budget，避免一个
   case 侵占整套 8x3 的执行窗口。

5. **控制 case 已给出修复信号，但尚未稳定。** Case07 的主要根因是 Bitable
   parent 长读和线程取消边界；Case08 的主要根因是模型输出枚举漂移与 reason/status
   矛盾。修复后均有一次正确升级，但仍需至少再跑两次并达到 2/3。

## 推荐的修复与复测顺序

1. 补 trace summary 和 tier-by-role 漏斗，不改变 retrieval 策略。
2. 以 Case 01 做空 candidate/dispatch 的最小复现；以 Case 02 做单文档读取
   timeout 复现，分别修复。
3. 在 trace 可归因后，检查 Case 03--05 的 rerank/judgment 是否淘汰互补角色；
   不预设全局 rerank 阈值。
4. 先运行 Case 07--08，再以 `--resume` 补齐其余缺失 runs；重新计算 2/3 gate。
# 2026-07-20 diagnostic update

- Latest Case01 live diagnostic (`tests/artifacts/p3_scope_first_v2_runs_phase4_shortlist_check/v2_pilot_01__run1__deep_dive.json`) completed in 112.05s without timeout. Planner emitted six executable queries; 72 unique rerank inputs became 59 retained chunks. It still failed because the Gold role carrying `hypershell 6,993 / 1,392 (+30.6%)` did not reach final evidence.
- This is not evidence that the role is absent from Feishu: the old partial shortlist was not invoked at all because its trigger was accidentally `more than one 80-chunk batch`. The updated contract triggers at retained pool >=24 and emits a role-stage funnel before any rerank threshold change.
- Live table-child selection occurred for `每日发送热点新闻` / `ON19bSWuhazGe1sUMy0ccuxanYd`: 4 children -> Qwen metadata rerank -> 3 selected. The parent is unrelated to Case01, so this validates only child limiting. Parent relevance is now trace-audited; no hard parent filter has been enabled.
- Formal P3 gate remains failed. The next valid result must use the new artifact fields `evidence_role_funnel`, `read_lineage_events`, `unique_read_chunks`, `unique_documents`, and `multi_query_hits`; a one-run success is diagnostic only, not a 2/3 stability result.
# 2026-07-20 Case01 rerun after admission guard

`tests/artifacts/p3_scope_first_v2_runs_role_funnel_shortlist_guard/v2_pilot_01__run1__deep_dive.json` is a non-timeout 101.27s diagnostic. It reads 33 unique chunks, rerank retains 27, and the empty partial-eval response is now explicitly fail-open (`partial_eval_no_selection`) rather than deleting all 27. Plan-eval judges 8 and global eval retains 6.

The formal one-run result is still fail: the `hypershell` brand-search click-growth role is the only missing final role. The new funnel proves every Case01 role exists at read and rerank; their first missing stage is `shortlist`. This removes candidate, Feishu read, and evidence-rerank threshold tuning from the immediate hypothesis set. The next gate is valid focus-group shortlist production and diversity validation, then repeated Case01 stability runs.

### 2026-07-22 streamed-rerank / mixed-admission diagnostic

- Verified artifact: `tests/artifacts/p3_scope_first_v2_runs_streamed_rerank_8s/v2_pilot_01__run1__deep_dive.json`. Case01 is still a one-run diagnostic failure, but E2E fell to **122.36s** with no transport error. Four Gold roles entered the funnel; three reached final.
- The one missing role is the `hypershell` brand-search click-growth role. Its exact first missing stage is `shortlist`, after candidate, read and rerank success. Partial admission submitted 246 chunks but accepted only 9 model refs from two of four batches, then deleted the remaining 237. This is a faulty admission-union behavior, not a source read failure or a rerank-score calibration signal.
- The corrective runtime now retains model-selected refs and fills unused per-Focus admission capacity with bounded deterministic document diversity. It assigns neither tier nor noise to those guard refs; source `plan_eval` remains the final source evidence judge. The required next run must record a continuous `read -> rerank -> shortlist -> plan_eval -> final` chain for the missing role before this case can enter 2/3 stability testing.
- The artifact also reports one Qwen document group at 10.187s while its client timeout was configured to 8s. Treat that setting as unproven HTTP/socket behavior, not as the required outer wall deadline; Case02 cancellation/drain validation remains open.

### 2026-07-22 Case01 admission-union Gate

- `tests/artifacts/p3_scope_first_v2_runs_admission_union_guard/v2_pilot_01__run1__deep_dive.json` is a non-timeout diagnostic **pass 1/1**, 177.95s E2E. All four approved Gold roles are present in final evidence, including the former `hypershell` brand-search click-growth role.
- The request had no consumable model admission refs. The deterministic diversity admission retained 52 of 67 rerank-retained chunks, then source plan-eval judged/retained the complete chain. This validates the new safety invariant—partial-eval omission cannot sever required evidence—but it is not evidence that the partial-eval model contract is healthy.
- Case01 now needs two further independent runs for its 2/3 stability criterion. Before broad rerank calibration, verify that future artifacts keep the same first-missing-stage attribution and distinguish a Qwen wall timeout from semantic fail-open.

### 2026-07-22 Case01 second diagnostic: source plan-eval deadline

- `tests/artifacts/p3_scope_first_v2_runs_admission_union_guard_v2/v2_pilot_01__run1__deep_dive.json` is timing-inconclusive: 200.72s E2E and Feishu reached its 180s source timeout, while `cancel_drain.remaining_sync_io=0` and no Qwen wall-timeout occurred.
- The model admission contract did produce 24 refs from 84 submitted chunks. Every Gold role survived candidate/read/rerank/shortlist, but none reached source `plan_eval` before the source deadline. This is a source-eval timing bottleneck, not a new semantic role-loss conclusion.
- The subsequent configured calibration is generation trigger 60 (was 80) and per-group keep ratio 70% (was 80%). It introduces no global Qwen score threshold. The next valid live attempt must separately report Qwen generation count, retained pool, source plan-eval completion and first missing role stage.

### 2026-07-22 trigger=60 / keep=70 diagnostic

- `tests/artifacts/p3_scope_first_v2_runs_trigger60_keep70/v2_pilot_01__run1__deep_dive.json` finished in 123.75s with no transport timeout. It confirms the new Qwen generation trigger and 70% group retention are live, but it is a semantic diagnostic failure: only the action-plan Gold role reached final.
- The two partial-eval batches took 20.27s and 29.71s and returned no refs; the deterministic fallback admitted 18 chunks. Affiliate and search-click roles were present after rerank but missing at shortlist. The source plan-eval took 26.55s and incorrectly concluded `upstream_evidence_needed` from that incomplete admission set.
- The next change is a bounded high-lineage document/heading diversity guard. It preserves up to three distinct headings from each of the top three multi-query documents after broad coverage, remains tier-free, and does not use benchmark roles or cross-batch Qwen-score comparison. The next Case01 run must validate that this removes the two shortlist drops before any Case01 stability count changes.

### 2026-07-22 partial-tier contract redesign (live Gate pending)

- The bounded admission/heading-diversity strategy has been removed. Partial eval now emits complete Focus-local provisional tiers for every submitted `(focus_id, chunk_ref)` pair, with no selected-chunk quota and `max_output_tokens=None` at the product configuration layer. `plan_eval` remains the only final tier, evidence-chain, coverage, gap, replan and top-K judge.
- A provider response may remove a chunk only when all of its submitted Focus pairs are explicitly `noise`. An all-noise Focus group must emit `reason=no_qualifying_chunks`. Empty/missing Focus groups, refs or pairs are body-free `partial_eval_contract_error` events and retain the affected chunks for `plan_eval`; they are neither noise nor evidence of upstream absence.
- Funnel terminology is now `partial_tier_retained`. Historical `partial_shortlisted` artifacts remain immutable and cannot be compared as though they implemented the new contract.
- Local regression is **125/125 passed**. The agent's first live dispatch attempt was blocked before external connection by sandbox `WinError 10013`; an escalated retry exceeded the local 60-second command window before producing an artifact. This is transport inconclusive. The user-side command below is the required live Case01 Gate.

### 2026-07-22 partial-tier first live Gate

- `tests/artifacts/p3_scope_first_v2_runs_partial_tier_live/v2_pilot_01__run1__deep_dive.json` is timing-inconclusive: E2E **202.05s**, a 180s source timeout, and clean cancel drain (`remaining_sync_io=0`). The source `plan_eval` began with 63 chunks but did not complete; this is not valid evidence of an upstream gap or a P3 semantic failure.
- Partial itself failed safely rather than deleting evidence: its 92.34s response had zero groups/tier rows, two malformed shape signals, and 111 missing Focus/chunk pairs. All 63 chunks became `partial_eval_incomplete`; three Gold roles reached `partial_tier_retained` and first miss at `plan_eval`.
- The next run adds lossless singleton-array normalization and body-free raw output-shape telemetry. It must establish whether DeepSeek collapsed `focus_groups`/`tiers` arrays or ignored the contract. No fallback shortlist or evidence-rerank threshold is reintroduced.

### 2026-07-22 partial-tier shape v2: semantic chain passed, service reliability inconclusive

- Artifact: `tests/artifacts/p3_scope_first_v2_runs_partial_tier_shape_v2/v2_pilot_01__run1__deep_dive.json`. The semantic Gold chain is complete for every required role, and partial returned valid Focus-local tier groups (88 and 142 tier decisions, no response-shape normalization). Source plan-eval completed in 30.70s and final post-selection global coverage remained `covered` for all three Focuses.
- This is not a formal P3 pass. The request logged three Feishu table `ReadTimeout` failures, had two `rerank_wall_timeout` events, and the final DeepSeek `eval_layer.llm_tool` exhausted three empty-content attempts before deterministic RuleEvalLayer fallback. A preserved chain under fail-open/fallback proves evidence safety only; it cannot count as one of the required 2/3 stable runs.
- Subsequent code records body-free DeepSeek per-attempt/fallback telemetry, Qwen timeout input/drain lineage, group-local Qwen fail-open, and explicit benchmark `inconclusive_reasons`. Future reports must state both `semantic_passed` and formal `passed`.

### 2026-07-22 reliability-trace live verification

- Fresh artifact: `tests/artifacts/p3_scope_first_v2_runs_reliability_trace_v1/v2_pilot_01__run1__deep_dive.json`, E2E 147.47s. Every Gold role completed the final chain and source plan-eval covered the single Focus, so `semantic_passed=true`.
- Formal result remains `passed=false` and `inconclusive=true`: three Feishu table `ReadTimeout` failures, one Qwen `rerank_wall_timeout`, and one retried DeepSeek empty response occurred. New trace correctly records Qwen input 124 / 37,380 chars and clean `rerank_drain=0`, plus DeepSeek final-eval attempt 1 empty after 26.09s and attempt 2 success after 12.11s. The final eval used LLM selection (6/8), not Rule fallback.

### 2026-07-22 Case02 partial-provider exception

- `tests/artifacts/p3_scope_first_v2_runs_case02_io_v1/v2_pilot_02__run1__deep_dive.json` is not a valid Case02 retrieval verdict. A partial-eval provider response was non-JSON reasoning text; its `JSONDecodeError` escaped the batch gather and collapsed the whole Feishu source to zero chunks.
- The defect is fixed as batch-local fail-open: malformed/exceptional partial output records a body-free `partial_eval_provider_error`, then follows ordinary incomplete-pair retention into source plan-eval. It cannot erase read evidence or be converted into `upstream_evidence_needed`. Future malformed bodies are no longer written to runtime logs/artifacts; the historical `io_v1` artifact is retained only as a restricted diagnostic. A fresh Case02 run is required.

### 2026-07-22 Case02 io_v2

- `tests/artifacts/p3_scope_first_v2_runs_case02_io_v2/v2_pilot_02__run1__deep_dive.json` live-verifies the repaired path: Feishu completed source evaluation with 12 retained chunks, all three Focuses covered, and all four Gold roles present through final evidence. No partial-provider exception or source timeout occurred.
- It remains formal-inconclusive because final DeepSeek eval returned empty content on all three calls and RuleEvalLayer performed final ordering. Final ordering now has a two-attempt budget because it cannot alter evidence admission/coverage; after one retry it deterministically falls back rather than adding a third 20--30s tail call.
