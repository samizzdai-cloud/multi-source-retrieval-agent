# Answer Agent — 模拟实现规格

> 目的: 在 RetrievalAgent 之外封装一个简单的回答层，用于端到端测试和 demo。
> 本文档仅描述 Answer Agent 层的实现规格，不影响 RetrievalAgent 自身的架构。

---

## 1. 整体架构

```
用户 query (+ 可选 session context / config)
        ↓
root_agent  (ADK SequentialAgent)
    │
    ├─ [Step 1] RetrievalAgent  (Custom BaseAgent, 已实现)
    │           职责: query planning → 并行检索 → rerank → 返回 chunks
    │           输出: RetrievalResult JSON 写入 session.state["retrieval_result"]
    │           打印: 全程进度（plan/检索/rerank 各阶段耗时、chunk 预览）
    │
    └─ [Step 2] AnswerAgent  (ADK LlmAgent, 本文档)
                职责: 读取 session.state["retrieval_result"] → 生成 NL 回答
                输出: 自然语言回答 + 来源引用列表
```

**重点考察对象仍是 RetrievalAgent**，AnswerAgent 只是消费其输出。

---

## 2. 数据流

```
Session State 传递:

RetrievalAgent
  → Event(actions=EventActions(state_delta={
      "retrieval_result": RetrievalResult JSON,
      "retrieval_meta": {
          "plan_ms": int,
          "retrieve_ms": int,
          "rerank_ms": int,
          "total_ms": int,
          "raw_chunks": int,
          "final_chunks": int,
          "source_stats": dict,
      }
    }))

AnswerAgent (LlmAgent)
  → instruction 模板中注入:
      {retrieval_result}   # chunks JSON
      {retrieval_meta}     # 耗时统计
  → Gemini 生成回答
```

---

## 3. RetrievalAgent 改动（最小化）

在 `retrieval_agent.py` 的 `_make_event()` 中，将 `RetrievalResult` 和性能元数据写入 `state_delta`：

```python
def _make_event(self, ctx, result: RetrievalResult, meta: dict = None) -> Event:
    result_json = json.dumps(asdict(result), ensure_ascii=False, default=str)
    state_delta = {"retrieval_result": result_json}
    if meta:
        state_delta["retrieval_meta"] = meta
    return Event(
        invocation_id=ctx.invocation_id,
        author=self.name,
        branch=ctx.branch,
        content=types.Content(role="model", parts=[types.Part(text=result_json)]),
        actions=EventActions(state_delta=state_delta),
    )
```

`meta` 在 `_run_async_impl` 末尾构建：
```python
meta = {
    "plan_ms": plan_ms,
    "retrieve_ms": retrieve_ms,
    "rerank_ms": rerank_ms,
    "total_ms": total_ms,
    "raw_chunks": total_before_filter,
    "final_chunks": len(final_chunks),
    "source_stats": source_stats,
}
yield self._make_event(ctx, result, meta)
```

---

## 4. AnswerAgent 规格

### 4.1 文件

`answer_agent.py` — 项目根目录

### 4.2 类型

`google.adk.agents.llm_agent.Agent` (LlmAgent)

### 4.3 Instruction 模板

```
你是一个知识问答助手。根据已检索到的知识片段回答用户问题。

## 检索性能
{retrieval_meta}

## 检索到的知识片段
{retrieval_result}

## 回答规范
1. 直接回答用户问题，基于上方知识片段的 text 内容
2. 每条引用标注来源：[文档名 > 章节路径]
3. 若 is_sufficient=false，在末尾注明：⚠️ 部分信息可能不完整
4. 不要重复展示原始 JSON，用自然语言综合表达
5. 若知识片段中没有相关信息，直接说明无法回答
```

### 4.4 模型

`gemini-3-flash-preview`（与 RetrievalAgent 的 query planning 共用同一模型）

### 4.5 输出格式（示例）

```
nubia 2025 Q4 广告 ROAS 分析：

根据 NUBIA 业务总结报告（2026-01-15 ~ 2026-01-21）：
- Google Ads ROAS 为 7.8（环比 +12%）
- Blended ROAS 为 6.40（广告预算缩减 74% 后提升）
- Z80 Ultra 品类表现最佳，CTR 4.2%

[来源]
① NUBIA 业务总结报告 (2026-01-15 ~ 2026-01-21) > 2.核心业务数据 > Google广告国家漏斗分布
② Weekly meeting 12 > Sales & Traffic Overview

[检索性能]
总耗时: 4374ms (Plan=3502ms | 检索=869ms | Rerank=1ms)
Raw chunks: 8 → Final: 8
```

---

## 5. root_agent 改动

```python
# root_agent.py

from google.adk.agents import SequentialAgent
from retrieval_agent import RetrievalAgent
from answer_agent import answer_agent
from tools.evaluator import EvalConfig

_retrieval_agent = RetrievalAgent(
    name="retrieval",
    eval_config=EvalConfig(
        min_good_chunks=5,
        min_relevance_score=0.3,
        hard_timeout_seconds=15.0,
        min_sources=1,
    ),
    top_k=10,
)

root_agent = SequentialAgent(
    name="context_rag_agent",
    description="多源并行检索 + 知识问答 Agent",
    sub_agents=[_retrieval_agent, answer_agent],
)
```

---

## 6. 端到端测试脚本规格

`tests/test_e2e_answer.py`

```
输入: query string (CLI 参数)
输出:
  [STEP 0] 配置检查
  [STEP 1] RetrievalAgent 全程进度（plan/检索/rerank 耗时 + chunk 预览）
  [STEP 2] AnswerAgent 输入摘要（chunk 数、focus 词）
  [STEP 3] 最终 NL 回答
  [STEP 4] 性能总结（Plan/检索/Rerank/LLM 各阶段耗时）
  [STEP 5] chunk 字段完整性报告
```

---

## 7. 不做的事（范围约束）

- AnswerAgent 不做 query 改写（由 RetrievalAgent 内部的 query_planner 负责）
- AnswerAgent 不做多轮对话（session context 暂时不传入）
- AnswerAgent 不做流式输出（B6 已排除）
- 不新增数据模型，直接复用 RetrievalResult JSON

---

## 8. 实现顺序

1. 修改 `retrieval_agent.py` — 增加 `meta` 写入 `state_delta`
2. 新建 `answer_agent.py` — LlmAgent 定义
3. 修改 `root_agent.py` — SequentialAgent 包装
4. 新建 `tests/test_e2e_answer.py` — 端到端测试
5. 运行验证
