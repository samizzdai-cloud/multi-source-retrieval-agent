# Qwen3-Rerank 工具使用指南

## 概述

Qwen3-Rerank 是阿里云提供的文档重排序模型，用于对召回的文档进行精准二次排序，显著提升 RAG 应用和搜索系统的准确率。

## 核心特性

- **高精度排序**：基于 Qwen3 基座模型，支持 100+ 语言
- **任务自适应**：通过 `instruct` 参数支持不同排序策略（QA、语义相似度、代码检索等）
- **大规模支持**：qwen3-rerank 支持单次 500 文档，每文档 4000 Token
- **成本优化**：¥0.0005/千Token，比 embedding 更经济

## 快速开始

### 1. 基础用法

```python
from tools.rerank import rerank_documents

# QA 模式：找答案
result = rerank_documents(
    query="如何预防感冒？",
    documents=[
        "勤洗手是预防感冒的有效方法",
        "感冒是一种常见疾病",
        "多喝水可以增强免疫力"
    ],
    top_n=2,
    return_documents=True
)

# 查看结果
for item in result['results']:
    print(f"Score: {item['relevance_score']:.4f}")
    print(f"Text: {item['document']['text']}")
```

### 2. RAG 场景

```python
from tools.rerank import rerank_for_rag

# 对检索到的 chunks 进行重排序
chunks = [
    {"text": "asyncio 是 Python 的异步库", "source": "doc1"},
    {"text": "requests 是同步 HTTP 库", "source": "doc2"},
    {"text": "异步编程提高 I/O 性能", "source": "doc3"}
]

reranked = rerank_for_rag(
    query="Python 异步编程的优势",
    chunks=chunks,
    top_n=2,
    score_threshold=0.3  # 过滤低分 chunk
)

# 每个 chunk 添加了 rerank_score 字段
for chunk in reranked:
    print(f"Score: {chunk['rerank_score']:.4f}")
    print(f"Text: {chunk['text']}")
```

### 3. FAQ 匹配

```python
from tools.rerank import rerank_for_faq

# 找到与用户问题最相似的标准问题
matches = rerank_for_faq(
    user_question="如何修改密码？",
    faq_questions=[
        "忘记密码怎么办？",
        "怎样更改登录密码？",
        "如何注册新账号？"
    ],
    top_n=2
)

for match in matches:
    print(f"Score: {match['score']:.4f}")
    print(f"FAQ: {match['question']}")
```

## 任务模式详解

### QA 模式（默认）

**适用场景**：用户提问，需要找到答案

```python
result = rerank_documents(
    query="如何预防感冒？",
    documents=["勤洗手是预防感冒的有效方法", "感冒是常见疾病"],
    instruct_template="qa"  # 或不传，默认就是 qa
)
```

**评分侧重**：
- ✅ 高分：文档**直接回答**了问题
- ❌ 低分：文档虽然相关，但没有提供答案

**示例对比**：
- Query: "如何预防感冒？"
- 高分文档："勤洗手是预防感冒的有效方法" → 0.93（直接回答）
- 低分文档："感冒是一种常见疾病" → 0.34（相关但不回答）

### 语义相似度模式

**适用场景**：FAQ 匹配、语义等价判断

```python
result = rerank_documents(
    query="如何修改密码？",
    documents=["忘记密码怎么办？", "怎样更改登录密码？"],
    instruct_template="semantic"
)
```

**评分侧重**：
- ✅ 高分：语义**等价**或**高度相似**
- ❌ 低分：语义不同，即使主题相关

**示例对比**：
- Query: "如何修改密码？"
- 高分文档："怎样更改登录密码？" → 0.95（语义等价）
- 低分文档："如何注册账号？" → 0.25（主题不同）

### 代码检索模式

**适用场景**：代码搜索、技术文档检索

```python
result = rerank_documents(
    query="如何读取文件内容",
    documents=[
        "with open('file.txt', 'r') as f: content = f.read()",
        "Python 文件操作使用 open() 函数"
    ],
    instruct_template="code"
)
```

### 自定义 Instruct

**高级用法**：自定义排序策略

```python
result = rerank_documents(
    query="量子计算的应用",
    documents=[...],
    instruct="Given a scientific query, retrieve relevant research papers."
)
```

**预定义模板**：
```python
from tools.rerank import INSTRUCT_TEMPLATES

print(INSTRUCT_TEMPLATES)
# {
#     "qa": "Given a web search query, retrieve relevant passages that answer the query.",
#     "semantic": "Retrieve semantically similar text.",
#     "code": "Given a code search query, retrieve relevant code snippets.",
#     "classification": "Given a classification query, retrieve relevant documents.",
#     "fact_verification": "Given a claim, retrieve documents that support or refute the claim."
# }
```

## 参数详解

### rerank_documents()

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | 必填 | 查询文本（用户问题或搜索关键词） |
| `documents` | List[str] \| List[dict] | 必填 | 待排序的文档列表 |
| `model` | str | "qwen3-rerank" | 模型名称 |
| `top_n` | int \| None | None | 返回前 N 个，None 返回全部 |
| `instruct` | str \| None | None | 自定义任务指令（优先级高） |
| `instruct_template` | str \| None | None | 预定义模板名称 |
| `return_documents` | bool | False | 是否返回文档原文 |
| `api_key` | str \| None | None | API Key，优先参数值 |
| `timeout` | float | 30.0 | 请求超时时间（秒） |

### rerank_for_rag()

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `query` | str | 必填 | 用户查询 |
| `chunks` | List[dict] | 必填 | 检索到的 chunk 列表 |
| `top_n` | int | 5 | 返回前 N 个 |
| `score_threshold` | float | 0.3 | 相关性阈值，低于此值被过滤 |
| `text_field` | str | "text" | chunk 中文本字段的 key 名称 |

### rerank_for_faq()

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `user_question` | str | 必填 | 用户提问 |
| `faq_questions` | List[str] | 必填 | FAQ 标准问题列表 |
| `top_n` | int | 3 | 返回前 N 个最相似的 |

## 返回格式

### 成功响应

```python
{
    "status": "success",
    "results": [
        {
            "index": 0,              # 原始文档索引
            "relevance_score": 0.95, # 相关性评分 [0.0, 1.0]
            "document": {            # 文档原文（仅 return_documents=True）
                "text": "..."
            }
        }
    ],
    "usage": {
        "total_tokens": 123
    },
    "request_id": "xxx",
    "_debug": {
        "model": "qwen3-rerank",
        "instruct": "...",
        "input_count": 10,
        "output_count": 5
    }
}
```

### 失败响应

```python
{
    "status": "error",
    "message": "错误信息",
    "code": "错误码"
}
```

## 模型对比

| 模型 | 最大文档数 | 单条最大Token | 价格 | 推荐场景 |
|------|-----------|--------------|------|---------|
| **qwen3-rerank** | 500 | 4,000 | ¥0.0005/千Token | **推荐**：文本语义检索、RAG |
| gte-rerank-v2 | 30,000 | - | ¥0.0008/千Token | 大批量文档 |
| qwen3-vl-rerank | 100 | 8,000 | ¥0.0007/千Token | 多模态（图片/视频） |

## 最佳实践

### 1. 分数阈值设置

```python
# RAG 场景：过滤低相关性 chunk
reranked = rerank_for_rag(
    query=query,
    chunks=chunks,
    score_threshold=0.3  # 建议 0.2-0.4
)
```

**经验值**：
- `>= 0.7`：高度相关，可直接使用
- `0.3 - 0.7`：中等相关，需结合上下文
- `< 0.3`：低相关，建议过滤

### 2. 两阶段检索

```python
# Stage 1: 向量检索（召回）
from tools.embedding import batch_embed
embeddings = batch_embed(documents, api_key)
top_100 = vector_search(query_emb, embeddings, top_k=100)

# Stage 2: Rerank（精排）
top_10 = rerank_for_rag(
    query=query,
    chunks=top_100,
    top_n=10,
    score_threshold=0.3
)
```

### 3. 批量处理

```python
# 对多个 query 批量 rerank（串行）
results = []
for query in queries:
    result = rerank_documents(query=query, documents=documents, top_n=5)
    results.append(result)
```

### 4. 错误处理

```python
result = rerank_documents(query=query, documents=documents)

if result['status'] == 'error':
    logger.warning(f"Rerank 失败: {result['message']}")
    # Fallback 到原始顺序
    return documents[:top_n]
else:
    return [documents[item['index']] for item in result['results']]
```

## 性能优化

### 1. 文档数量控制

```python
# 先用 BM25 粗排，再用 rerank 精排
from rank_bm25 import BM25Okapi

bm25 = BM25Okapi([doc.split() for doc in documents])
top_100_indices = bm25.get_top_n(query.split(), range(len(documents)), n=100)
top_100_docs = [documents[i] for i in top_100_indices]

# Rerank 只处理 top-100
result = rerank_documents(query=query, documents=top_100_docs, top_n=10)
```

### 2. 文档长度截断

```python
# 超长文档截断到 4000 Token（约 3000 字）
def truncate_doc(text: str, max_chars: int = 3000) -> str:
    return text[:max_chars] if len(text) > max_chars else text

truncated_docs = [truncate_doc(doc) for doc in documents]
result = rerank_documents(query=query, documents=truncated_docs)
```

### 3. 缓存策略

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def cached_rerank(query: str, docs_tuple: tuple) -> dict:
    """缓存 rerank 结果（相同 query + documents 组合）"""
    return rerank_documents(query=query, documents=list(docs_tuple))

# 使用时将 list 转为 tuple（可哈希）
result = cached_rerank(query, tuple(documents))
```

## 常见问题

### Q1: Rerank 和 Embedding 的区别？

| 维度 | Embedding | Rerank |
|------|-----------|--------|
| **输入** | 单个文本 | Query + Document 对 |
| **输出** | 向量（可缓存） | 相关性分数（不可缓存） |
| **速度** | 快（批量并行） | 慢（逐对计算） |
| **精度** | 中等 | 高 |
| **成本** | 低 | 中等 |
| **使用场景** | 召回阶段 | 精排阶段 |

**推荐流程**：Embedding 召回 → Rerank 精排

### Q2: 什么时候需要 Rerank？

**需要 Rerank**：
- ✅ RAG 应用中，召回结果质量不稳定
- ✅ 搜索结果需要高精度排序
- ✅ FAQ 匹配需要语义等价判断

**不需要 Rerank**：
- ❌ 召回结果已经很准确
- ❌ 对延迟要求极高（< 100ms）
- ❌ 文档数量极大（> 1000）且预算有限

### Q3: 如何选择 instruct_template？

| 场景 | 推荐模板 | 说明 |
|------|---------|------|
| RAG 问答 | `qa` | 找答案 |
| FAQ 匹配 | `semantic` | 语义等价 |
| 代码搜索 | `code` | 代码片段检索 |
| 文档分类 | `classification` | 分类标签匹配 |
| 事实验证 | `fact_verification` | 支持/反驳判断 |

### Q4: 分数不稳定怎么办？

**原因**：
- 分数是**相对值**，只在单次请求内有意义
- 不同请求的分数不可直接比较

**解决方案**：
```python
# 使用分数差值而非绝对值
results = result['results']
if len(results) >= 2:
    score_gap = results[0]['relevance_score'] - results[1]['relevance_score']
    if score_gap > 0.2:
        print("Top-1 显著优于 Top-2")
```

## 测试

运行测试脚本：

```bash
# 设置 API Key
export DASHSCOPE_API_KEY='your-api-key'

# 运行测试
cd tools
python test_rerank.py
```

测试覆盖：
- ✅ QA 模式
- ✅ 语义相似度模式
- ✅ RAG 场景
- ✅ FAQ 匹配
- ✅ 自定义 instruct
- ✅ 边界情况

## 参考资料

- [阿里云 Rerank 模型文档](https://help.aliyun.com/zh/model-studio/rerank-model)
- [Qwen3-Embedding GitHub](https://github.com/QwenLM/Qwen3-Embedding)
- [MTEB 排行榜](https://huggingface.co/spaces/mteb/leaderboard)

## 更新日志

### v1.0.0 (2025-01-XX)

- ✅ 支持 qwen3-rerank 模型
- ✅ 支持 QA / 语义相似度 / 代码检索等多种模式
- ✅ 提供 RAG 和 FAQ 场景的便捷函数
- ✅ 完整的错误处理和日志记录
- ✅ 单元测试覆盖
