# P1 Native-order refactor checklist

## Scope boundary

The runtime path is `build_retrieve_agent -> {FeishuSourceAgent, GoogleSourceAgent}
-> source bounded I/O -> preview/eval -> final assembly`.  P1 removes every
pre-evaluation relevance selector from that path.  API page limits, file-size
gates, timeouts, concurrency limits and stable deduplication remain because
they bound I/O rather than judge relevance.

## One-shot change set

1. Replace every source candidate, tab, Gmail, attachment, type-pool and chunk
   selector with stable API order plus cross-query deduplication.  A source may
   stop reading only at its declared page/read budget; it may not use title,
   snippet, local score or model score to change order.
2. Delete all selector-only code, including unreachable code after early
   returns, compatibility imports and `if False` branches.  No dormant path is
   retained for a future toggle.
3. Remove selector config from `SourceAgentConfig`, preset overrides and the
   effective-config merger.  Keep only explicit search/page/read budgets.
4. Remove `candidate_rerank_*` and `chunk_rerank_*` metadata.  Trace candidate
   records carry only source API rank, document/type and read outcome.
5. Rename or retire legacy eval/report schemas that describe a rerank funnel.
   The benchmark records candidate recall, evidence recall, API rank, reads,
   replan and evaluation judgments instead.
6. Remove non-main-source selector parameters too: Bailian invocation
   overrides, old evaluator configuration, query-expansion/RRF wording and
   sheet-pool selector API.

## Acceptance gates

1. Static: no `dashscope`, `qwen3-rerank`, `rerank-hybrid` or `rerank` remains
   in runtime/config/eval code; historical raw artifacts are excluded by path,
   not by an in-code allowlist.
2. Imports/build: all source modules import; `RetrieveConfig` resolves every
   preset; Google mock agent build has no OAuth requirement.
3. Contract: Feishu primary nonzero never sends fallback; primary zero sends at
   most one alias and one expression query; both retain original anchors.
4. Contract: Google Drive/file/tab/Gmail fixtures preserve API order inside the
   declared budget and deduplicate across sub-queries.
5. Regression: preview/full, batch/ledger and trace tests pass; a real Feishu
   trace contains only query/candidate/read/judgment events and has no selector
   score/rank metadata.

## Completion evidence (2026-07-14)

- `tests.test_no_legacy_selector`, Google smoke, profile and context tests: 16 passed.
- `compileall` and `git diff --check`: passed.
- Authorized live `real_q01`: two native API searches read 63 and 69 document
  chunks plus 2 table chunks; preview/full evaluation selected 6 final chunks.
  Its old sequence-based evidence IDs no longer match live content-hash IDs;
  that truth-label migration is P3 work, not a selector regression.
