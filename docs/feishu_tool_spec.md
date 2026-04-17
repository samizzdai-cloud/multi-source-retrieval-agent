# Feishu Tool — 流程编排与能力规格 (v4.1: Reader 解耦 + 两阶段评分 + 校准数据)

> 描述 feishu 作为 RetrievalAgent 多源检索中的一个 source 的完整能力边界、当前流程和可扩展方向。
> 对照参考：`tools/bailian_retrieve.py`
> **v4.1 更新** (2026-04-16):
> - Reader 解耦: sheet/bitable 独立模块（删除 v1 残留，sheet 1024→425、bitable 807→354）
> - 两阶段评分: Stage 1 BM25+IDF 降噪 → Stage 2 RSF 混合（Doc/Table 差异化权重）
> - 阈值校准 v4: Pearson(score, grade)=-0.014，确认 L2 score 不可作为早停阈值
> - Bug 修复: `feishu_retrieve.py` logging format TypeError（line 545）
> **v4.0 基线** (2026-04-16): DocPool 混合评分、skip_scoring、rrf_merge 统一 boost、归一化修复

---

## 1. 定位与职责

```
RetrievalAgent
    ├─ 创建 EmbeddingCache + precompute_focuses(focuses) 并行启动
    ├─ 创建 FeishuDocPool(uat, query_type)
    │    └─ pool.set_embedding_cache(cache, focuses)  ← 注入 embedding 能力
    │
    ├─ fan-out:
    │    task_0 = feishu_retrieve(sq_A, doc_pool=pool) ─┐
    │    task_1 = feishu_retrieve(sq_B, doc_pool=pool) ─┼─ 共享 pool 去重 + 调度
    │    task_2 = feishu_retrieve(sq_C, doc_pool=pool) ─┘
    │
    └─ EvaluationLayer → rerank_and_filter → 最终 top-K

每个 feishu_retrieve 内部:
    Phase A: search → pool.submit_and_read → pool.wait_and_score (∥ Phase B)
    Phase B: search_tables → TablePool → sheet/bitable readers
    pool 内部:
      ├─ 文档去重（多个 sub_query 搜到同一 doc → 只读一次）
      ├─ 路由: query_type + 未缓存文档数 → Block API / MCP
      │    Block API: skip_scoring=True + 真实 sub_query (用于大表 LLM 提取)
      │    MCP: fetch_doc_markdown → parse_mcp_markdown_to_chunks
      ├─ 渐进缓存: 一篇文档 chunk 完 → event.set() → 等待的 task 立即可用
      └─ 混合评分: hybrid = 0.7 × embedding_sim(focus,chunk) + 0.3 × BM25_norm
         BM25 使用 kw_generalization 扩展的 sub_query
         噪声过滤: hybrid < 0.33 → 不输出
         所有分数归一化到 [0, 1]
```

**新架构核心文件**:
- `tools/feishu_doc_pool.py` — 跨 task 共享的文档读取池
- `tools/feishu_retrieve.py` — Dispatcher（支持 pool 模式 + 向后兼容独立模式）
- `feishu_read_tool/basic_tool/mcp_fetch_doc.py` — MCP fetch-doc + Markdown→chunks 解析

**设定参数** (query_type → top-N docs / per-doc chunks / max output):

| query_type | top-N docs | per-doc top-K | max output | MCP 触发条件 | Block API sem |
|---|---|---|---|---|---|
| data | 5 | 4 | 20 | 不触发（全走 Block API） | 3 |
| concept | 8 | 5 | 25 | 未缓存文档 > 3 | 3 |
| mixed | 6 | 5 | 22 | 未缓存文档 > 4 | 3 |

**P2 延迟优化关键参数**:
- DocPool `total_timeout`: 12s（渐进式返回，不等全部完成）
- DocPool `per-doc timeout`: 10s（快速释放 sem slot）
- `early_stop_min_elapsed_s`: 0s (由 min_sources=2 保护飞书)
- `min_sources`: 2 (要求飞书+百炼都返回)
- Sheet/Bitable LLM 行筛选 → BM25 替代 (<5ms vs 2-3s)
- Stage 5 BM25 预筛 → 仅 top-30 做 Embedding (省 51% API 时间)
- Table search+read 完全并行于 DocPool (消除 6-8s 串行)

**输入**: `(query: str, uat: str, time_filter: str | None, query_type: str, doc_pool: FeishuDocPool | None, table_pool: TablePool | None)`
**输出**: `List[UnifiedChunk]`（按 hybrid 混合评分排序，score ∈ [0, 1]，交由 RetrievalAgent 合并 + rerank）

### v4.1 评分体系（两阶段差异化，所有分数归一化到 [0, 1]）

| 评分位置 | Stage 1 阈值 | Stage 2 公式 | Stage 2 阈值 |
|----------|------|-----------|------|
| **Doc** `feishu_doc_pool._score_doc_chunks` | BM25+IDF ≥ 0.05 | `0.3 × bm25_norm + 0.7 × sim_emb` | ≥ `pool_noise_threshold` (默认 0.33) |
| **Table** `feishu_retrieve._two_stage_score_chunks` | BM25+IDF ≥ 0.10 | `0.6 × bm25_norm + 0.4 × sim_emb` | ≥ 0.20 |
| rrf_merge | — | `source_weight × norm_src + rank_weight × norm_rank` → `min(1.0, agg × boost)` | — |
| select_by_focus | — | `(emb_weight × sim_emb + kw_weight × sim_kw) / total_w` | — |

**设计意图**:
- Doc 侧语义主导（emb 0.7）：文档长文本适合 embedding 语义匹配
- Table 侧词面主导（BM25 0.6）：表格单元多为关键词，结构化数据精确匹配更有效
- `sim_emb` = top-2 focuses 余弦相似度均值（非 max，保留区分度）
- `bm25_norm = min(1, BM25_raw / 5.0)`，BM25 含 IDF 权重（`_compute_idf`）

**阈值校准 v4 告警** ⚠️:
- Pearson(chunk.score, overall_grade) = -0.014（18 queries, 1554 graded）
- feishu 单源 mean grade = 1.55（bailian 2.42，3× 差距）
- L2 score 不建议作为早停/过滤阈值，改用 reranker L4 focus_hits；详见 §10

---

## 2. 当前流程（v4.0 混合评分架构）

> v4.0 架构：DocPool 混合评分 + skip_scoring + 归一化统一
> pipeline 内 Stage 2+5 在 DocPool 模式下跳过，评分移至 DocPool.wait_and_score

```
feishu_retrieve (Dispatcher, per sub_query):

Phase A — 文档搜索（始终执行，与 Phase B 并行）:
  A1. 品牌变体并行搜索 (search_feishu_docs, MCP + Wiki)
  A2. 零返回重试 (简化 query / 品牌大小写变体)
  A3. 预检策略 (data 类跳过无关结果)
  A4. DocPool 读取:
      submit_and_read: BM25 文档级排序 → top-N → 路由
      _read_block_api_batch: run_rag_pipeline(query=真实sub_query, skip_scoring=True)
        Stage 3: Block API 并发获取 blocks（QPS 限流 + 99991400 退避）
        Stage 4: 两遍扫描 (特殊block + 文本chunk + 评论注入)
        Stage 2+5: SKIP (评分由 DocPool 完成)
      score_and_collect (v4.1, 事件驱动):
        Stage 1: bm25_norm = min(1, BM25+IDF(expand_keywords(sub_query), chunk) / 5)
                 过滤 bm25_norm < 0.05
        Stage 2: sim_emb = mean(top-2 of cosine_sim(focus_embs, chunk_emb))
                 hybrid = 0.3 × bm25_norm + 0.7 × sim_emb
                 过滤 hybrid < pool_noise_threshold（默认 0.33）

Phase B — 表格搜索（仅 data/mixed，与 Phase A 并行）:
  B1. search_feishu_tables (Direct + Wiki table)
  B2. TablePool 去重 + budget (max 3 sheet + 3 bitable)
  B3. Reader Dispatcher (v4.1 解耦):
      doc_type = SHEET → _read_sheet → sheet_reader.read_sheets
      doc_type = BITABLE → _read_bitable → bitable_reader.read_bitables
      sheet_reader: 40行 chunk + tab 前缀 + header carry + 合并单元格填充
      bitable_reader: 40行 chunk + 表名前缀 + contains filter + DateTime 格式化
  B4. _two_stage_score_chunks (v4.1 新增):
      Stage 1: bm25_norm ≥ 0.10（比 doc 更严，表格噪声多）
      Stage 2: hybrid = 0.6 × bm25_norm + 0.4 × sim_emb ≥ 0.20
      表格侧词面主导（0.6 BM25），与文档侧的语义主导（0.7 emb）形成差异化

合并: doc_chunks + table_chunks → List[UnifiedChunk]（score ∈ [0, 1]）
```

### Reader 解耦架构 (v4.1)

```
tools/feishu_retrieve.py (Dispatcher — 只做路由和合并)
  ├─ _read_docx   → feishu_read_tool/docx_pipeline.run_rag_pipeline
  ├─ _read_sheet  → feishu_read_tool/sheet_reader.read_sheets      ✨ 独立
  └─ _read_bitable → feishu_read_tool/bitable_reader.read_bitables ✨ 独立
```

- **sheet_reader.py** (425 行): 独立 sheet 读取，常量 `MAX_TABS_PER_SHEET=5, MAX_READ_ROWS=500, CHUNK_MAX_ROWS=40`
- **bitable_reader.py** (354 行): 独立 bitable 读取，常量 `MAX_TABLES_PER_BITABLE=3, MAX_RECORDS=2000, CHUNK_MAX_ROWS=40`
- **2026-04-16 修复**: 两个文件此前各有 v1 代码残留在文件末尾（Python 后定义覆盖前定义 → 实际跑 v1），已清理完成

### Stage 4 Block Type 覆盖全表

| block_type | 名称 | 处理方式 |
|---|---|---|
| 2,3-11,12-15,17,22 | 文字/标题/列表/引用/代码/待办 | chunker v2 格式渲染 |
| 19 (Callout) | 高亮块 | children 聚合 + [callout]:{} |
| 30 (Sheet embed) | 嵌入电子表格 | build_sheet_chunk → 5行分chunk |
| 31 (Table) | 原生表格 | 小表→MD, 大表+表头→LLM 提取, 大表无表头→截断 |
| 40 (Mermaid 小组件) | 流程图 | add_ons.record → mermaid chunk |
| 43 (Whiteboard) | 画板 | Board API → mermaid flowchart |
| 18/53 (Bitable embed) | 多维表格嵌入 | list_tables → list_fields → search_records → MD |
| equation | 行内公式 | $KaTeX$ 格式 |
| strikethrough | 删除线文字 | 跳过不计入 chunk |

### D-series 改造收益

| 阶段 | 改造前 | 改造后 |
|---|---|---|
| Stage 2 filter | ~2.5s（LLM） | <1ms（BM25） |
| Stage 5 score | ~18s（LLM） | <500ms（BM25+embedding）|
| 总 wall time | ~24s | ~2-3s |
| feishu 与 bailian 对齐 | early stop 对 feishu 无意义 | feishu 2-3s 回来，early stop 生效 |

### 配置参数（PipelineConfig 新增字段）

| 参数 | 默认 | 说明 |
|---|---|---|
| `query_type` | "mixed" | 影响 Stage 2 BM25 filter 和 Stage 5 评分 |
| `use_embedding_score` | True | Stage 5 开关：True=BM25+embedding, False=纯 BM25 |
| `bm25_weight` | 0.5 | 混合评分中 BM25 权重 |
| `emb_weight_score` | 0.5 | 混合评分中 embedding 权重 |

### Stage 4 Block Type 覆盖全表（v3 最新）

| block_type | 名称 | 处理方式 | 改动来源 |
|---|---|---|---|
| 2,3-11,12-15,17,22 | 文字/标题/列表/引用/代码/待办 | chunker v2 格式渲染 | 已有 |
| 19 (Callout) | 高亮块 | children 聚合 + [callout]:{} | 今日新增 |
| 30 (Sheet embed) | 嵌入电子表格 | build_sheet_chunk | 已有 |
| 31 (Table) | 原生表格 | build_table_chunk | 已有 |
| 40 (Mermaid 小组件) | 流程图 | add_ons.record → mermaid chunk | 今日新增 |
| 43 (Whiteboard) | 画板 | Board API → mermaid flowchart | 今日新增 |
| 53 (Bitable embed) | 多维表格嵌入 | reference_base.token → Markdown 表格 | 今日新增 |
| equation | 行内公式 | $KaTeX$ 格式 | 今日新增 |
| strikethrough | 删除线文字 | 跳过不计入 chunk | 今日新增 |

---

## 2.1 待规划（不执行）

### F1: EvalConfig.min_sources 对 feishu 慢源的 soft timeout

当前 min_sources=2 时，若 feishu 超过 5s 未返回，bailian 1s 已回来但无法触发早停。
规划：在 EvaluationLayer 中增加 per-source soft_timeout，feishu 超时后降级为 min_sources=1。
暂不实施，等 D-series 生效后重新评估是否仍需要。

### F2: per-source min_relevance_score

BM25+embedding 评分（feishu）与 bailian 内置 rerank 的分数量纲可能仍有偏差。
规划：在 EvalConfig 中增加 source_relevance_thresholds: Dict[str, float]。
需实测数据验证后决定。

> F3（eval_agent）、F4（Sheet V2 fix）、F5（Bitable token fix）已迁移至
> `.spec_organized/multi_source_rag_spec_v2.md §10`

### F6: Bitable embed token 解析（✅ 已修复）

问题：`reference_base.token` 格式为 `AppToken_tblTableId`，旧代码 `split('_')[0]` 在某些 token 下失效。
修复：`_build_bitable_embed_chunk` 入口加 `rsplit('_tbl', 1)[0]` 防御性解析。

---

## 2.2 原始流程（v1 基线，已废弃）

> ~~基线测试：2026-04-10 `nubia UK 流量 渠道`~~
> ~~Stage 1: 3,719ms  Stage 2-5 LLM pipeline: 20,588ms  总计: ~24s  chunks=63（LLM 是主要瓶颈）~~

```
[已被 D-series 替代]
Stage 2: LLM filter（_legacy_filter_candidates）→ BM25 filter
Stage 5: LLM score+summarize（llm_score_and_summarize）→ BM25+embedding hybrid
原始代码保留在 retrieval_judge.py / llm_client.py 供回退参考。
```
        block types: 30(table), 31(bitable_embed), 32(grid)
        Sheet: sheet_utils → 按行转 Markdown

Stage 5: LLM 评分 + 摘要
  llm_score_and_summarize(query, chunks)
    → 每个 chunk 打分 0-10，生成 1-2 句摘要
    → 阈值: text ≥ 0.5，table ≥ 0.4（归一化后进入 UnifiedChunk.score）

Stage 6: Token 预算分配
  allocate_budget(chunks, selected_ids, cfg)
    → 总上限: 12,000 tokens
    → 每篇文档上限: 4,000 tokens / 5 chunks
    → 输出: 按 relevance_score 降序排列的 ChunkResult 列表

Stage 7: 转换为 UnifiedChunk
  _chunk_result_to_unified(cr)
    → source_tool="feishu", score 归一化到 [0,1]
```

### 当前参数配置

| 参数 | 默认值 | 说明 |
|---|---|---|
| `page_size` | 20 | 搜索每页条数 |
| `max_candidates` | 10 | LLM 过滤后保留最多文档数 |
| `time_filter` | None | 飞书时间过滤（由 query_planner 注入） |
| `uat` | `""` → env fallback | 用户访问令牌 |

---

## 3. 凭证注入链路（修复后 v1.1）

```
SourceConfig.feishu_uat  (root_agent 传入或 env fallback)
    ↓ sc.get("feishu_uat", "FEISHU_UAT")
retrieval_agent.py fan-out → kwargs["uat"]
    ↓
feishu_retrieve(uat=...)
    ├─ search_feishu_file(uat=token)   → X-Lark-MCP-UAT header
    └─ run_rag_pipeline(user_access_token=token)
         └─ FeishuClient(user_access_token=token)
```

**防线**: 若 `uat` 为空（env 也未配置），`_get_available_sources()` 不会将 `feishu` 加入 `available_sources`，fan-out 不会触发，`feishu_retrieve` 不被调用。

---

## 4. 现有能力清单

### 4.1 已接入 RetrievalAgent（v3 D-series 现行流程）

| 能力 | 实现位置 | 状态 |
|---|---|---|
| 三路混合搜索（MCP + Wiki + Direct）| `feishu_read_tool/search.py` | ✅ 生产中 |
| 时间过滤（相对/绝对） | `search.py` 参数 | ✅ 生产中 |
| FeishuDocPool 跨 sub_query 共享文档读取 | `tools/feishu_doc_pool.py` | ✅ 生产中 |
| Block API 并发抓取（rate-limited, sem=3）| `feishu_read_tool/docx_pipeline.py` | ✅ 生产中 |
| 文本块切割（标题路径感知） | `feishu_read_tool/chunker.py` | ✅ 生产中 |
| 内嵌表格/Whiteboard/Bitable embed 解析 | `feishu_read_tool/table_handler.py` | ✅ 生产中 |
| BM25 候选文档筛选（Stage 2，替代 LLM）| `tools/chunk_scorer.py` | ✅ 生产中 |
| BM25+embedding 混合 chunk 评分（Stage 5，替代 LLM）| `tools/chunk_scorer.py` | ✅ 生产中 |
| Sheet 读取（section 扫描 + BM25 行筛选）| `feishu_read_tool/sheet_reader.py` | ✅ 生产中 |
| Bitable 读取（contains filter + DateTime）| `feishu_read_tool/bitable_reader.py` | ✅ 生产中 |
| Phase B Table search 与 DocPool 完全并行 | `tools/feishu_retrieve.py` | ✅ 生产中 |
| LLM 候选过滤（Stage 2，旧版）| `feishu_read_tool/retrieval_judge.py` | 🚫 已被 BM25 替代（D2）|
| LLM chunk 评分+摘要（Stage 5，旧版）| `feishu_read_tool/pipeline.py` | 🚫 已被 BM25+embedding 替代（D3）|

### 4.2 已存在但未接入 RetrievalAgent

| 能力 | 实现位置 | 说明 |
|---|---|---|
| 多维表格（Bitable）独立查询 | `read_tool/bitable_read.py` | list_tables / list_fields / search_records，支持筛选条件 |
| 飞书链接解析 | `url_tool.py` | 从 URL 提取 doc_id / bitable_id / table_id / wiki_token |
| 文件夹递归扫描 | `folder_scanner.py` | 遍历指定 folder token 内所有文件，输出 FileRecord 列表 |
| 单文档深度阅读（Path A） | `doc_tools.py` / `doc_reader.py` | resolve_doc_ref → read_full_doc → analyze_doc，LLM 全文分析 |
| 知识空间节点搜索 | `reference/KB_feishu_reference/` | 知识库 wiki_space 节点 API（仅文档） |

---

## 5. 可扩展能力设计（v2 规划）

### 5.1 Bitable 结构化检索 — `feishu_bitable_retrieve`

**场景**: 用户 query 涉及表格型数据（如"nubia 3月销售数据"），当前 feishu 路径只能通过文档内嵌表格抓到 → 缺少对独立多维表格的直接查询。

```
新增入口: tools/feishu_retrieve.py → feishu_bitable_retrieve(query, uat, table_hint?)

流程:
  1. query_planner 生成 target="feishu_bitable"（新增源类型）
  2. 按 table_hint（URL/token）定位 bitable 或由 LLM 推断目标表格
  3. list_fields → 获取字段 schema
  4. LLM 生成筛选条件（field_name + operator + value）
  5. search_records(bitable_id, table_id, uat, conditions)
  6. 记录 → 转为 Markdown 表格 → UnifiedChunk

关键约束:
  - 需要预先在 SourceConfig 中配置 bitable 白名单（bitable_id mapping）
  - 字段 schema 由 list_fields 动态获取，无需硬编码
  - 适合: 销售数据、库存、KPI 追踪类结构化表
```

### 5.2 文件夹作用域检索 — `folder_scoped_search`

**场景**: query_planner 识别出用户意图仅限某个业务域（如"hypershell 项目文件夹内"），可缩小搜索范围，提升精度。

```
流程:
  1. SourceConfig 配置 folder_token_map: {"hypershell": "FolderXXX", "nubia": "FolderYYY"}
  2. query_planner sub_query 携带 folder_hint
  3. feishu_retrieve 先 FolderScanner 获取该文件夹下所有 doc_id
  4. 将 doc_id 过滤注入 search_feishu_file（当前 API 不直接支持 → 降级为 search 后 post-filter）

依赖: SourceConfig 增加 feishu_folder_map 字段
```

### 5.3 知识空间（Wiki）节点检索

**场景**: 用户知识沉淀在飞书知识库（wiki space）而非普通云文档中，当前 search-doc 无法检索 wiki 节点。

```
流程:
  使用 知识空间节点搜索 API（GET /wiki/v2/spaces/{space_id}/nodes/search）
  → 返回节点列表（含 node_token / obj_token / title）
  → 再通过 FeishuClient.list_document_blocks(obj_token) 读取内容
  → 进入 Stage 4-7 标准 pipeline

依赖: SourceConfig 增加 feishu_wiki_space_ids: List[str] 字段
```

### 5.4 搜索质量提升 — 多轮翻页 + 重查

**场景**: 当前 Stage 2 LLM 过滤后可用文档不足，无法达到 EvalConfig.min_good_chunks。

```
改进: feishu_retrieve 内建 adaptive_search 机制
  Round 1: page_size=20, max_pages=1（当前行为）
  Round 2（按需）: 若 pipeline 输出 chunks < 阈值 → 继续翻页或放宽 time_filter 重搜
  触发条件: RAGResult.total_chunks < min_good_chunks

成本: +1 round = +1 search API call + Stage 3-5 增量处理（seen_doc_ids 去重）
```

---

## 6. 与 bailian_retrieve 的对比

| 维度 | feishu_retrieve（当前） | bailian_retrieve_unified |
|---|---|---|
| 检索方式 | 关键词 AND 匹配（MCP search-doc） | 向量语义检索（Dense + Sparse 混合） |
| query 风格 | 2-3 词短关键词 | 完整语义句子 |
| 内容类型 | 任意飞书云文档（doc/docx/sheet/bitable） | 预先索引的知识库文档 |
| 时间过滤 | ✅ 支持（open_time_relative） | ✅ 支持（时效 tag: YYYY_QN / last_long） |
| 业务实体过滤 | ❌（全局搜索） | ✅（indexes 指定知识库名称） |
| 分类过滤 | ❌ | ✅（l2 tags: Ads / DTC / Marketplace 等） |
| Reranking | 百炼内置 qwen3-rerank-hybrid | RetrievalAgent 全局 RRF rerank |
| 凭证 | user_access_token（个人身份） | AK/SK（服务身份） |
| 实时性 | ✅ 飞书实时文档 | 依赖知识库同步周期 |
| 结构化数据 | ✅ 表格/Sheet（Stage 4 解析） | ❌（纯文本 chunk） |

---

## 7. SourceConfig 字段规划

```python
@dataclass
class SourceConfig:
    # 当前字段
    feishu_uat: str = ""                    # 用户访问令牌（必填，否则 feishu 不启用）
    load_from_env: bool = True

    # v2 扩展（规划）
    feishu_folder_map: str = ""             # "hypershell:FolderXXX,nubia:FolderYYY"
    feishu_wiki_space_ids: str = ""         # "space1,space2"（逗号分隔）
    feishu_bitable_map: str = ""            # "sales_table:BitableXXX"
```

---

## 8. 待办事项（TODO）

> 执行策略：逐步丰富各文件类型的 read 能力 → 最后统一调整 feishu_tool 整体架构。

### 8.1 BUG FIX（优先）

- [ ] **[TODO-51] Wiki 新版子目录（block_type=51）** — P0，用户明确要求
  - 现象：type-51 block 在 pass-2 text traversal 中被静默跳过，无内容产出。
  - 需要确认：SDK 是否对 type-51 返回子目录结构（title/link），若有 → 提取为文本 chunk；若无 → 需调用 Wiki v2 API 获取节点内容。
  - 影响文件：`feishu_read_tool/chunker.py`（TEXT_TYPES 或新分支）

- [ ] **[BUG-2] type-18 多维表格嵌入（bitable embed）静默跳过**
  - 现象：pipeline.py pass-1 只处理 type-30（sheet）和 type-31（table），type-18 无分支，内容完全丢失。
  - 修复方向：pass-1 增加 `btype == 18` 分支 → 提取 bitable_token → 调用 `bitable_read.py` 获取记录 → Markdown 表格 → Chunk
  - 影响文件：`feishu_read_tool/pipeline.py`（pass-1）+ 可能需要 client 新增 bitable token 提取

- [x] **[BUG-1] Wiki-hosted docx token 解引用失败**（`pipeline.py` Stage 3）✅
  - 现象：MCP 返回 `feishu.cn/wiki/XxxToken` URL 时，`id` 字段是 wiki node token，非 docx document_id。`list_document_blocks(wiki_node_token)` 调用 404，文档被静默丢弃。
  - 修复：`_fetch_single_doc` 中检测 `doc_url` 含 `/wiki/`，先调 `client.get_wiki_node_info(doc_id)` → 取 `obj_token`，再走 Block API。
  - 影响文件：`feishu_read_tool/pipeline.py`

---

### 8.2 各文件类型读取能力（逐步丰富）

#### 📄 docx / doc — Block Type 能力覆盖全表

> 参照飞书官方枚举（共 51 种），按处理状态分类。

| block_type | 名称 | 处理状态 | 说明 |
|---|---|---|---|
| 1 | 页面根节点 | ✅ 正常跳过 | 容器，无内容 |
| 2 | 文本 | ✅ TEXT_TYPES | `build_text_chunks` |
| 3–11 | 标题 H1-H9 | ✅ HEADING_TYPES | 更新 heading_path + 提取文本 |
| 12 | 无序列表 | ✅ TEXT_TYPES | |
| 13 | 有序列表 | ✅ TEXT_TYPES | |
| 14 | 代码块 | ✅ TEXT_TYPES | |
| 15 | 引用 | ✅ TEXT_TYPES | |
| 17 | 待办事项 | ✅ TEXT_TYPES | |
| 19 | 高亮块（Callout） | ✅ TEXT_TYPES | |
| 22 | 分割线 | ✅ TEXT_TYPES（空内容） | 无文本，产出空 chunk 后被过滤 |
| 30 | 电子表格（嵌入 Sheet） | ✅ `build_sheet_chunk` | 读取外部 sheet 数据 |
| 31 | 表格 | ✅ `build_table_chunk` | 已修复 cell 文本提取（BUG-fix）|
| 32 | 表格单元格 | ✅ consumed by type-31 | |
| 24 | 分栏容器 | ⚠️ 容器跳过，内容靠子块 | 子块 type-2/12 在 flat list → pass-2 读到 |
| 25 | 分栏列 | ⚠️ 同上 | |
| 34 | 引用容器 | ⚠️ 同上 | 需验证子块是否进 flat list |
| 49 | 源同步块 | ⚠️ 跳过 | 仅支持查询，需特殊 API |
| 50 | 引用同步块 | ⚠️ 跳过 | 需调用「获取引用同步块内容」API |
| **18** | **多维表格嵌入（Bitable embed）** | 🔴 **静默跳过** | pass-1 无 btype==18 分支，无内容产出 |
| **27** | **图片** | 🔴 **静默跳过** | SDK 仅返回 image token，无文本；caption 待确认 |
| **51** | **Wiki 新版子目录** | 🔴 **静默跳过，P0** | 用户明确要求加入 P0 待办 |
| 16 | — | ✅ 枚举中不存在 | |
| 20 | 会话卡片 | ➖ 跳过（合理） | 无文本语义价值 |
| 21 | 流程图 & UML | ➖ 跳过（合理） | 图形无法文本化 |
| 23 | 文件附件 | ➖ 跳过（合理） | 无 Block API 内容 |
| 26 | 内嵌网页 | ➖ 跳过（合理） | |
| 28 | 开放平台小组件 | ➖ 跳过（合理） | |
| 29 | 思维笔记 | ➖ 跳过（合理） | |
| 33 | 视图 Block | ➖ 跳过（合理） | |
| 35–39 | 任务 / OKR 系列 | ➖ 跳过（合理） | |
| 40–48 | 小组件 / 议程系列 | ➖ 跳过（合理） | |
| 41 | Jira 问题 | ➖ 跳过（合理） | |
| 43 | 画板 | ➖ 跳过（合理） | |
| 48 | 链接预览 | ➖ 跳过 | URL 文本可考虑提取，低优先级 |

- [x] **[BUG-1]** Wiki-hosted docx token 解引用（见 §8.1）✅ 已修复
- [x] 表格单元格文本提取（type-31 cell children）✅ 已修复
- [ ] **[BUG-2] type-18 bitable embed 静默跳过**（pipeline.py pass-1 缺 btype==18 分支）
- [ ] **[TODO-51] Wiki 新版子目录（type-51）**：P0，见 §8.1 BUG FIX 列表
- [ ] type-27 图片 caption 提取（低优先级，需确认 SDK 是否暴露 caption 字段）
- [ ] type-49/50 同步块内容（需特殊 API，中优先级）
- [ ] 旧版 doc 兼容性验证（block_type 行为差异，低优先级）

#### 📊 sheet（电子表格）
- [x] Direct API 搜索（已接入混合 hybrid search）
- [x] docx 内嵌 Sheet 解析（`sheet_utils.py` via Block API）
- [ ] **独立 Sheet 读取**：`doc_type=SHEET` 时 Block API 不适用 → 需走 `FeishuClient.read_sheet_range()` 读取指定范围数据，转为 Markdown → UnifiedChunk
  - 需在 Stage 3 按 `doc_type` 分流：`SHEET` → `read_sheet_range`，`DOCX` → `list_document_blocks`
  - `read_sheet_range()` 已实现（`client.py`），缺分流逻辑和 chunk 转换
  - 影响文件：`feishu_read_tool/pipeline.py`（Stage 3 + Stage 4 新增 sheet 分支）

#### 🗃️ bitable（多维表格）
- [x] Direct API 搜索（已接入混合 hybrid search）
- [ ] **独立 Bitable 读取**：`doc_type=BITABLE` 时 Block API 无法读取记录 → 需走 `bitable_read.py`（list_tables → list_fields → search_records）
  - `bitable_read.py` 已实现（`list_tables` / `list_fields` / `search_records`），缺接入 pipeline
  - 记录 → Markdown 表格 → UnifiedChunk 转换需新增
  - 影响文件：`feishu_read_tool/pipeline.py` Stage 3 分流 + 新建 `bitable_chunker`（或复用 `table_handler.py`）

#### 🎞️ slides（幻灯片）
- [x] Direct API 搜索（已接入混合 hybrid search）
- [ ] **可行性验证**：`doc_type=SLIDES` 是否支持 Block API（slides 有独立 API，待实测）
  - 若 Block API 兼容 → 复用现有 text chunking
  - 若不兼容 → Stage 3 中仅保留 title + summary 作为单一 UnifiedChunk（低信息量降级）

#### 📎 file（二进制附件）
- [x] Direct API 搜索（有返回但极少）
- [ ] **暂不读取**：PDF/Office 附件无 Block API 支持，Stage 3 中对 `doc_type=FILE` 直接跳过（return None）

---


### 8.3 搜索能力扩展（后续规划）

- [ ] **多轮翻页 adaptive_search**：chunks < min_good_chunks 时自动翻页重搜（`feishu_retrieve.py` 内，小改动）
- [ ] **Bitable 独立语义检索**（`feishu_bitable_retrieve`）：query → LLM 生成筛选条件 → structured query → UnifiedChunk
- [ ] **Wiki 知识空间全量检索**：`/wiki/v2/spaces/{id}/nodes/search`，覆盖无法被 search-doc 搜索到的 wiki 文档
- [ ] **文件夹作用域搜索**：`SourceConfig.feishu_folder_map` + FolderScanner post-filter，精准缩小搜索域

---

### 8.5 LLM 移除 + BM25 评分重构（改动 D，P0）

> **背景**：基线测试 Stage 2+5 LLM 合计 20s+，是 feishu pipeline 的绝对瓶颈。
> 目标：wall time 从 ~24s 降到 ~3s，与 bailian（~2s）对齐，使 evaluator early stop 对 feishu 真正有意义。

#### D-系列改动规划

**D1：创建 `tools/chunk_scorer.py`（共享 BM25 评分模块）**

```python
# 职责：零 LLM 的 chunk 相关性评分，供 feishu_tool 和未来其他 tool 复用
# 核心函数：
def bm25_score_chunks(query, chunks, query_type, expanded=True) -> List[Tuple[Chunk, float]]
def normalize_scores(scored, source="bm25", target_min=0.3, target_max=0.95) -> List[...]
```

**参数对应关系（feishu 语境中 dense/sparse top_k 的真实意义）**：

| 百炼参数（对服务端无实质影响） | feishu 等价（真正有效） | data | concept | mixed |
|---|---|---|---|---|
| `dense_similarity_top_k` | EmbeddingCache `sim_threshold` | 0.50 | 0.35 | 0.40 |
| `sparse_similarity_top_k` | BM25 `max_candidates` | 10 | 15 | 12 |
| `rerank_top_n` | `PipelineConfig.max_candidates`（最终保留） | 6 | 8 | 8 |
| `rerank_min_score` | BM25 归一化后 `min_score` | 0.3 | 0.2 | 0.25 |

注：百炼服务端实测 `dense/sparse_top_k` 在 5-20 范围内对结果几乎无影响（scores 差 <0.001），
feishu 本地 BM25 评分中这两个参数才是真正有意义的控制杆。

**D2：`feishu_read_tool/pipeline.py` Stage 2 改造**

```
改造前：LLM filter（2-3s，_legacy_filter_candidates）
改造后：BM25 pre-filter
  - BM25 score → top max_candidates
  - data: expanded=False, max_candidates=10
  - concept: expanded=True, max_candidates=15
```

**D3：`feishu_read_tool/pipeline.py` Stage 5 改造**

```
改造前：LLM score+summarize（5-8s，llm_score_and_summarize）
改造后：BM25 score（<10ms，chunk_scorer.bm25_score_chunks）
  - 归一化到 [0.3, 0.95]，与 bailian score 量纲对齐
  - data: expanded=False（精确词匹配）
  - concept: expanded=True（同义词扩展）
```

**D4：`feishu_retrieve.py` 接受 `query_type` 参数**

```python
async def feishu_retrieve(query, uat, time_filter=None, query_type="mixed") -> List[UnifiedChunk]
# query_type 传入 PipelineConfig，影响 D2/D3 的参数选择
```

**D5：`feishu_read_tool/pipeline_models.py` PipelineConfig 新增 query_type 感知**

```python
@dataclass
class PipelineConfig:
    query_type: str = "mixed"
    # Stage 2 BM25 filter
    bm25_max_candidates: int = 12       # 按 query_type 覆盖
    bm25_expanded: bool = True
    # Stage 5 BM25 score
    bm25_score_threshold: float = 0.25  # 按 query_type 覆盖
    # ...existing fields
```

**D6：`EvalConfig` 改为 per-source `min_relevance_score`**

```python
# 当前：全局 0.5（与 bailian 对齐，对 feishu BM25 分数过严）
# 改后：
source_relevance_thresholds: Dict[str, float] = {
    "bailian": 0.5,  # 与服务端 rerank_min_score 对齐
    "feishu": 0.3,   # BM25 归一化后的合理阈值
}
```

**D7：循环 import 修复（已完成 ✅）**

- `feishu_read_tool/pipeline_models.py`：LLM 常量 inline，不再 import `tools`
- `feishu_read_tool/pipeline.py`：`call_llm` 改为函数内延迟 import
- `feishu_read_tool/llm_client.py`：`call_llm` 改为函数内延迟 import
- `tools/__init__.py`：`feishu_retrieve` 改为 `__getattr__` 延迟加载

**预期收益**：

| 阶段 | 当前 | 改后 |
|---|---|---|
| Stage 2 filter | ~2.5s（LLM） | <0.01s（BM25） |
| Stage 5 score | ~18s（LLM，每批20 chunks）| <0.01s（BM25） |
| 总 wall time | ~24s | ~3s |
| feishu 与 bailian 对齐后 | early stop 对 feishu 无意义 | feishu 2-3s 即回来，early stop 生效 |

---

### 8.6 架构重构（read 能力完善后统一进行）

> 待 §8.2 各类型 read 能力实现后，统一评估是否需要：
> - Stage 3 重构为 dispatcher 模式（按 doc_type 路由到不同 reader）
> - 将 `pipeline.py` 拆分为 `docx_reader.py` / `sheet_reader.py` / `bitable_reader.py`
> - 统一 reader 接口：`async def read(doc: dict, client: FeishuClient, cfg) -> List[Chunk]`

---

## 9. 实施优先级

| 优先级 | 能力 | 改动量 | 价值 |
|---|---|---|---|
| P0 ✅ | 凭证链路修复（v1.1） | 已完成 | 多用户场景必须 |
| P0 ✅ | 混合 hybrid search（MCP + Direct） | 已完成 | sheet/bitable/slides 覆盖 |
| P0 ✅ | **[BUG-1] Wiki docx token 解引用** | 已完成 | 已修复，不再静默丢失 |
| P0 ✅ | **[BUG-fix] type-31 表格单元格文本提取** | 已完成 | cell children 正确读取 |
| P0 🔴 | **[TODO-51] type-51 Wiki 新版子目录** | 小（chunker 新分支） | 用户明确要求 |
| P0 🔴 | **[BUG-2] type-18 bitable embed 静默跳过** | 中（pipeline pass-1 + bitable API） | 嵌入式多维表格内容丢失 |
| P1 ✅ | **独立 Sheet 读取（v4.1）** | 已完成 — `feishu_read_tool/sheet_reader.py` 425 行 | 数据型文档（销售表等）覆盖 |
| P1 ✅ | **独立 Bitable 读取（v4.1）** | 已完成 — `feishu_read_tool/bitable_reader.py` 354 行 | 结构化查询数据覆盖 |
| P2 | type-49/50 同步块内容读取 | 中（特殊 API） | 同步文档内容场景 |
| P2 | type-27 图片 caption 提取 | 小（验证 SDK 字段） | 低频但有价值 |
| P2 | Slides 读取可行性验证 | 小（验证+降级方案） | 低频但有价值 |
| P2 | file 类型 Stage 3 跳过 | 极小 | 避免无效 API 调用 |
| P0 ✅ | **[D7] 循环 import 修复** | 已完成（3文件延迟import） | feishu pipeline 可被独立测试 |
| P0 ✅ | **[D1] BM25 共享评分（feishu_doc_pool._bm25_score + _compute_idf）** | 已完成 | 零 LLM 评分基础设施 |
| P0 ✅ | **[D2] Stage 2 LLM filter → BM25** | 已完成 | -2.5s |
| P0 ✅ | **[D3] Stage 5 LLM score → BM25+embedding 两阶段** | 已完成（v4.1 两阶段 Doc/Table 差异化） | -18s，最大收益 |
| P0 ✅ | **[D4] feishu_retrieve 接受 query_type** | 已完成 | query_type 感知 |
| P0 ✅ | **[D5] PipelineConfig 新增 query_type 感知** | 已完成 | 参数按类型分化 |
| P1 | **[D6] EvalConfig per-source min_relevance_score** | 小 | feishu/bailian 量纲对齐 |
| P3 | 多轮翻页 adaptive_search | 小 | 提升召回率 |
| P4 | Wiki 知识空间全量检索 | 中 | 知识库场景 |
| P5 | 文件夹作用域搜索 | 小-中 | 精准业务域 |
| P6 | 架构重构（dispatcher + reader 拆分） | 大 | 可维护性，待 read 能力完善后 |

---

## 10. v4.1 阈值校准发现（2026-04-16）

### 10.1 校准配置

- 18 queries（6 data + 6 concept + 6 mixed）
- WIDE_RECALL: `hard_timeout=55s`、`feishu_pool_timeout=55s`、`feishu_pool_noise_threshold=0.0`
- Grader: `gemini-3-flash-preview`，1554/1566 有效评分

### 10.2 Feishu 源质量（严峻）

| 指标 | Feishu | Bailian | 差距 |
|---|---|---|---|
| 总 chunk 数 | 980 | 574 | feishu 量多 |
| mean overall_grade | **1.55** | **2.42** | 3× |
| %≥3 | 5.2% | 35.7% | 7× |
| %≥4 | 1.4% | 19.3% | 14× |

### 10.3 Feishu 按 doc_type 细分（全部平坦）

| doc_type | mean grade |
|---|---|
| text | 1.63 |
| table_md | 1.66 |
| unknown | 1.31 |

→ 无论 text 还是 table，grade 都停留在 1.6 附近，说明不是纯粹的 chunking 类型问题。

### 10.4 Score 与 Grade 无相关

Pearson(chunk_score, overall_grade) 全体 = **-0.014**，data -0.142, concept +0.059, mixed +0.093。

### 10.5 推断原因（待验证）

1. **heading_path 噪声**: chunk 中插入完整章节路径，可能稀释关键词密度
2. **Stage 1 BM25 阈值过低**: doc=0.05，实测 feishu 1.4% ≥4，阈值可上调到 0.10
3. **embedding 召回过宽**: top-2 focuses 均值对低质 chunk 仍会给高 emb 分
4. **搜索源本身限制**: MCP search-doc 是关键词 AND 匹配，对语义查询召回质量天然差

### 10.6 Action Items（按优先级）

- [ ] **A. Stage 1 阈值实验**: feishu_doc_pool._score_doc_chunks 的 `bm25_noise` 从 0.05 → 0.10（表格侧是 0.10，验证是否对 doc 也有效）
- [ ] **B. heading_path 裁剪实验**: 只保留末级 2 级 heading，减少 chunk 内容稀释
- [ ] **C. Focus 覆盖诊断**: 25.5% focus 零高分，定位是搜索未召回 / chunking 丢失 / 评分误杀
- [ ] **D. min_relevance_score=0.6 全局应用**: 卡在 bailian 甜点区左侧，天然过滤 feishu 噪声
- [ ] **E. min_sources=2 策略复查**: 若 feishu 对特定 query 结构性缺失，强制等 feishu 可能拖慢整体
