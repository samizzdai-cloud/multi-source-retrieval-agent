# Google Workspace Source Agent Architecture Specification

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