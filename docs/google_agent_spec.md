# Google Workspace Source Agent Architecture Specification

> **v2.7（2026-05-07）综合性能优化已落地**——平均耗时 34.5s → 23.6s（−32%），critical 召回 +60%，noise −80%。
> 详见 §6 / §7 / §8 / §9 / §10。

## 1. 架构总览与组件映射
Google Workspace Source Agent 作为连接全局 Retrieve Agent 和 Google 庞大生态（Drive、Gmail 等）的桥梁，负责将高维的检索意图转化为具体的 API 请求，并将复杂的异构数据格式清洗、分块、重排序后输出标准 Chunk。

核心映射组件如下（基于 Vertical DDD 架构）：
- **Agent 层**：由垂直目录 `agents/google/` 提供强内聚的认知层支持。
  - `google_source_agent.py`：其中的 `GoogleSourceAgent` 继承自 `SourceAgent` 基类，作为纯粹的工作流引擎，提供标准的 Plan-Eval-Loop 并发控制骨架。
  - `google_retrieve_llm_build.py`：将 Google 源的特有 Pydantic Schema（如 `google_enable_fulltext`）与 Prompt 彻底隔离，通过 Factory Hook 组装供 Agent 调用的 LLM 大脑。
- **Tool 检索与分发层**：`tools/google/google_retrieve.py`。统一的调度入口，封装了对 Drive SDK 和 Gmail SDK 的并发请求，同时处理了搜索参数设定与文件候选项粗排（Candidate Reranking）。
- **解析与分块层 (Reader & Chunker)**：以 `google_workspace_tool/drive_content_reader.py` 为路由，按 MIME Type 路由给下游的 Reader（如 `doc_reader.py`, `sheet_reader.py`, `gmail_rag_pipeline.py` 等），这些 Reader 会完成底层数据的读取和最初始的文本切分，返回特定类型的块结构。
- **精排与过滤层**：利用 `tools/core/rerank.py`，结合传入的 Target Focus 在分块级别做语义重排，确保提取出的片段与真实意图高度匹配。

## 2. 全链路核心流程图与架构对比
从系统派发带有 `target_focus` 意图的 `SubQuery` 到最终输出统一结构的高优 `ChunkEntry` 序列，底层存在复杂的 IO 交互和参数流转。

下面是涵盖 Google Workspace 和 Feishu 的底层流转对比模型（基于真实代码调用链构建）：

```text
[Global] RetrieveAgent 
      │
      ▼  (1) Dispatch SubQuery(query, target_focus)
[Agent] SourceAgent (agents/google/google_source_agent.py)
      │
      ├──▶ [Stage 0: LLM Plan] (via google_retrieve_llm_build.py)
      │     │-- Generate SubQuery with google_enable_fulltext / google_order_by
      │
      ├──▶ [Stage 1: Search & Pre-Filter]
      │     │-- Invoke Platform API (google_retrieve / feishu_retrieve)
      │     │-- API Returns Raw Candidates (Doc ID, Title, Snippet)
      │     └── Core Reranker Trims Top-K Candidates (by File Title vs Query)
      │
      ├──▶ [Stage 2: Concurrent Read & Chunking (IO Bound)]
      │     │-- ThreadPool / Async Dispatch to Type-Specific Readers
      │     │    * Google: drive_content_reader.py routes by mimeType
      │     │    * Feishu: docx_pipeline / table_pool routes by obj_type
      │     └── Returns List[UnifiedChunk] (Text & Markdown Tables)
      │
      ├──▶ [Stage 3: Semantic Chunk Reranking]
      │     │-- select_reranked_chunks(chunks_text vs focus_text)
      │     └── Returns Filtered & Scored Top-N Chunks
      │
      ▼  (10) Yield List[ChunkEntry] with standardized metadata
[Global] RetrieveAgent
```

## 3. 详细流程分解：参数流转与双系统对照

### 环节 1：Focus 接收与任务入口
- **入口函数**：`execute_sub_query_full(sq: SubQuery) -> List[ChunkEntry]`。
- **参数流转**：
  - `sq.query`：用于向外部 API 提交字面匹配检索词。
  - `sq.target_focus`：作为后续 Reranker 精排对比时的“靶向基准（Baseline）”。Agent 会尝试提取 `focus_text` 的详尽描述。
- **对齐关系**：
  - **Google**：在 `agents/google/google_source_agent.py` 内部接收后直接传递给 `google_retrieve`。
  - **Feishu**：在 `agents/feishu/feishu_source_agent.py` 中有相同的接收签名，传递给 `tools/feishu/feishu_retrieve.py`。

### 环节 2：平台并发检索与候选粗排 (Candidate Select)
- **机制与数据流**：
  将传入的 `query` 下发给平台提供的全文搜索 API。由于云端系统庞大，不能将所有匹配的文档全部下载解析。因此在拿到初步列表（常为 `List[Dict]`，含有 ID、标题、摘要）后，执行内存级的 `select_search_candidates`（即 Reranker 的标题级预筛选）。
- **对齐关系**：
  - **Google (`tools/google/google_retrieve.py`)**：
    1. 前置授权检查：`retrieve_agent` 启动时若启用 Google 源，会立即通过 `auth_utils.get_credentials` 验证 Token，确保终端弹出授权流。
    2. 调用 Drive SDK：根据 `enable_fulltext_search` 决定使用全文检索 (`fullText contains`) 还是标题检索 (`name contains`)，且支持按最近阅读时间 (`viewedByMeTime desc`) 等强时效性字段排序。只请求最核心的属性 `fields="id, name, mimeType, description, modifiedTime, createdTime, viewedByMeTime, owners"`。
    3. 调用 Gmail SDK：通过 `users().threads().list` 搭配 `messages().get`。如果不需解析附件，会请求极简的 `format='metadata'` 仅获取 Headers 与 `snippet`。
    4. 调用 `_select_drive_candidates`，结合时间与作者等元数据丰富 Candidate Text 后，送入 LLM Reranker 进行智能筛选。
    5. 对 Sheets 类文件，额外使用 `_select_sheet_ranges` 对内部 sheet_name 进行一次预筛。
  - **Feishu (`tools/feishu/feishu_retrieve.py`)**：
    1. 调用 飞书 Search API 或 MCP 工具，根据传入的 `file_type` 过滤知识库、文档或表格。
    2. 直接将飞书返回的 title、summary 封装，同样交给 `select_search_candidates` 做粗排淘汰，极大减小 `docx_pipeline` 获取完整 Block 树的压力。

### 环节 3：IO 密集型的自适应并发读取 (Read & Chunking)
- **机制与并发**：
  这部分是系统的性能瓶颈区，需要将之前选中的 Top-K 文档 ID 全部拉回正文，并在拉取过程中完成拆分（Chunking），以适配大模型的 Context 窗口。主要采用 `ThreadPoolExecutor` 或者 asyncio 协程池以 `doc_id` 粒度进行并发。
- **对齐关系**：
  - **Google (`google_workspace_tool/drive_content_reader.py`)**：
    利用 `read_drive_chunks` 按 `mimeType` 动态路由。
    - **API 调用**：Word/Excel/PPTX 采用 `requests` 字节流直接下载，Google Docs/Slides 调用原生 SDK 解析结构（去除删除线等）。
    - **Chunk 切分**：依赖 `chunkers/md_chunker.py` 里的 `_bin_pack_segments`。
    - **格式统一**：嵌套表格会被压扁为 `[cell:A1{text}]` 的格式，超大表格会在 `md_chunker` 中沿用行分割+首行带入策略。
  - **Feishu (`feishu_read_tool/docx_pipeline.py`)**：
    - **API 调用**：依赖 `ListDocumentBlockRequest` 递归提取 block 树，以及利用 IO 池并行获取 Bitable / Sheet 附件和评论 (`get_doc_comments`)。对于表格走 `read_sheet_range`。
    - **Chunk 切分**：依赖 `feishu_read_tool/chunker.py` 的 `build_text_chunks`。同样使用 `_bin_pack_segments` 进行 Heading 切分。
    - **格式统一**：嵌套表格原本强制 `\n` 拉平。大表格切分由 `table_handler.py` 的 `_split_rows_into_chunks` 处理。目前两者的渲染表现与逻辑已强行对齐。

### 环节 4：语义精排与质量拦截 (Rerank Selection)
- **机制与数据流**：
  从各大 Reader 取回的数据已经是标准化的 `List[UnifiedChunk]` 列表（通常可能有几十上百块）。此时我们将这些 chunk 的纯文本内容（`chunk.text`）送往 `select_reranked_chunks` 核心模型 API。
  比较参数是：`query=focus_text` vs `chunks=[payload.text, ...]`。
- **对齐关系**：
  - **Google**：`agents/google/google_source_agent.py` 中的 `_select_chunks_for_sub_query` 调用。通过设置 `score_threshold`（比如 0.18 或更高）动态抛弃灌水无用的块。若 Rerank 调用失败或结果为空，则具有 `fallback_select` 保底策略（按自带权重截取）。
  - **Feishu**：`agents/feishu/feishu_source_agent.py` 使用与 Google 完全相同的重排核心逻辑和配置基准。两边的 Token 开销预估、算分方式保证了最终选出的结果具有同等公信力。

### 环节 5：输出映射与跨代理流转
- 选中的 `UnifiedChunk` 会通过 `_unified_to_chunk_entry` 打包为 `ChunkEntry` 暴露给主控 RAG 循环。
- ID 溯源策略统一为 `[source_tag]:[doc_id_or_url]:[chunk_offset_hash]`，其中 Google 的 `source_tool` 会带上如 `google`, `gmail`，飞书则带上 `feishu` 以便追踪。

## 4. Agentic 设计：Plan, Eval 与 Loop 机制
Google Agent 在保持高度流式并发的同时，凭借 `SourceAgent` 基类提供的架构设计，支持智能修正机制。这一切由 `SourceAgentConfig` (如 `max_loops` 控制) 调度执行。

- **Plan-Only Agent (检索前置规划)**：
  在主链路发起前，如果系统识别出这是一个复杂的模糊需求，`plan_only_agent` 会基于用户原始 Context 分析 Google Workspace 域内最适合的实体词，自行推导最佳查询策略。
- **Eval-Only Agent (基于 Focus 的评判)**：
  在 `ChunkEntry` 组装完成之后返回全局 Agent 之前，`eval_only_agent` 可介入进行自我评估：这些片段究竟有没有命中 `focus_text` 指示的内容？还是只是触发了泛滥的关键词？根据严格打分生成 `EvalResult`。
- **Plan-Eval 迭代微调 Loop**：
  若 Eval 判定当前检索结果未达及格线，且 `max_loops > 1`，引擎将启动反馈环。`plan_eval_agent` 依靠上一轮的失败分析（如“命中邮件，但缺乏附件数据”），动态调整检索策略并重新构造更精确的 `query` 下发给底层重试。这种带记忆的试错环显著拉高了针对 Google 源海量碎片数据的召回质量天花板。

---

## 5. v2.7 性能优化实测基线（2026-05-07）

balanced preset 4-query 实测（详见根目录 `balanced_4q_final.log`）：

| Query | elapsed | tier 分布 |
|---|---|---|
| Q1（DTC GMV）| 22.0s | important:1, noise:1 |
| Q4（KOL 合作）| 25.8s | **critical:2, important:3** ⭐ |
| Q44（11 Pro 销量）| 22.5s | **critical:3, important:2** ⭐ |
| Q48（竞品格局）| 23.9s | **critical:3, important:2** ⭐ |
| **avg** | **23.6s** | **c=8 / i=8 / n=1** |

理论 floor ≈ 19s（plan_only 1.4s + Phase E+F 9s + eval_only 4s + 其他 4.6s）。当前 23.6s 已逼近物理下界。

## 6. 双 preset 对照

balanced 与 lite 的核心差异（`tools/core/models.py`）：

| 配置项 | balanced | lite | 说明 |
|---|---|---|---|
| `google_download_timeout_s` | 7.0 | 6.0 | 二进制下载 socket 上限 |
| `google_api_timeout_s` | 7.0 | 6.0 | 原生 API socket 上限 |
| `google_per_file_timeout_s` | 9.0 | 7.0 | 单文件 wait_for_done 兜底 |
| `google_pptx_download_timeout_s` | 7.0 | 6.0 | pptx 独立 timeout |
| `google_word_download_timeout_s` | 7.0 | 6.0 | word/docx 独立 timeout |
| `google_native_only` | False | **True** | lite 跳过 pptx/docx/excel/txt/csv |
| `google_enable_gmail_attachments` | True | **False** | lite 仅 gmail 正文 |
| `google_doc_search_per_sq` | 10 | 8 | Drive list page_size |
| `google_slide_search_per_sq` | 10 | 8 | 同上 |
| `google_doc_candidates_per_sq` | 5 | 4 | rerank 后保留数 |
| `google_slide_candidates_per_sq` | 4 | 3 | 同上 |
| `google_sheet_candidates_per_sq` | 4 | 3 | sheet tab 数 |
| `google_max_gmail_threads_per_subquery` | 3 | 2 | gmail 召回上限 |
| `google_gmail_rerank_top_n` | 3 | 2 | gmail rerank 后保留 |

- balanced：质量优先，留 docx/excel/pptx 通道兜底
- lite：速度优先，原生文件路径 + gmail body only，平均省 −2~3s

## 7. v2.7 关键流程改造（与 §3 对照）

### 7.1 Phase A：5 路并发搜索

`agents/google/google_source_agent.py:_search_one`：

```python
results = await asyncio.gather(
    asyncio.to_thread(_drive_call, q_doc, doc_search_per_sq, order_by_param),
    asyncio.to_thread(_drive_call, q_slide, slide_search_per_sq, order_by_param),
    asyncio.to_thread(_drive_call, q_sheet, sheet_search_per_sq, order_by_param),
    asyncio.to_thread(_drive_call, q_sheet_sidecar, 3, order_by) if q_sheet_sidecar else asyncio.sleep(0, result=[]),
    asyncio.to_thread(_gmail_call),
    return_exceptions=True,
)
```

- 5 路并发：search_one 单 sq 从 4-7s（串行）压到 1.5-3s
- `return_exceptions=True`：单路失败不阻塞其他通道
- fallback1（max_groups=2）/ fallback2（raw_query）改为按需 gather（仅对 0 召回通道触发）
- broad fallback：total < 3 时跨 mime 一次广义搜索（page_size=20）

### 7.2 Phase A 关键词扩展层

`tools/core/kw_generalization.py` + `_expand_bilingual()`：

```
plan_only LLM 输出：'nubia UK DTC March GMV'
       │
       ▼
generate_grouped_keywords：
  [['nubia'], ['UK'], ['DTC'], ['March'], ['GMV']]
       │
       ▼
kw_generalization 同义词扩展（含 marketing umbrella、社媒 platform umbrella）：
  [['nubia'],
   ['UK', 'britain', 'england', 'united kingdom', '英国'],
   ['DTC', 'direct', 'direct-to-consumer', '官网', '独立站', ...],
   ['March'],
   ['GMV', 'revenue', '销售额', '营收', ...]]
       │
       ▼
_expand_bilingual（月份/季度自动 OR 中英）：
  [..., ['March', '3月', 'Mar'], ...]
       │
       ▼
Drive search query：
  (nubia) AND (UK OR britain OR ...) AND (DTC OR direct OR ...) 
  AND (March OR 3月 OR Mar) AND (GMV OR revenue OR ...)
```

新增 umbrella：
- 营销 ↔ marketing/campaign/推广/promotion/投放
- 营销活动 → kol/affiliate/paid media
- 社媒/social media → facebook/instagram/tiktok/youtube

### 7.3 自适应 candidate cap

`_adaptive_cap(n_candidates, abundant_cap, moderate_cap)`：
- ≤3 候选不砍（保护边缘 query）
- 4-7 用 cap+1
- ≥8 用 preset cap（aggressive）

应用于 doc/slide/gmail 三路。Q1/Q55 这种候选稀疏的 query 自动跳过 cap，避免召回崩溃。

### 7.4 文件读取 timeout 与 size gate 分层

| 文件类型 | timeout | size gate | 备注 |
|---|---|---|---|
| native Docs | api_timeout=7s | 45MB（auto outline >10MB）| 50MB+ skip + fallback |
| native Sheets | api_timeout=7s | — | tab 级 cap=4 |
| native Slides | api_timeout=7s | 55MB（可配）| 仅在 `enable_native_slide_size_gate` 时启用 |
| pptx | **pptx_download=7s** | **48MB（独立可配）** | 独立 timeout，比 download_timeout 更激进 |
| word/docx | **word_download=7s** | **30MB（新增）** | filter fallback 保留 size_gate |
| excel/csv | download_timeout=7s | — | sheet_pool 路径 |
| gmail | shared 14 worker | — | thread fetch + 可选附件 |

`_query_aware_truncate()`（slide chunk 后筛）：关键词命中 + reader_score 融合，省 file-level rerank 成本。

### 7.5 X1 cross-sq pre-filter（新 Phase G-3）

```
Phase G per-sub-query Qwen rerank（top sub_query_top_n=8/sq）
       │
       ▼ chunk_pool 8-16
Phase G-3：if chunk_pool > source_top_k * 2:
  - 用 focus 全文集合作为 query（跨 sq 评估）
  - Qwen rerank 跨 sq 收口到 source_top_k * 2 = 10
       │
       ▼ chunk_pool ≤ 10
Phase H eval_only LLM
```

开关：`google_enable_cross_sq_pre_filter=True`；阈值：`google_cross_sq_pre_filter_threshold=10`。

### 7.6 Wait/Event 线程安全（保留 Codex 早期修复）

每个 file pool（doc/sheet/slide/gmail）的修复点：
- `ensure_waitable()`：任务提交前预注册 asyncio.Event
- `mark_done()`：worker 用 `loop.call_soon_threadsafe(evt.set)` 跨线程唤醒主协程
- per_file_timeout 9s 作为最终安全网

修复前症状：所有 wait_for_done 精准卡 20s 兜底。修复后：wait_done 反映真实文件完成时刻。

### 7.7 跨 query LRU cache

`_FILE_CONTENT_CACHE`（同进程 module-level dict）：
- key = `f"{file_id}|{mime}"` 或 sheet 含 tab：`f"{file_id}|{mime}|{','.join(titles)}"`
- TTL 300s，LRU 64 file（满后驱逐最旧 1/4）
- 命中时跳过整次 `read_drive_chunks`，直接返回 cached gc_list
- 仅在同进程多 query 场景生效（eval batch / 长跑服务）

## 8. 完整流水延迟分布

| # | 环节 | 平均耗时 | 范围 | 并发模型 | 卡点 |
|---|---|---|---|---|---|
| ① plan_only LLM (Gemini) | 1.4s | 1.0-2.0s | 串行 1 LLM call | — |
| ② Phase A search_one | 2.6s | 1.5-5.4s | gather × N sq, 内部 5 路 | — |
| ③ Phase B metadata_prefetch | 1.5s | 0.5-3.5s | 8-worker batchGet | — |
| ④ Phase C+D 候选 + 合并 | 0.7s | 0.3-1.0s | Qwen rerank × 4 通道 | — |
| ⑤ **Phase E+F read+wait** | **9.0s** | **5-15s** | type_pools 4+3+3+14 | 🔴 主卡点 |
| ⑥ Phase G file/sq rerank | 1.6s | 0.6-2.5s | gather + Qwen | — |
| ⑦ Phase G-3 X1 pre-filter | 0.4s | 0.3-0.5s | 单次 Qwen rerank | — |
| ⑧ **Phase H eval_only LLM** | **4.0s** | **3-5s** | 串行 1 LLM call | 🟠 第二卡点 |

**理论 floor**：1.4 + 2.6 + 1.5 + 0.7 + 9.0 + 1.6 + 0.4 + 4.0 ≈ 21.2s。当前 23.6s 接近上限。

## 9. plan_only Prompt 设计

`tools/core/source_query_config.py:get_source_query_rules("google")` 关键规则：

1. data 类型：「品牌 + 渠道/国家 + 指标词」，月份中英由关键词层透明处理
2. concept 类型：2-3 个关键词，不加 time_filter
3. personal_recent 类型：`author_filter='me'` + `viewedByMeTime desc`
4. **社媒/营销/广告类**：仅在 query 显式问"按平台分布"时枚举平台名（FB/IG/TT/YT），其它情况由关键词扩展层自动 OR

设计原则：**LLM 输出语义关键词，不重复，不枚举同义词**。同义词、月份双语、marketing tactics、platform umbrella 全部下沉到 `kw_generalization.py`。

## 10. 已知限制（v2.7 后）

1. **数据缺失 query**：Drive 里没有相关主题文件时检索无能为力。需要 plan 层引导或"无相关数据"返回。
2. **plan_only LLM 输出非确定性**：相同 query 多轮跑可能产出不同 sub_query → Drive 召回差异。建议 plan 结果 hash 缓存 / few-shot 锚定。
3. **eval_only LLM RTT 物理下界 3-4s**：除非换更小 LLM 或 batch 化，无法压缩。
4. **Phase E+F 9s 物理下界**：受 Google API 网络延迟与配额限制。
5. **Notion 仍为 stub**：本次未涉及。