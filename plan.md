# Feishu Retrieval Main-Path Consolidation Plan

## 0. Status, scope, and implementation gate

This is the authoritative implementation and acceptance ledger for the current
retrieval redesign.  It records target behavior, observed code baseline,
dependencies, tests, and phase gates.  A decision in this document is not
implemented until its phase is approved, code is changed, and acceptance tests
pass.

Immediate scope: `user query -> Global Planner -> Planner Runner -> Feishu
Source Agent -> document/table I/O -> source evaluation -> final selection ->
asynchronous Profile governance`. Other sources must retain compatible
contracts but are not the primary implementation target in this cycle.

Implementation has been approved by the owner. Each subsequent code change
still requires the phase-level audit and acceptance checks below.

### Execution snapshot — 2026-07-17

- Phase 0 baseline audit: complete. The full unit suite now passes (55 tests)
  after the implemented slices; the worktree was already materially dirty and
  has been preserved.
- Phase 1: implemented and unit-tested. Global Planner no longer emits/parses
  `query_intent`; `read_gap` has been removed from runtime and benchmark
  contracts. Internal source compatibility defaults remain intentionally local.
- Phase 2: core Feishu SourceAgent request boundaries implemented: documents=4,
  structured tables=3, and table route caps=3. Live Feishu verification is
  still pending.
- Phase 3: first materialization slice implemented: Sheet section refinement,
  explicit table previews, lineage metadata, and reader-side BM25 bypass. The
  >500 request-local mapping/final hydration policy remains pending.
- Phase 5: durable body-free shadow-report persistence and offline Delta
  extraction are implemented in a separate governance SQLite store. The
  external entrypoint is `python -m tools.core.profile_governance_runner`:
  `extract` is stateless for manual testing; `run-once` advances only a
  governance cursor and is safe for Task Scheduler. Scheduled holdout runs and
  automatic approved writes remain deliberately unimplemented.

### Live smoke result — 2026-07-17

Real UAT run, Feishu-only and retrieve-only, used `Doc2_Q1` (numeric document
query) and `Sheet1_Q1` (structured-sheet query) for `lite`, `balanced`, and
`deep_dive`. Artifacts are under `eval/live_smoke_feishu_20260717_elevated/`.

| Preset | Doc2_Q1 | Sheet1_Q1 | Finding |
|---|---:|---:|---|
| lite | 0 final chunks | 0 final chunks after 50 reads / 5 judgments | document MCP read path works, but final eval rejected the selected evidence |
| balanced | 0 final chunks | 0 final chunks after 45 reads; source timeout | structured-table route returned no parents; document I/O exceeded the 45 s source deadline |
| deep_dive | 2 final chunks | 0 final chunks | one document path completed end-to-end; Sheet doc/table search returned zero candidates |

This is a transport and runtime-path smoke result, not a recall-quality score.
The next implementation gate is to isolate table-search API availability from
planner query quality, then make structured queries use the intended table path
in every preset and calibrate source timeout against post-I/O rerank/eval cost.

### E2E result — 2026-07-17

User-authorized Feishu-only E2E was run for the numeric Z80 Ultra / Campaign
ROI query and the Z80 Ultra KOL summary query across all three presets. All
six runs reached real retrieval, but no answer is valid: the configured
AnswerAgent is hard-coded to Gemini and this environment has no Gemini API key
(`No API key was provided`). This is an AnswerAgent configuration blocker, not
a Feishu UAT failure. Artifacts are in `eval/live_e2e_feishu_20260717/`.

Additional runtime findings: two `lite`/`balanced` runs crashed inside
`tiers_list_to_map` because an eval `tiers` item was a string rather than an
object; KOL in balanced/deep_dive exceeded the 45 s Feishu source timeout; and
the numeric query's planner semantic check dropped one or more required
sales/ROI focuses. These must be fixed before treating E2E answer quality as a
meaningful metric.

### Retrieve-only regression after tier guard — 2026-07-17

The user-authorized follow-up ran the Z80 Ultra numeric query and `Hypershell
主打产品相对竞品的核心卖点是什么？不同国家市场有区别吗？` across all presets with a
test-only Feishu source timeout of 90 s. Artifacts are in
`eval/live_retrieve_after_fixes_20260717/`.

| Preset | Z80 numeric | Hypershell comparison |
|---|---:|---:|
| lite | 3 final chunks, 110.2 s | 5 final chunks, 39.7 s |
| balanced | 0 final chunks, 42.6 s | 0 final chunks, 48.7 s |
| deep_dive | 2 final chunks, 103.9 s | 0 final chunks, 48.9 s |

The 90 s source budget eliminated source-timeout cancellation in this run, but
it also showed that total E2E retrieval time can exceed it because global
planning/eval sit outside the source budget. The prior tier crash was a
runtime-contract bug: DeepSeek occasionally returned non-object entries inside
`tiers`; the adapter only JSON-decoded and assumed schema enforcement, then
called `.get()` on a string. `tiers_list_to_map` now skips malformed entries
and a unit contract test covers it. This is not a retrieval-chain loss or a
ranking strategy decision. Remaining zero-chunk cases are different: their
eval/plan-eval labeled evidence as noise or returned IDs whose lifecycle and
short-reference handling required a follow-up correction; see the session sync
below.

### Session synchronization — 2026-07-18

- **Feishu short-reference contract:** source eval, plan-eval and cross-source
  global eval render request-local `C1/C2/...` references and resolve only to
  the current pool's canonical IDs. Exact known canonical IDs are tolerated as
  provider compatibility; unknown IDs remain rejected.
- **Noise lifecycle is now decision-aware:** a `continue` branch prunes
  uncovered noise before replan. A `stop` branch first consumes that same
  plan-eval result, records `selected_noise_excluded`, and never reports an ID
  deleted by the noise policy as an LLM hallucination. Eval-only noise remains
  judgment trace only and cannot reach source result, global eval or user
  context.
- **Live smoke:** `E2E_Hypershell_Comp/deep_dive`, Feishu-only, retrieve-only,
  90-second source budget, artifact
  `eval/live_retrieve_noise_exit_fixed_20260717/`. It completed in 72.09 s,
  with 65 reads and 26 judgments, no source timeout and no stale-ID
  hallucination. Eight plan-eval noise selections and two eval-only noise
  selections were explicitly excluded; final payload was correctly empty.
- **Interpretation:** this is now an evidence-quality/search problem, not an
  eval-ID contract problem. The read documents were unrelated RAG/product
  methodology material, not Hypershell competitor/country evidence. Do not
  relax the noise rubric to manufacture a non-empty answer.
- **Next implementation gate:** persist pre/post rerank and pre/post eval tier
  histograms plus `selected_noise_excluded` in the trace, then diagnose Feishu
  entity/title/body matching and candidate rank on the same E2E cases.

### 0.1 Mandatory pre-implementation audit

Before every phase, inspect the current working tree and the actual call graph;
do not assume this plan still reflects the code. Record baseline behavior and
dependencies in the phase PR/commit notes before editing.

| Area | Files / dependency to inspect | Baseline to verify |
|---|---|---|
| Global planning | `retrieve_agent.py`, `tools/core/global_planner.py`, `tools/core/planner_runner.py`, contracts | planner contract, fallback, retry and v1 compatibility paths |
| Feishu search | `feishu_read_tool/search.py`, `tools/feishu/feishu_retrieve.py`, `agents/feishu/feishu_source_agent.py` | MCP/Direct/Wiki route, request count, candidate order and query-type routing |
| Table orchestration | `tools/feishu/table_pool.py`, `rate_limiter.py` | parent dedupe, structure cache, all-child selection, semaphores, token buckets, wall timeouts |
| Spreadsheet reader | `feishu_read_tool/sheet_reader.py` | tab discovery, Find API, blank-row sections, merge fill, header detection, row filtering |
| Bitable reader | `feishu_read_tool/bitable_reader.py` | table discovery, field normalization, record fetch limits, record filtering |
| Table utility legacy path | `feishu_read_tool/table_handler.py`, `docx_pipeline.py` | which pure helpers are reusable and which old `Chunk`/Docx semantics must not enter the new path |
| Context and final selection | `tools/core/chunk_preview.py`, `context_budget.py`, `eval_layer.py`, `final_assembler.py` | preview/full use, token budgets, final hydration behavior |
| Profile governance | `tools/core/gtm_profile.py`, trace emitters, tests | profile lookup/injection, fallback variants, trace persistence and writer state |

Preconditions for modifying a phase:

1. Preserve unrelated dirty-worktree changes.
2. Freeze representative offline fixtures before changing semantics.
3. Record a bounded live-Feishu baseline when credentials are available.
4. No new external model/API dependency is introduced without an explicit
   decision and a fallback behavior.

## 1. Confirmed target contracts

### 1.1 Planner and replan

The Global Planner outputs only executable planning data:

- `focuses`: independently verifiable answer tasks;
- subquery fields supported by the target source: `target_source`, `query`,
  `query_type`, `time_filter`, and `author_filter`.

It does not output evidence-role taxonomy, answer text, rerank configuration,
read budgets, or Profile patches. User entities, metrics, dates, regions, and
requested output constraints are preserved.

There are only two replan meanings:

1. `upstream_evidence_needed`: existing evidence cannot support the requested
   answer; issue a new executable query for the missing upstream evidence.
2. `query_refinement`: a bounded entity-preserving query variant is justified
   after the primary search has no candidates.

`read_gap` is removed. Selected documents and table children are read in the
first pass; a later replan cannot be used to compensate for skipped reading.
`plan_eval` returns per-focus `status`, `gap_type`, and `reason`; a `continue`
branch contains executable source fields only.

Planner retry policy target: at most two retries after the initial request,
within a 30-second total planner budget. Retry transient transport or empty
output only; schema and semantic failures use deterministic recovery/fallback,
not repeated LLM calls.

### 1.2 Feishu search and bounded I/O

- Every subquery performs Feishu document search through MCP with `count=4`.
- `personal_recent` retains owner/time filters but also uses MCP `count=4`.
- `structured` additionally performs Direct API table search with `count=3`
  for Sheet and Bitable; other query types do not start table search.
- Wiki table search is default-on, runs alongside Direct API table search, and
  is locally type-filtered to `SHEET`/`BITABLE` before stable dedupe and merge.
- Search count is a hard request budget. Cross-subquery dedupe reduces duplicate
  I/O; it never launches compensating replacement searches.
- A selected table parent exposes all structural children. Existing TablePool
  cache, single-flight behavior, source semaphores, token buckets and timeouts
  stay in place unless measurements prove a necessary change.

### 1.3 Table materialization and lineage

The system unifies the *contract*, not the physical semantics of Sheet and
Bitable:

```text
Sheet parent -> tab child -> one or more logical sections
Bitable parent -> inner-table child -> one logical table unit
```

For every table unit, the reader produces a stable identity and a request-local
mapping of normalized rows/records. Evaluation receives a deterministic preview;
the final path can identify the corresponding original unit/range without new
remote I/O.

- Sheet sections: preserve existing blank-row boundaries, then optionally refine
  a blank-delimited block on repeated headers, column-count changes, and
  subtotal rows. This is an enhancement, not a replacement of Sheet's
  merge-aware header logic or Find API hit handling.
- Bitable: do not invent semantic sections. Its inner table remains the logical
  child; large record sets may be physically paged with stable record ranges.
- A physical page is not a semantic section. Paging repeats the normalized
  header and uses a stable range suffix.
- Reader-internal BM25 row/record pruning is removed. It conflicts with
  post-I/O evidence evaluation and makes `content` falsely look complete.
- A unit up to 500 rows/records may expose full normalized content. Above 500,
  the active evaluation/answer payload is preview-only; the complete normalized
  material remains in a request-local mapping with `content_mode=preview_only`.
  No full >500-row body is silently placed in an LLM prompt. The exact policy
  for programmatic final range hydration is a gated decision in Phase 3.

Minimal answer-path chunk fields:

- `chunk_id`, `source`, `content_type` (`document` or `table`), `content`,
  `preview_content`;
- lineage in metadata: `document_id`, `parent_id`, `child_id`, `range`,
  `content_mode`, `focus_ids`, `sub_query_ids`, source name and heading.

`preview_content` and `content` always identify the same unit/range. For a
preview-only unit, `content` must not masquerade as complete original content.

### 1.4 Profile usage and governance

`data/gtm_profile.sqlite` is the active Profile store. Its approved contents
are bounded query candidates only, never facts/evidence: `entity`,
`entity_alias`, `expression_set`, and derived statistics. The legacy
`user_profile.json` has been removed; the standalone search experiment no
longer consumes it.

Profile use has two online points:

1. **Raw-query bootstrap**: a bounded lexical MemoryPack is injected into the
   Global Planner prompt as query candidates, never facts.
2. **Plan-scoped vocabulary resolution**: after Planner creates `focus` and
   `subquery`, the resolver may derive at most one alias and one expression
   replacement, preserving source, region, dates, metrics, and all other query
   anchors. They execute only if that primary search returns zero candidates.

The resolver follows existing Planner intent through `focus`, `subquery`,
`target_source`, `query_type`, `time_filter`, and `author_filter`; it does not
need new Planner output fields and does not alter a focus's scope/breadth.

Matching policy:

- exact normalized entity/alias/expression/variant matching is the default;
- a unique, bounded typo correction may be attempted only after zero candidates;
- broad semantic Profile expansion is not online-default. It may first run in
  shadow mode as an `Intent-to-Vocabulary` candidate and can be promoted only
  through Delta and holdout evidence.

Governance lifecycle:

```text
body-free trace -> durable shadow record -> Delta candidate -> frozen holdout
Profile off/on test -> validated callback -> atomic SQLite Profile patch
```

Only a Profile-derived fallback with `primary_candidates=0`, fallback candidate
gain, and subsequent retained evidence can produce a Delta candidate.
`plan_eval.query_refinement`, generic planner rewrites, and ordinary replan
never train Profile.

Online retrieval never writes Profile. Offline evaluation is triggered daily
or when 200 new eligible fallback observations accumulate, with a single-flight
run lock. It may write only after all gates pass; an approved write is visible
to the next request, not retroactively to an in-flight plan.

An LLM/Agent is not required for Profile writes. Deterministic extraction,
dedupe, validation, holdout comparison, and callback own write authority. A
future constrained Agent may propose a patch, but cannot persist it directly.

### 1.5 Rerank

Post-I/O rerank remains where presets currently enable it. It never filters
search candidates, table parents, table children, or reader rows before the
bounded I/O contract completes. `rerank_score_threshold` remains unset until
calibration; scores are comparable only within one `focus x modality x
provider-batch` request.

## 2. Observed baseline and optimization objective

| Area | Observed baseline | Optimization objective |
|---|---|---|
| Global plan | Profile Pack is passed as `session_context`; current planner still has historical query-intent fields and no explicit retry loop | executable-only contract, deterministic recovery and bounded retry |
| Feishu docs | MCP plus Wiki document path, counts currently differ from target | one MCP count=4 policy, stable merge and trace |
| Feishu tables | Direct plus Wiki table route; Wiki needs local type filtering; existing route counts differ | structured-only Direct=3 plus default Wiki table participation |
| TablePool | parent dedupe/cache, all-child selection, read semaphores already exist | preserve these controls; measure before changing limits |
| Sheet Reader | blank-row sections, Find API for large tabs, merge/header logic; BM25 prunes rows >40 | enhance section boundaries, retain original row mapping, remove BM25 prune |
| Bitable Reader | inner-table child, type flattening, contains/fallback record fetch, BM25 prunes records >40 | preserve field semantics, retain record mapping, remove BM25 prune/payload ambiguity |
| table_handler | active for Docx embedded/native tables; only `header_coverage` reaches Sheet path | extract pure helpers, never import old `Chunk`/Docx pipeline into Feishu table path |
| Preview | generic builder supports `metadata.table_preview`, but Feishu table readers do not populate it | deterministic table preview from normalized material |
| Profile | SQLite lookup and zero-candidate variants exist; shadow observation is in-memory only | durable trace/Delta/holdout loop and plan-scoped resolver observability |

## 3. Required implementation phases

### Phase 0 — Baseline audit and fixture freeze

1. Execute the audit in Section 0.1 and record exact current request counts,
   reader limits, timeout values, and active preset branches.
2. Freeze unit fixtures for document, small Sheet, Sheet with multiple logical
   subtables, large Sheet with Find hits, Bitable with rich fields, Bitable with
   large records, and zero-candidate Profile fallback.
3. Capture an opt-in, bounded live-Feishu baseline for quality/latency only.
4. Identify existing dirty changes that overlap planned files; do not overwrite
   them without explicit reconciliation.

Gate: fixtures and baseline observations exist; no semantic implementation is
started before this gate passes.

### Phase 1 — Planner and replan contract cleanup

1. Remove `read_gap` from plan-eval instructions, schemas, validators, traces,
   labels and reports.
2. Remove planner-facing evidence role/query-intent requirements; keep only
   executable subquery fields.
3. Implement the bounded retry/recovery policy and distinguish:
   transport/empty output, schema invalidity, semantic invalidity, and fallback.
4. Ensure plan-eval expresses replan reason through `gap_type`, not a hidden
   subquery intent field.

Dependencies: Phase 0 planner fixtures; preserve force-fallback compatibility.

Acceptance:

- no runtime/benchmark enum accepts `read_gap`;
- a valid planner result needs only executable fields;
- retry executes at most twice only for allowed failures;
- invalid fields cannot reach source I/O;
- fallback and local recovery are trace-distinguishable.

### Phase 2 — Feishu search-budget and candidate-route convergence

1. Set MCP document count=4 for all Feishu document query types.
2. Set Direct table count=3 for structured subqueries only.
3. Keep Wiki table search default-on; locally filter Wiki outputs to Sheet/Bitable
   and merge with Direct results in explicit stable order.
4. Preserve time/owner filters in the search request. Do not add compensating
   searches after dedupe.
5. Emit body-free search trace fields: requested count, returned count, route,
   local type-filter result, deduped parent count, and selected child count.

Dependencies: Phase 1 executable `query_type`; existing TablePool budget and
rate limits remain unchanged.

Acceptance:

- docs=4 and structured tables=3 are verified at the request boundary;
- non-structured subqueries never invoke Direct table search;
- Direct and Wiki table outputs are type-safe and stably merged;
- dedupe only eliminates duplicate reads, never triggers a replacement call.

### Phase 3 — Feishu Table Reader convergence

1. Extract reusable pure functions from existing readers/handler into a small
   shared utility boundary: value flattening, blank-column trimming, Markdown
   rendering, header-carrying physical pages, section refinement and preview
   construction. Do not migrate old `Chunk`, Docx block IDs, or fixed 20-row
   semantics into the main table path.
2. Sheet: retain Find API, merge fill and merge-aware header detection. Apply
   `table_handler`-style section refinement only within existing blank-delimited
   blocks and preserve exact original row ranges.
3. Bitable: retain field-schema headers and rich value formatting. Keep inner
   tables as logical units; add stable record-id/range bookkeeping rather than
   semantic section splitting.
4. Remove reader-internal BM25 row/record pruning. Preserve API/read limits as
   I/O limits, not relevance filters.
5. Build deterministic `table_preview` from normalized material: unit identity,
   description/header, sample rows, terminal/subtotal rows where applicable,
   and existing Find-hit context. Ensure preview and original unit map one-to-one.
6. Implement the >500 physical-output rule with `content_mode`, request-local
   material mapping, and tests. Decide before code whether final assembly may
   programmatically hydrate a selected bounded range; it must never re-read
   Feishu or send the whole oversized unit to an LLM.

Dependencies: Phase 2 trace and TablePool behavior; normalized chunk contract
must be designed before final-assembler changes.

Acceptance:

- Sheet blank/duplicate-header/column-change/subtotal fixtures get expected
  section ranges without false splits;
- Bitable preserves field values and stable record range/IDs;
- no Sheet/Bitable output reports BM25-pruned content as a full unit;
- each table chunk has deterministic preview, parent/child/range and mode;
- all selected structural children remain read under existing limits;
- >500 rows never enter LLM context as a full body.

### Phase 4 — Chunk, context, and selection compatibility

1. Normalize `content_type` and table lineage at reader-to-ChunkEntry boundary.
2. Update preview consumers so eval/rerank/final selection use `preview_content`
   and modality fields rather than ID-string heuristics.
3. Preserve full original content only for eligible units; keep preview-only
   material out of answer payload unless the approved local range-hydration
   rule applies.
4. Verify preset behavior: deep-dive retains global evaluation; lite/balanced
   retain their cross-source top-k selector under the same preview/full contract.

Dependencies: Phase 3 chunk data; context budget behavior is regression-tested.

Acceptance:

- document, Sheet and Bitable lineage survives merge/dedupe/final assembly;
- no preview is mistaken for complete original material;
- token batchers retain stable IDs and do not inspect oversized full content;
- preset-specific final selection has unchanged source/top-k boundaries except
  for intentional contract corrections.

### Phase 5 — Durable Profile governance and resolver observability

1. Persist body-free trace records to a dedicated governance store, separate
   from `gtm_profile.sqlite`. Store request/run ID, source, focus, subquery,
   route, candidate counts, fallback origin, evidence outcome and terminal
   status; never store document body text.
3. Make Profile fallback origin explicit (`alias`, `expression`, `typo`, future
   semantic-shadow) so Delta extraction can distinguish it from generic replan.
4. Implement deterministic Delta extraction for the eligible zero-primary /
   fallback-gain / retained-evidence pattern. The current external extractor
   records candidate gain only; verified retained-evidence remains a required
   holdout join, so extraction alone cannot qualify or write a patch.
5. Implement an offline single-flight runner: daily or after 200 eligible new
   observations. It generates candidate patches, runs frozen Profile off/on
   holdout tests, records decisions, then uses the existing validated callback
   for atomic Profile mutation.
6. Keep plan-scoped resolution bounded. Exact lexical matches are active; typo
   candidates are zero-candidate-only; semantic candidates are shadow-only until
   separately approved.
7. Add read-after-write verification: a committed patch must appear in the next
   request's MemoryPack and Planner prompt, and can create only bounded fallback
   variants for matching planned subqueries.

Dependencies: Phases 1-2 trace fields; an explicit storage/retention decision.

Acceptance:

- answer latency and success do not depend on governance completion;
- trace survives ordinary worker shutdown; cancellation is recorded distinctly;
- no Profile patch is possible without Delta and holdout gates;
- generic plan-eval refinement cannot become a Profile item;
- next-request prompt injection and zero-candidate variant behavior are proven
  with integration tests;
- an Agent, if introduced, can propose but never directly write a patch.

### Phase 6 — Benchmark, load test, and calibration

1. Freeze query-first Feishu cases against evidence features and lineage, not
   fragile historical chunk IDs.
2. Report separately: planner validity, doc candidate recall, table parent/child
   coverage, section/range correctness, evidence-chain completeness, source
   tier quality, final selection, timeout class, Profile fallback gain and false
   expansion.
3. Run MCP and Direct concurrency tests separately from retrieval-quality tests.
   Step load: 1, 2, 4, 6, 8 concurrent requests, fixed fixtures, three runs per
   level; stop on material rate-limit/error/p95 regression.
4. Run rerank off/on paired tests only after Phases 1-4 stabilize. Calibrate any
   threshold only from same-group score distributions and erroneous-drop review.
5. Run Profile holdout off/on tests only after Phase 5 has sufficient eligible
   Delta candidates; never use the same cases to propose and approve a patch.

Gate: no threshold or automated Profile write is enabled by a single successful
live query; report both gains and regressions.

## 4. Required test matrix

| Area | Required assertions |
|---|---|
| Planner | preserves user anchors; executable fields only; retry/fallback classes are distinct |
| Plan eval | only two gap types; replan queries are executable; no `read_gap` |
| Feishu document search | MCP count exactly 4 for concept, structured and personal_recent |
| Feishu table search | only structured triggers Direct count=3; Wiki is enabled and locally type-filtered |
| Candidate merge | stable order; dedupe saves I/O without replacement search |
| TablePool | parent dedupe, all-child selection, semaphore/token-bucket and timeout behavior survive |
| Sheet Reader | blank sections, repeated headers, col changes, subtotals, merged cells, Find-hit ranges |
| Bitable Reader | rich fields, stable table/record identity, contains/fallback fetch, oversized record pages |
| Table material | no BM25 content pruning; preview/full mapping; >500 mode and no full-body leak |
| Context/final | preview-only evaluation, stable IDs, modality from field, preset selector compatibility |
| Profile read path | exact raw-query pack is injected; planned-subquery variants preserve anchors; zero-primary condition enforced |
| Profile governance | body-free durable trace; eligible Delta only; holdout gate; atomic read-after-write; typo/semantic shadow boundaries |

### Live Feishu run record

Every opt-in live run records: planner focus/subquery count; request/returned
candidate counts by MCP/Direct/Wiki route; parent/child/read counts; section/page
counts; preview/full mode distribution; rate-limit/timeouts; source tiers; final
chunks; end-to-end latency; and Profile fallback origin/outcome when applicable.

## 5. Open decisions requiring owner approval before the relevant phase

1. Governance storage location and retention period for trace, Delta, holdout,
   and write-decision records.
2. Exact final-assembly policy for a selected >500-row unit: preview-only
   response, or programmatic hydration of a bounded original range.
3. Whether/when a semantic Intent-to-Vocabulary candidate moves from shadow-only
   to an executable zero-candidate fallback.
4. The objective thresholds for concurrency stop conditions and Profile holdout
   acceptance after baseline data is collected.
