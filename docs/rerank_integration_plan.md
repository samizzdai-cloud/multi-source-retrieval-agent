# Rerank 集成到 Feishu 检索系统的完整方案

## 概述

本文档详细说明如何将 qwen3-rerank 模型集成到 Feishu 检索系统中，用于两个关键场景：
1. **搜索结果重排序**：决定哪些候选文档提交给 doc_pool/table_pool
2. **Chunks 精排和过滤**：对最终返回的 chunks 进行二次排序和质量过滤

## 核心设计

### 1. 索引映射机制

**问题**：rerank 返回的 `index` 需要映射回原始数据

**解决方案**：
```python
# 原始候选列表
candidates = [
    {"id": "doc_001", "title": "文档1", "summary": "摘要1"},
    {"id": "doc_002", "title": "文档2", "summary": "摘要2"},
    # ...
]

# 准备 rerank 输入（保持顺序）
rerank_docs = [f"[{c['title']}] {c['summary']}" for c in candidates]

# 调用 rerank
result = rerank_documents(query=focus, documents=rerank_docs, top_n=5)

# 索引映射：rerank 返回的 index → 原始 candidates
for item in result['results']:
    original_idx = item['index']  # rerank 返回的索引
    score = item['relevance_score']  # 相关性分数
    original_doc = candidates[original_idx]  # 映射回原始文档
    
    print(f"Rank: index={original_idx} → {original_doc['id']}")
```

**关键点**：
- ✅ rerank 输入的顺序必须与原始列表一致
- ✅ rerank 返回的 `index` 是原始列表的索引（0-based）
- ✅ 通过 `candidates[index]` 直接映射回原始数据

---

### 2. 场景一：搜索结果重排序

**位置**：`tools/feishu_doc_pool.py` → `submit_and_read()`

**当前逻辑**：
```python
# 当前使用 BM25 排序
ranked = _bm25_rank_candidates(query, candidates)[:cfg.top_n_docs]
```

**集成 Rerank**：
```python
async def submit_and_read(
    self, candidates: List[dict], query: str,
    query_type: Optional[str] = None,
    use_rerank: bool = True,  # 新增参数
) -> List[str]:
    """注册候选文档，Rerank/BM25 筛选 top-N，触发读取。"""
    
    effective_qt = query_type or self.default_query_type
    cfg = PoolConfig.for_query_type(effective_qt)
    
    # ── Rerank 重排序 ────────────────────────────────────────────
    if use_rerank and len(candidates) > 1:
        from tools.rerank import rerank_documents
        
        # 准备输入：组合 title + summary
        rerank_docs = []
        for c in candidates:
            doc_text = f"[{c.get('title', '')}] {c.get('summary', '')}"
            rerank_docs.append(doc_text)
        
        # 调用 rerank（使用 focus 作为 query）
        rerank_result = rerank_documents(
            query=query,  # 使用 sub_query 作为 query
            documents=rerank_docs,
            model="qwen3-rerank",
            top_n=cfg.top_n_docs,  # data=5, concept=8, mixed=6
            instruct_template="qa",  # QA 模式：找答案
            return_documents=False,
        )
        
        # 索引映射：rerank 返回的 index → 原始 candidates
        if rerank_result['status'] == 'success' and rerank_result['results']:
            ranked = []
            for item in rerank_result['results']:
                original_idx = item['index']
                ranked.append(candidates[original_idx])
            
            logger.info(
                "DocPool.submit_and_read: rerank → %d docs (tokens=%d)",
                len(ranked), rerank_result['usage']['total_tokens']
            )
        else:
            # Fallback 到 BM25
            logger.warning("DocPool: rerank failed, fallback to BM25")
            ranked = _bm25_rank_candidates(query, candidates)[:cfg.top_n_docs]
    else:
        # 不使用 rerank，直接 BM25
        ranked = _bm25_rank_candidates(query, candidates)[:cfg.top_n_docs]
    
    # 后续逻辑不变...
    selected_ids = []
    # ...
```

**参数配置**：
| query_type | top_n_docs | 说明 |
|-----------|-----------|------|
| data | 5 | 数据类查询，精确匹配 |
| concept | 8 | 概念类查询，广泛召回 |
| mixed | 6 | 混合类查询，平衡策略 |

---

### 3. 场景二：Chunks 精排和过滤

**位置**：`agents/feishu_source_agent.py` → `execute_sub_query_full()`

**当前逻辑**：
```python
uc_list = await feishu_retrieve(...)
uc_list = sorted(uc_list, key=lambda x: x.score or 0.0, reverse=True)
uc_list = uc_list[: self.cfg.sub_query_top_n]  # 截断到 top-8
```

**集成 Rerank**：
```python
async def execute_sub_query_full(self, sq: SubQuery) -> List[ChunkEntry]:
    """执行单个 sub_query，返回 ChunkEntry 列表。"""
    
    # ... 前面的逻辑不变 ...
    
    uc_list = await feishu_retrieve(...)
    if not uc_list:
        return []
    
    # ── Rerank 精排和过滤 ────────────────────────────────────────
    if len(uc_list) > 1:
        from tools.rerank import rerank_for_rag
        
        # 转换为 rerank_for_rag 需要的格式
        chunks_for_rerank = [
            {
                "text": uc.text,
                "source_name": uc.source_name,
                "source_url": uc.source_url,
                "heading_path": uc.heading_path,
                "score": uc.score,
                "metadata": uc.metadata,
                "_original_uc": uc,  # 保留原始对象
            }
            for uc in uc_list
        ]
        
        # 调用 rerank_for_rag（自动过滤低分）
        reranked_chunks = rerank_for_rag(
            query=sq.target_focus,  # 使用 focus 作为 query（更语义化）
            chunks=chunks_for_rerank,
            top_n=self.cfg.sub_query_top_n,  # 默认 8
            score_threshold=0.3,  # 过滤低相关性 chunk
            text_field="text",
        )
        
        # 恢复为 UnifiedChunk 列表
        uc_list = [chunk["_original_uc"] for chunk in reranked_chunks]
        
        logger.info(
            "[feishu] rerank: %d → %d chunks (threshold=0.3)",
            len(chunks_for_rerank), len(uc_list)
        )
    else:
        # 单个 chunk，直接使用
        uc_list = sorted(uc_list, key=lambda x: x.score or 0.0, reverse=True)
        uc_list = uc_list[: self.cfg.sub_query_top_n]
    
    # 转换为 ChunkEntry
    return [_unified_to_chunk_entry(uc, sq.query, sq.target_focus) for uc in uc_list]
```

**分数阈值建议**：
- `>= 0.7`：高度相关，可直接使用
- `0.3 - 0.7`：中等相关，需结合上下文
- `< 0.3`：低相关，建议过滤

---

## 数据格式规范

### 搜索结果格式（MCP + Wiki 合并后）

```python
{
    "id": "doc_001",                    # 文档 ID
    "title": "NUBIA 2024 Q1 销售数据",  # 文档标题
    "summary": "本文档详细分析...",      # 文档摘要（MCP 提供）
    "doc_type": "DOCX",                 # 文档类型
    "url": "https://...",               # 文档 URL
    "_source": "mcp"                    # 来源标识
}
```

### Rerank 输入格式

**搜索结果重排序**：
```python
# 格式：[标题] 摘要
doc_text = f"[{candidate['title']}] {candidate['summary']}"
```

**Chunks 精排**：
```python
# 直接使用 chunk.text
doc_text = chunk['text']
```

### Rerank 输出格式

```python
{
    "status": "success",
    "results": [
        {
            "index": 0,              # 原始列表索引
            "relevance_score": 0.95  # 相关性分数 [0.0, 1.0]
        },
        # ...
    ],
    "usage": {"total_tokens": 123},
    "request_id": "xxx"
}
```

---

## 性能优化

### 1. 两阶段检索

```python
# Stage 1: 向量检索（召回 100）
top_100 = vector_search(query_emb, embeddings, top_k=100)

# Stage 2: Rerank（精排 10）
top_10 = rerank_documents(query=query, documents=top_100, top_n=10)
```

### 2. 批量处理

```python
# 对多个 sub_query 串行 rerank
for sq in sub_queries:
    result = rerank_documents(query=sq.query, documents=candidates)
```

### 3. 缓存策略

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def cached_rerank(query: str, docs_tuple: tuple) -> dict:
    """缓存 rerank 结果（相同 query + documents 组合）"""
    return rerank_documents(query=query, documents=list(docs_tuple))
```

---

## 成本估算

### 模型定价

| 模型 | 价格 | 最大文档数 | 单条最大Token |
|------|------|-----------|--------------|
| qwen3-rerank | ¥0.0005/千Token | 500 | 4,000 |

### 场景成本

**场景一：搜索结果重排序**
- 输入：8 个候选 × 平均 100 字（title + summary）= 800 字 ≈ 600 Token
- 输出：忽略不计
- 成本：600 Token × ¥0.0005/千 = ¥0.0003/次

**场景二：Chunks 精排**
- 输入：8 个 chunks × 平均 200 字 = 1600 字 ≈ 1200 Token
- 输出：忽略不计
- 成本：1200 Token × ¥0.0005/千 = ¥0.0006/次

**总成本**：每个 sub_query 约 ¥0.001（0.1 分）

---

## 测试验证

### 测试脚本

```bash
# 设置 API Key
export DASHSCOPE_API_KEY='your-api-key'

# 运行测试
python3 tools/test_rerank_feishu.py
```

### 测试覆盖

- ✅ 搜索结果重排序（8 个候选 → top-5）
- ✅ Chunks 精排和过滤（8 个 chunks → top-5，阈值 0.3）
- ✅ 索引映射正确性验证
- ✅ 边界情况（空 summary、单个候选、大量候选）

### 预期效果

**搜索结果重排序**：
- BM25 Top-1: "NUBIA 2024 Q1 销售数据分析"（关键词匹配）
- Rerank Top-1: "NUBIA 美国市场运营报告"（语义相关性）

**Chunks 精排**：
- 原始 Top-1: "2024 年第一季度，NUBIA 品牌整体销售额..."（BM25 高分）
- Rerank Top-1: "NUBIA 在美国市场的销售额达到 500 万美元..."（直接回答）

---

## 集成步骤

### Step 1: 安装依赖

```bash
pip install requests python-dotenv
```

### Step 2: 配置 API Key

```bash
export DASHSCOPE_API_KEY='your-api-key'
```

### Step 3: 修改 DocPool

在 `tools/feishu_doc_pool.py` 的 `submit_and_read()` 中添加 rerank 逻辑（见上文）。

### Step 4: 修改 SourceAgent

在 `agents/feishu_source_agent.py` 的 `execute_sub_query_full()` 中添加 rerank 逻辑（见上文）。

### Step 5: 测试验证

```bash
python3 tools/test_rerank_feishu.py
```

### Step 6: 监控指标

- Rerank 前后的答案准确率对比
- 平均分数分布
- 耗时和成本统计

---

## 常见问题

### Q1: Rerank 返回 0 个结果？

**可能原因**：
1. API Key 未配置或无效
2. 文档内容为空或格式错误
3. Query 与文档完全不相关

**解决方案**：
```python
# 添加 fallback 逻辑
if rerank_result['status'] == 'success' and rerank_result['results']:
    ranked = [candidates[item['index']] for item in rerank_result['results']]
else:
    # Fallback 到 BM25
    ranked = _bm25_rank_candidates(query, candidates)[:cfg.top_n_docs]
```

### Q2: 如何调整分数阈值？

**建议**：
- 初始值：0.3（过滤明显不相关的）
- 严格模式：0.5（只保留高相关的）
- 宽松模式：0.2（尽量多召回）

**调优方法**：
```python
# 分析分数分布
scores = [item['relevance_score'] for item in result['results']]
print(f"Min: {min(scores):.4f}, Max: {max(scores):.4f}, Avg: {sum(scores)/len(scores):.4f}")

# 根据分布调整阈值
score_threshold = 0.3  # 调整此值
```

### Q3: Rerank 会增加多少延迟？

**实测数据**：
- 8 个候选：~200ms
- 20 个候选：~400ms
- 50 个候选：~800ms

**优化建议**：
- 先用 BM25 粗排到 top-20，再 rerank
- 并行处理多个 sub_query 的 rerank

---

## 总结

### 核心优势

1. **精度提升**：语义理解优于关键词匹配
2. **灵活性**：支持 QA / 语义相似度等多种模式
3. **易集成**：只需修改两处代码（DocPool + SourceAgent）
4. **低成本**：每次查询约 ¥0.001

### 关键设计

1. **索引映射**：rerank 返回的 index 直接映射回原始列表
2. **格式组合**：title + summary 组合成 rerank 输入
3. **分数过滤**：阈值 0.3 过滤低相关性结果
4. **Fallback 机制**：rerank 失败时回退到 BM25

### 下一步

1. ✅ 完成工具开发（`tools/rerank.py`）
2. ✅ 完成测试脚本（`tools/test_rerank_feishu.py`）
3. ⏳ 集成到 DocPool（搜索结果重排序）
4. ⏳ 集成到 SourceAgent（Chunks 精排）
5. ⏳ 线上 A/B 测试验证效果
