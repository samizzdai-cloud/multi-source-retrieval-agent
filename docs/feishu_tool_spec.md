# Feishu Source — 工具与流程规格（v5.1）

> 文档版本：**v5.1**  
> 同步日期：2026-05-11  
> 适用代码：`agents/feishu/` + `tools/feishu/` + `feishu_read_tool/`

---

## 1. 定位

`FeishuSourceAgent` 是 retrieve_agent 多源链路下的 feishu 适配器，对接：
- **doc**：飞书云文档（docx）—— 通过 `block_api` 或 `MCP fetch-doc` 读取
- **table**：spreadsheet (`SHEET`) + 多维表 (`BITABLE`)
- **wiki**：知识空间节点（搜索阶段融合，无独立读取路径）

数据流：search → child select → read → chunk → eval → top_k。

---

## 2. 调用链

```
sub_query (from global_planner v2.2，已带 query_type / time_filter / author_filter)
  │
  ▼ FeishuSourceAgent.run(focuses, pre_planned_sub_queries=...)
  │   ↓ _plan 直通 (pre_planned 短路，无 plan_only_LLM)
  │   ↓ _execute_batch
  │
  ▼ _execute_batch_lite (preset=lite)    或   _execute_batch (balanced/deep_dive)
  │
  ┌─── lite 路径（极速，MCP-only + 分流）────────────────────────┐
  │ 1. 每个 sub_query 并发 search_feishu_docs (count=20)         │
  │ 2. 跨 sq 去重 → 截到前 LITE_DOC_CAP=8 篇                       │
  │ 3. 4+4 分流：前 4 → MCP fetch-doc；后 4 → block_api          │
  │    block_api 走 lite_block_only=True（跳过 IO/comments）       │
  │ 4. DocPool 并发读 → per-sq wait & rerank → top_n             │
  │ 不启动 table 流水线，structured 降级 concept                   │
  └──────────────────────────────────────────────────────────────┘

  ┌─── balanced / deep_dive 路径（doc + table 双管） ────────────┐
  │ 1. 每个 sub_query 并发跑：                                     │
  │    - search_and_select_feishu_doc_candidates                  │
  │    - search_and_select_feishu_table_candidates (structured)   │
  │ 2. doc 路径：候选 rerank → _assign_doc_route_hints            │
  │    （multi-hit + top1 走 block_api，其余 MCP）→ DocPool 读     │
  │ 3. table 路径：parent_select → child_select (qwen rerank)     │
  │    → _trim_table_parents(target=6) → _read_table_one          │
  │ 4. per-sq _wait_doc + _wait_table 并行收 → rerank → top_n     │
  └──────────────────────────────────────────────────────────────┘
  │
  ▼ chunk_pool
  ▼ eval_only LLM (按 effective_source_top_k 选 top_k 并打 tier)
  ▼ SourceAgentResult(top_chunks)
```

---

## 3. Doc 路径

### 3.1 Search

| 函数 | 文件 | 主要 API |
|---|---|---|
| `search_feishu_docs` | `feishu_read_tool/search.py` | MCP `search-doc` (`open.feishu.cn/mcp`) |
| `search_feishu_mcp_pure` | 同上 | MCP `search-doc`（personal_recent 专用） |
| `search_and_select_feishu_doc_candidates` | `tools/feishu/feishu_retrieve.py` | 上层 wrapper：search + rerank + edit_time 过滤 |

### 3.2 Route Hint（balanced/deep_dive）

`_assign_doc_route_hints(doc_list, min_block=2)`：

| 决策 | route_hint |
|---|---|
| `multi-hit ≥ 2 个 sq` 或 `_is_top1=True` | `block_api`（高价值 doc，深度解析） |
| 保底至少 `min_block=2` 篇 | `block_api`（按 score 降序补） |
| 其余 | `mcp`（MCP fetch-doc，浅解析） |
| `personal_recent` 类型生成的 doc | 强制 `mcp` |

### 3.3 Lite 4+4 分流（lite-only）

```python
# agents/feishu/feishu_source_agent.py::_execute_batch_lite
_LITE_DOC_CAP = 8
_LITE_MCP_TOP_N = 4
for idx, cand in enumerate(doc_list):
    cand["route_hint"] = "mcp" if idx < _LITE_MCP_TOP_N else "block_api"
```

设计意图：
- MCP 与 block_api 走**不同限速桶**（MCP 是 mcp.feishu.cn 自己的 QPS；block_api 是账号 99991400 共享 5 qps）
- 4+4 把负载拆到两个桶，避免单桶集中触限速
- block_api 半路走 `lite_block_only=True`，**不调** type30/53/43 的扩展 API 与 `get_doc_comments`，把 99991400 全留给主路径 `list_document_blocks`

### 3.4 Read：FeishuDocPool

文件：`tools/feishu/feishu_doc_pool.py`

| 参数 | 值 | 作用 |
|---|---|---|
| `MCP_CONCURRENCY` | 6 | MCP fetch-doc 信号量 |
| `BLOCK_API_CONCURRENCY` | 4 | block_api 信号量 |
| `MCP_MAX_ATTEMPTS` | 2 | MCP 失败重试次数（初次+1） |
| `MCP_RETRY_BACKOFF_S` | 0.8 | MCP 失败固定退避 |

两条 read 路径：

**`_read_mcp_batch`** → MCP fetch-doc（per-doc 一次 `mcp.feishu.cn/mcp/tools/call`）  
**`_read_block_api_batch`** → `run_rag_pipeline`（docx_pipeline）

```python
PipelineConfig(
    max_candidates=1,
    skip_scoring=True,
    lite_block_only=self._lite_block_only,   # ← lite preset 期 True
)
```

### 3.5 `lite_block_only=True` 行为（`feishu_read_tool/docx_pipeline.py`）

```python
# Pass 1b: IO blocks（type30 sheet / type53 bitable / type43 whiteboard）
if cfg.lite_block_only:
    _IO_BLOCK_TYPES = set()    # 跳过
else:
    _IO_BLOCK_TYPES = {30, 53, 43}

# Comments fetch + injection
if not cfg.lite_block_only:
    io_tasks.append(("comments", ...))
    ...
    _inject_comments(...)
```

**实际意味**：
- `list_document_blocks` 主路径仍跑（拿 block 树 + 内联文本）
- type30 sheet / type53 bitable / type43 whiteboard 这些"嵌入式 IO block" → 不调任何扩展 API
- `get_doc_comments` 跳过

---

## 4. Table 路径（structured 类型 query）

### 4.1 Search + Selection

文件：`tools/feishu/feishu_retrieve.py::search_and_select_feishu_table_candidates`

```
Step 1: search_feishu_tables (MCP search-doc，page_size=20)
Step 2: _dedupe → top {direct, wiki}（lite/balanced=3, deep_dive=6）
Step 3: 并发 _fetch_table_structure × N parents（table_pool 缓存）
        - SHEET: query_all_sheets_metadata 拿 tab 列表
        - BITABLE: list_tables 拿 table 列表
Step 4: 把所有 parent 的 children 合并 → qwen rerank 全局 top-K
        TABLE_CHILD_SELECT_GLOBAL_TOP_K = 10
        TABLE_CHILD_SELECT_TOP_K = 2  (per parent cap)
Step 5: 按 parent 分桶，每 parent 最多 2 个 child
Step 6: annotate parent.selected_sheet_ids / selected_table_ids
```

### 4.2 Trim + Merge

`_trim_table_parents(prepared, target=6, min_per_sq=2)`：跨 sq 去重 + 截到 6 张 parent。

### 4.3 Read：`_read_table_one`

文件：`agents/feishu/feishu_source_agent.py::_execute_batch`

```python
async def _read_table_one(cand):
    if doc_type == "SHEET":
        chunks = await self._to_thread(read_sheets, read_query, [cand], uat)
    elif doc_type == "BITABLE":
        chunks = await self._to_thread(read_bitables, read_query, [cand], uat)
    table_chunks_by_token[token] = normalized
    event.set()
```

#### Sheet Reader 快速路径（`feishu_read_tool/sheet_reader.py::read_sheets`）

```python
selected_sheet_ids = set(candidate.get("selected_sheet_ids") or [])
if cached_tabs is not None and selected_sheet_ids:
    candidate_tabs = [t for t in cached_tabs if t.sheet_id in selected_sheet_ids]
    # ↑ 只读 selected 的 ≤2 个 tab
with ThreadPoolExecutor(max_workers=min(len(candidate_tabs), 5)) as ex:
    futures = [ex.submit(_read_single_tab, ...) for tab in candidate_tabs]
```

#### `_read_single_tab` 路径

```python
if row_count <= SMALL_TAB_ROW_THRESHOLD(=50):
    # 小表：1 次 read_sheet_range
    rows = client.read_sheet_range(range_str)
else:
    # 大表：find_cells 定位 + read_sheet_range 拿全量
    # ★ find_cells 现在并行（ThreadPoolExecutor max=4） ★
    with ThreadPoolExecutor(max_workers=min(len(keywords), 4)) as kw_ex:
        for fut in as_completed({kw_ex.submit(client.find_cells, ...): kw for kw in keywords}):
            ...
    rows = client.read_sheet_range(range_str)  # 1 次拿全量
    sections = _find_sections_around_hits(rows, hit_rows_set)
```

**find_cells 并行收益**：3 个 keyword 从串行 ~18s 降到 ~6s（wall-clock = max 单次）。实测单 sheet 47s → 17.5s（-63%）。

### 4.4 Wait

```python
async def _wait_table():
    for tid in t_ids:
        await asyncio.wait_for(table_events[tid].wait(), timeout=doc_pool_timeout)
        collected.extend(table_chunks_by_token[tid])
```

> 注：这里是 for-loop 顺序等待，但每个 event 是后台并发触发的。`doc_pool_timeout = max(8, source_timeout_s * 0.8)`。

---

## 5. 限速桶（Feishu 服务端硬约束）

### 5.1 99991400 共享 5 qps 桶（账号级硬限）

文件：`feishu_read_tool/rate_limiter.py::DOC_API_BUCKET`

| API | 优先级 |
|---|---|
| `list_document_blocks` (docx v1) | **P_BLOCK_API=0**（关键路径） |
| `get_whiteboard_nodes` (board v1) | `P_WHITEBOARD=10` |
| `get_doc_comments` (drive v1) | `P_COMMENT=20` |

**进程级单例 PriorityTokenBucket(qps=5)**。多客户端实例共享。

### 5.2 不在共享桶里的 API（独立 QPS）

| API | 桶 |
|---|---|
| `read_sheet_range` (sheets v2) | 实测 50 并发全 200，独立 |
| `find_cells` (sheets v2) | 同上独立（**可放心并行**） |
| `bitable/v1/.../records/search` | 独立 |
| MCP `search-doc` / `fetch-doc` (`mcp.feishu.cn`) | MCP server 自有 QPS |

### 5.3 错误码

| 错误码 | 出处 | 含义 |
|---|---|---|
| `99991400` | docx 共享桶 | "request trigger frequency limit"（账号级） |
| `99992402` | sheet metadata batch_query | field validation failed（token/doc_type 必填） |
| `[NETWORK:5002]` | MCP fetch-doc | 服务端转发的 frequency limit（包了一层） |

---

## 6. 块类型映射（Stage 4 Block Type）

文件：`feishu_read_tool/docx_pipeline.py`

| block_type | 含义 | 额外 API | API | 在 99991400 桶？ |
|---|---|---|---|---|
| 2/12/13/14/15/17/22 | 文本/标题/列表/代码/quote/todo | ❌ | — | — |
| 30 | embedded Sheet | ✅ | `sheets/v2/.../values/{range}` | ❌ |
| 31, 32 | inline table | ❌ | block flat fields | — |
| 40 | Mermaid 流程图 | ❌ | `block.add_ons.record` 内联 | — |
| 43 | Whiteboard 画板 | ✅ | `board/v1/whiteboards/{id}/nodes` | ✅ P_WHITEBOARD |
| 53 | Bitable embed (`reference_base.token`) | ✅ | `bitable/v1/apps/{at}/tables/{tid}/records/search` | ❌ |
| 18 | Bitable embed (旧 SDK) | ✅ | 同 53 | ❌ |
| Doc-level | comments | ✅ | `drive/v1/files/{tk}/comments` | ✅ P_COMMENT |

`lite_block_only=True` 时，type 30/43/53 + comments **全部跳过**，仅保留主 block 树读取。

---

## 7. 关键常量

| 常量 | 值 | 含义 | 文件 |
|---|---|---|---|
| `TABLE_CHILD_SELECT_GLOBAL_TOP_K` | 10 | 全局 child rerank top-K | `tools/feishu/feishu_retrieve.py` |
| `TABLE_CHILD_SELECT_TOP_K` | 2 | 每 parent 最多保留的 child | 同上 |
| `SMALL_TAB_ROW_THRESHOLD` | 50 | ≤50 行直接全量读 | `feishu_read_tool/sheet_reader.py` |
| `MAX_TABS_PER_SHEET` | 5 | 单 spreadsheet 兜底 cap | 同上 |
| `READ_MAX_ROWS` | 500 | 单次 read_sheet_range 行上限 | 同上 |
| `_LITE_DOC_CAP` (lite) | 8 | 跨 sq 截到 8 篇 | `agents/feishu/feishu_source_agent.py` |
| `_LITE_MCP_TOP_N` (lite) | 4 | 4 篇走 MCP，其余 block_api | 同上 |
| `MCP_CONCURRENCY` | 6 | MCP 信号量 | `tools/feishu/feishu_doc_pool.py` |
| `BLOCK_API_CONCURRENCY` | 4 | block_api 信号量 | 同上 |

---

## 8. 凭证 / Config

```python
# tools/core/models.py::SourceConfig
feishu_uat: str = ""            # 用户访问令牌（FEISHU_UAT env）
feishu_open_id: str = ""        # 用户 open_id（personal_recent / author_filter='me' 用）
```

构建期（`retrieve_agent._build_feishu_source_agent`）：
- `FeishuDocPool(uat, lite_block_only=(preset=="lite"))`
- `TablePool(uat)`
- 注入到 `FeishuSourceAgent(uat, doc_pool, table_pool, preset)`

`FeishuSourceAgent.__init__` 把 `_executor`（源专属 ThreadPool=16）注入给两个 pool。

---

## 9. Preset 行为差异

| 维度 | lite | balanced | deep_dive |
|---|---|---|---|
| 执行路径 | `_execute_batch_lite` | `_execute_batch` | `_execute_batch` |
| Doc route | 4 MCP + 4 block_api（`lite_block_only`） | block_api 选高价值 + MCP 兜底 | 同 balanced + 多 loop |
| Table 流水线 | **OFF**（structured → concept 降级） | ON | ON |
| `source_top_k` (base) | 5 | 5 | 8 |
| `max_loops` | 1 | 1 (默认) | 2 |
| `source_timeout_s` | 25 | 30 | 45 |
| direct/wiki search limit | 3/3 | 3/3 | 6/6 |

> **effective_source_top_k** 在 run() 内动态算 `base + (n_focuses_routed - 1) * 2`。multi-focus 自动扩容。

---

## 10. 改动 Changelog

| 版本 | 关键改动 |
|---|---|
| v4.0 | doc/table 独立流水 + qwen rerank child select |
| v5.0 (2026-05-08) | FeishuDocPool 抗频控（99991400 共享桶 + 优先级 + 退避） |
| **v5.1 (2026-05-11)** | **lite 4+4 路由分流** + **`lite_block_only`**（跳过 IO/comments）+ **`sheet_reader.find_cells` 并行**（max=4，N keyword 并发） + planner v2.2 接入（`run(pre_planned_sub_queries=...)`） |

---

## 11. 已知限制

- `_api_call_with_backoff max_attempts=5`：最坏退避 25s。本案没触发；未优化。
- 单 sheet 内 `find_cells × keywords` 并行后下限 = max(单次 find_cells RTT) ≈ 5-7s（受服务端响应限制，无法再压）。
- BITABLE search_records 不支持游标分页 cap（page_size=50 后截断）。
- Wiki search 与 direct search 分两条通道：wiki 节点解析需 `get_wiki_node_info` 二次 API（命中后才发）。
- balanced 长 query 偶发 35s（`plan_eval` 多 loop LLM RTT 累计）。
