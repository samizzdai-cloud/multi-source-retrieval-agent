# Google Workspace Source — 架构规格（v3.0）

> 文档版本：**v3.0**  
> 同步日期：2026-05-11  
> 适用代码：`agents/google/` + `tools/google/` + `google_workspace_tool/`

---

## 1. 定位

`GoogleSourceAgent` 是 retrieve_agent 多源链路下的 Google Workspace 适配器，覆盖：
- **Drive 原生** (native)：Google Docs / Sheets / Slides
- **Drive 非原生** (nonnative)：xlsx/csv（Excel family）、pptx（Slide family）、docx/doc/txt（Doc family）
- **Gmail**：thread + 邮件正文（可选附件）

数据流：5 路并发 search → qwen rerank candidate → 跨 sq merge → 4 type pool 并发 read → BM25 / qwen rerank chunk → eval → top_k。

> **Google 与 Feishu 的根本差异**：Google Sheets v4 提供 `spreadsheets.values.batchGet`，**一次 API 拿全部选中 ranges**——没有 feishu 那种 `find_cells × N keyword` 串行 API 问题。

---

## 2. 调用链概览

```
sub_query (from global_planner v2.2，含 query_type / time_filter / google_enable_fulltext / google_order_by / author_filter)
  │
  ▼ GoogleSourceAgent.run(focuses, pre_planned_sub_queries=...)
  │   ↓ _plan 直通 (pre_planned 短路，无 plan_only_LLM)
  │   ↓ _execute_batch
  │
  ▼ Phase A: 5 路并发 search (per sub_query)
  ▼ Phase B: 收集 spreadsheet → batch metadata prefetch (1 RPC)
  ▼ Phase B2: 初始化 DocPool / SlidePool / GmailPool / sheet_nonnative_pool
  ▼ Phase C: per-sq qwen rerank 选 candidate (sheet/doc/slide/gmail 各自)
  ▼ Phase D: 跨 sq 合并 selections（file_id 去重）
  ▼ Phase E: 4 type pool 并发 read (sheet / sheet_nonnative / doc / slide / gmail)
  ▼ Phase F: 收 chunks per sub_query
  ▼ Phase G: per-sq chunk 级 qwen rerank → top_n
  ▼ chunk_pool
  ▼ eval_only LLM → top_k tier 评分
  ▼ SourceAgentResult(top_chunks)
```

---

## 3. Phase A: 5 路并发 search

`_search_one(sq)` 并发发起 5 个 Drive search 任务（`asyncio.gather`，单路异常不阻塞其他）：

| 通道 | mime types | 数量配置 | 备注 |
|---|---|---|---|
| **doc** | `Docs` + (option) `Word`/`Word_old`/`Txt` | `google_doc_search_per_sq`（lite=8 / balanced=10 / deep=16） | nonnative 受 query_type gate |
| **slide** | `Slides` + (option) `Pptx` | `google_slide_search_per_sq`（8/10/15） | nonnative 受 query_type gate |
| **sheet** | `Sheets` + (option) `Excel`/`CSV` | `google_sheet_search_per_sq`（5） | nonnative 受 query_type gate |
| **sheet_sidecar** | sheet mimes 但 `enable_fulltext=False` | 固定 3 | 抓 title-only 命中 |
| **gmail** | — | `google_max_gmail_threads_per_subquery`（lite=2 / balanced=3 / deep=10） | `fetch_snippets=True` 直接拉 snippet |

### 3.1 关键词扩展层

`generate_grouped_keywords(sq.query)`（`tools/core/kw_generalization.py`）+ `_expand_bilingual`（中英月份/季度同义词扩展）。

### 3.2 query 构造

`_build_search_q(query, keyword_groups, enable_fulltext, time_filter, author_filter, mime_types, max_groups)`：
- `enable_fulltext=True` → Drive `fullText contains ...`
- `enable_fulltext=False` → Drive `name contains ...`（title-only fallback / sidecar）
- `time_filter` 形如 `'>2025-09-01'` → 转 `modifiedTime > '...'`
- `author_filter='me'` → `'me' in owners`

### 3.3 Fallback 链

- **fb1**：某通道 0 结果 → 用 `max_groups=2` 重发（更松的 AND）
- **fb2**：fb1 仍 0 → `keyword_groups=None`（仅 mime + 基础 query）

`query_type=="personal_recent"` 时 Validator 已注入：
- `google_enable_fulltext=False`
- `google_order_by="viewedByMeTime desc"`

实际值：
```python
enable_fulltext = sq.google_enable_fulltext if sq.google_enable_fulltext is not None else cfg.google_enable_fulltext_search  # default True
order_by        = sq.google_order_by      if sq.google_order_by      is not None else cfg.google_order_by_mode
order_by_param  = order_by if not enable_fulltext else None       # 全文模式不传 order_by
```

---

## 4. Phase B: Sheet Metadata Batch Prefetch

```python
sheet_pool.batch_fetch_metadata(
    all_spreadsheet_ids, creds,
    native_api_timeout=int(cfg.google_api_timeout_s),
    max_workers=8,
    batch_timeout_s=max(5.0, min(source_timeout_s * 0.25, api_timeout_s + 5.0)),
)
```

**一次 RPC**（concurrent.futures × 8 worker）拿所有 spreadsheet 的 `tabs/sheetId/title/rowCount/columnCount`。后续 Phase E 读 sheet 时**复用此缓存**，无需重发 metadata API。

---

## 5. Phase B2: Pool 初始化

| Pool | 文件 | 装的什么 |
|---|---|---|
| `SheetPool` | `google_workspace_tool/sheet_pool.py` | native sheet 元数据 + tab 列表 + 读后 chunks |
| `DocPool` | `google_workspace_tool/doc_pool.py` | native Docs + word/docx/txt 元数据 |
| `SlidePool` | `google_workspace_tool/slide_pool.py` | native Slides + pptx 元数据 |
| `sheet_nonnative_pool` | DocPool 实例 | xlsx/csv 元数据（与 sheet family 分离） |
| `GmailPool` | `google_workspace_tool/gmail_pool.py` | thread 元数据（subject/snippet/sender/date） |

所有 Pool 进程级单例（per Source Agent），跨 sub_query 缓存。

---

## 6. Phase C: Per-sub_query qwen rerank（candidate 级）

每个 sub_query 对各类型独立筛选：

| 类型 | 函数 | 选 top |
|---|---|---|
| Sheet（native） | `sheet_pool.select_sheets_for_sub_query(top_n=google_sheet_candidates_per_sq)` | balanced 3 / deep 9 |
| Sheet（nonnative） | passthrough（保留全部 file，无 rerank） | — |
| Doc / Word / Txt | `select_reranked_chunks(top_n=google_doc_candidates_per_sq)` | lite 5 / bal 6 / deep 9 |
| Slide / Pptx | `select_reranked_chunks(top_n=google_slide_candidates_per_sq)` | lite 3 / bal 4 / deep 9 |
| Gmail | `select_reranked_chunks(top_n=google_gmail_rerank_top_n)` | 2-3 |

> 候选 rerank input 是 (title + heading + snippet + Format tag) 的 cheap doc，单次 LLM 调用 ≤500ms。

---

## 7. Phase D: 跨 sub_query 合并

```python
merged_sheets         = SheetPool.merge_selections(all_sheet_selections)         # file_id → set(sq_ids)
merged_sheet_nonnative= sheet_nonnative_pool.merge_selections(...)
merged_docs           = doc_pool.merge_selections(...)
merged_slides         = slide_pool.merge_selections(...)
merged_gmail          = gmail_pool.merge_selections(...)
```

同一 file_id 若被多个 sub_query 选中，**只读一次**，结果广播给所有 sq。

---

## 8. Phase E: 4 type pool 并发读

文件：`agents/google/google_source_agent.py::_execute_batch` (lines ~1822-2400)

### 8.1 Type pool workers（balanced）

| Pool | workers | per_file_timeout |
|---|---|---|
| `sheet` | 8 | min(20s, source_timeout_s) |
| `doc` | 3 | 同上 |
| `slide` | 5 | 同上 |
| `nonnative` (pptx/word/excel/csv/txt 合并) | 5 | 同上 |
| `gmail` | 2 | 同上 |

### 8.2 Size gate（balanced）

| 文件类型 | 上限（bytes） | 超限处理 |
|---|---|---|
| native Doc | 45_000_000 | 跳过 |
| native Slide | 55_000_000 | 跳过（`google_enable_native_slide_size_gate=True`） |
| pptx | 35_000_000 | 跳过 |
| word/docx | 30_000_000 | 跳过 |
| pptx download | timeout 5s | 流式打断 |
| word download | timeout 7s | 流式打断 |

### 8.3 各类型读 API（**全部是单 API call，无 per-keyword loop**）

| Type | API | 行为 |
|---|---|---|
| **native Sheet** | `spreadsheets().values().batchGet(ranges=[selected_tabs])` | **1 次 API 拿全部 selected tabs** |
| **native Doc** | `documents().get(documentId=...)` | 1 次拿整文档 |
| **native Slide** | `presentations().get(presentationId=...)` | 1 次拿整 deck |
| **xlsx/csv** | `files().get_media(fileId=...)` | 二进制下载 → pandas 本地解析 |
| **pptx** | 同上 → python-pptx 解析 | |
| **word/docx** | 同上 → python-docx 解析 | |
| **txt** | 同上 → 纯文本 | |
| **Gmail thread** | `users().threads().get(id=...)` | 1 次拿 thread + 所有 message |

> **没有 find_cells 等价物**。Google Sheets v4 设计上批量查询是一等公民。

### 8.4 File cache（LRU，跨 query）

```python
_FILE_CONTENT_CACHE: Dict[str, (timestamp, gc_list)]
_FILE_CONTENT_CACHE_LOCK = threading.Lock()
TTL = cfg.google_file_content_cache_ttl_s    # 300s
MAX = cfg.google_file_content_cache_max_files  # 64
```

Cache key = `(file_id, mime, sheet_titles_sorted_tuple)`。命中时**跳过 read_drive_chunks**，直接复用 raw GoogleChunk 列表（再走一次 query-aware scoring）。

---

## 9. Phase F + G: Chunk 收集与 rerank

### 9.1 收集

```python
chunks_by_sq[sq.sub_query_id].extend(uc_list)
```

按 sub_query 分桶。同一 file 被多 sq 选中时，chunks 广播给每个 sq。

### 9.2 Phase F-G: type-pool budget rerank

当某 sub_query 内某 type 的 chunks 数超过 `google_type_bucket_rerank_threshold=25`，触发**emergency rerank**：
- 输入：该 type pool 全部 chunks
- 输出：top `google_type_bucket_rerank_top_n=7`（gmail=4）
- 通过 qwen rerank 收紧（按 type 单独限）

### 9.3 Phase G: per-sq chunk rerank

```python
selected = _select_reranked_chunks(
    query=sq.query,
    chunks=chunks_by_sq[sq.sub_query_id],
    top_n=cfg.sub_query_top_n,   # lite=8 / bal=10 / deep=14
)
```

最终每个 sub_query 出 top_n chunks 入 source 级 chunk_pool。

---

## 10. nonnative 家族 gate（family-level）

文件：`tools/core/models.py::SourceAgentConfig.google_nonnative_search_query_types`

```python
{
    "sheet": {"structured", "personal_recent"},
    "slide": {"structured", "concept", "personal_recent"},
    "doc":   {"structured", "concept", "personal_recent"},
}
```

某 family 的 nonnative 文件**只在** `query_type ∈ 白名单`时被检索。例：
- `query_type=concept` → sheet family 不召回 xlsx/csv（concept 类不需要表格）
- `lite` preset 覆盖：slide family 直接 `set()` 即 PPTX 全关；sheet family 也关
- `balanced` 覆盖：slide family 关 PPTX（实测 PPTX 占 51% 文件读时长，关掉省 3-5s）
- `deep_dive` 覆盖：全 family × 全 query_type 全开

---

## 11. Family 分类 (`_family_by_mime`)

| Family | Native mime | Nonnative mimes |
|---|---|---|
| `sheet` | `Sheets` | `Excel`, `Excel_old`, `Excel_macro`, `CSV` |
| `slide` | `Slides` | `Pptx` |
| `doc` | `Docs` | `Word`, `Word_old`, `Txt` |

**Native ↔ nonnative dedup**：同一 family 内 native 与 nonnative 候选若 title 一致，去重保 native。

---

## 12. Preset 矩阵

文件：`tools/core/models.py::_PRESET_DEFAULTS`

| 维度 | lite | balanced | deep_dive |
|---|---|---|---|
| `max_loops` | 1 | 1 | 1（覆盖 LOOSE=2，eval_layer 兜底）|
| `source_top_k` (base) | 5 | 5 | 8 |
| `sub_query_top_n` (chunk rerank top) | 5 默认 | 10 | 14 |
| `google_download_timeout_s` | 6 | 7 | 8 |
| `google_api_timeout_s` | 6 | 7 | 8 |
| `google_per_file_timeout_s` | 7 | 9 | 10 |
| `google_native_only` | True | False | False |
| `google_enable_sheet_search` | False | True | True |
| `google_enable_gmail_attachments` | False | True | True |
| `google_doc_search_per_sq` | 8 | 10 | 16 |
| `google_slide_search_per_sq` | 8 | 10 | 15 |
| `google_doc_candidates_per_sq` | 5 | 6 | 9 |
| `google_slide_candidates_per_sq` | 3 | 4 | 9 |
| `google_sheet_candidates_per_sq` | 3 | 3 | 3 |
| `nonnative_gate.slide` | set() | set() (off PPTX) | 全开 |
| `nonnative_gate.sheet` | set() | structured + personal_recent | 全开 |
| `nonnative_gate.doc` | structured + concept + personal_recent | 全开 | 全开 |
| `type_pool_sheet_workers` | 4 | 8 | 5 |
| `type_pool_slide_workers` | 5 | 5 | 4 |
| `type_pool_doc_workers` | 3 | 3 | 4 |
| `type_pool_nonnative_workers` | 4 | 5 | 5 |
| `type_pool_gmail_workers` | 2 | 2 | 3 |
| Size gate (native Doc) | 45MB | 45MB | 50MB |
| Size gate (native Slide) | 55MB | 55MB | 60MB |
| Size gate (pptx) | 35MB | 35MB | 38.5MB |
| Size gate (word) | 30MB | 30MB | 33MB |

---

## 13. plan_only / eval_only LLM（仅 fallback / replan 调用）

v2.2 主路径 **不调** plan_only（pre_planned 直通）。仅在 v1 fallback / planner_runner reject 某 focus 时调用。

`eval_only` 每次 run 末必调，prompt 共享 `EVAL_RUBRIC_BLOCK`（含 multi-focus guard）：

```python
output_schema=build_eval_only_model(source_top_k + MULTI_FOCUS_TOP_K_EXTRA(=10))
```

schema cap 膨胀，运行时按 `effective_source_top_k=base+(n_focuses-1)*2` 截断。

### Google 特有 eval prompt 段

```
## Google 源额外提示
当 focus 只写月份但没有年份时，按 current_date 推断业务年度；
明显历史年份的数据行（例如 2023-3）不能评为 critical，除非 focus 明确要求该年份。
```

post_process_top_chunks 还在 source agent 内对 stale year 行 demote critical → important。

---

## 14. 凭证

```python
# tools/core/models.py::SourceConfig
google_credentials_path: str = ""   # OAuth2 client credentials.json
google_token_path: str = ""         # OAuth2 token.json
```

构建期 `retrieve_agent` 调 `get_credentials(_SCOPES)` 预先刷新 token，确保 OAuth2 有效后才注入 `GoogleSourceAgent`。

---

## 15. 限速观察

- Drive v3 search: 1000 req/100s/user —— 远大于 feishu，5 路 × N sub_query 并发不触限
- Sheets v4 batchGet: 60 req/100s/user/读写 —— payload 大时是瓶颈，不是次数
- Slides v1 get: 60 req/100s/user —— 单文件大时主要瓶颈
- Gmail threads.get: 250 quota units/user/s —— 极少触限

**整源 timeout** `source_timeout_s` (lite=25 / balanced=30 / deep_dive=45) 是兜底。Phase E read 是主消耗，期内的 worker pool 各自限并发。

---

## 16. 改动 Changelog

| 版本 | 关键改动 |
|---|---|
| v2.7 (2026-05-07) | 5 路并发 search + X1 cross-sq pre-filter + 跨 query LRU cache |
| v2.8 (2026-05-07) | family 化（native ↔ nonnative dedup + Format tag rerank）+ Phase E 消除重复 metadata API + Phase B metadata 字段精简 |
| v2.9 (2026-05-08) | 每源独立 ThreadPoolExecutor (`DEFAULT_EXECUTOR_WORKERS=20`)，避免与 feishu / bailian 共抢全局池 |
| **v3.0 (2026-05-11)** | **planner v2.2 接入**：`run(pre_planned_sub_queries=...)` 直通，跳过自己 plan_only_LLM；删冗余字段 `google_enable_fulltext` / `google_order_by` 从 `PlannedSubQuery`，改由 `GoogleValidator` 按 `query_type=='personal_recent'` 推断；schema cap 膨胀 +10 给 multi-focus 留预算 |

---

## 17. 已知限制

- `LC1BsAqWph5DxstT` 这类特定 sheet 在 feishu 端慢，**Google 端没有等价问题**（batchGet 单 API call）
- PPTX 大文件下载是常见尾延迟来源；balanced preset 默认 PPTX off，deep_dive 全开
- nonnative excel/csv 解析靠 pandas (`engine='xlrd'`)，超大文件 (>30MB) 易 OOM/慢，受 size gate 兜底
- LRU cache 跨 query 命中率取决于 query 主题相似度；冷启动无加速
- Drive search `q=` 字符串长度有上限（约 2000 char），过多关键词会被截
