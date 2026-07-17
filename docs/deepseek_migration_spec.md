# DeepSeek 模型迁移方案 (v1.0)

> 版本: **v1.0**
> 日期: 2026-07-10
> 状态: **Path B (ADK LlmAgent) 已通过连通性测试**，Path A (call_llm) 待改造

---

## 0. 目标

将 Multi-source RAG Agent 的 LLM 调用从 Google Gemini 迁移到 DeepSeek：

| 组件 | 原模型 (Gemini) | 新模型 (DeepSeek) |
|---|---|---|
| Global Planner v2.2 | `gemini-3.5-flash` | **`deepseek-v4-pro`** |
| Source Agent (plan_only / eval_only / plan_eval) | `gemini-3.1-flash-lite` | **`deepseek-v4-flash`** |
| Eval Layer LLM (deep_dive) | `gemini-3.1-flash-lite` | **`deepseek-v4-flash`** |
| Answer Agent | `gemini-3-flash-preview` | **`deepseek-v4-flash`** |

---

## 1. DeepSeek API 端点

| 端点 | URL | 用途 | 连通性 |
|---|---|---|---|
| OpenAI 兼容 | `https://api.deepseek.com/v1` | litellm `deepseek/` 前缀默认路由 | ✅ 已验证 (HTTP 401 → 服务可达) |
| Anthropic 兼容 | `https://api.deepseek.com/anthropic` | Anthropic Messages API 格式 | ✅ 已验证 (HTTP 401 → 服务可达) |

**决策**: 使用 **标准 OpenAI 兼容端点** `https://api.deepseek.com/v1`。

> ADK 的 `LiteLlm` 适配器通过 `deepseek/` 前缀自动路由到 DeepSeek provider，
> litellm 内部使用 `https://api.deepseek.com/v1` + `DEEPSEEK_API_KEY`。
> `/anthropic` 端点用于 Anthropic 格式的 API，本项目的 OpenAI 兼容调用链不需要它。

---

## 2. LLM 调用拓扑 (当前 vs 改造后)

项目有**两条独立的 LLM 调用路径**：

```
用户 Query
    │
    ├── 路径 A: call_llm()                       ← 需改造
    │   文件: tools/core/llm_client.py
    │   底层: google.genai.Client  →  改为: litellm
    │   调用方:
    │     • global_planner.py   → plan_global()     (v2.2 主入口)
    │     • query_planner.py    → plan_query()      (v1 兜底)
    │     • docx_pipeline.py    → _call_llm_table()
    │   特征: response_schema / thinking_budget / 同步阻塞
    │
    └── 路径 B: ADK LlmAgent                       ← 已可用 ✅
        文件: agents/feishu/feishu_retrieve_llm_build.py (及各源)
        底层: google.adk.agents.LlmAgent  →  model="deepseek/xxx"
        调用方 (每源 3 个):
          • build_*_plan_only_agent()
          • build_*_eval_only_agent()
          • build_*_plan_eval_agent()
        特征: output_schema (Pydantic) / InMemoryRunner
```

### 2.1 路径 B：ADK LlmAgent (已通过测试 ✅)

ADK 2.3.0 内置 `LiteLlm` 适配器，支持 `deepseek/` 前缀：

```python
# 原代码 (Gemini)
LlmAgent(model="gemini-3.1-flash-lite", ...)

# 改为 DeepSeek (只需改 model 字符串)
LlmAgent(model="deepseek/deepseek-v4-flash", ...)
```

ADK 内部路由链: `LlmAgent(model="deepseek/deepseek-v4-flash")` → `LLMRegistry.resolve()` → `LiteLlm` → `litellm.acompletion(model="deepseek-v4-flash")`

**无需改动任何 ADK 调用代码——只改 model 字符串默认值即可。**

### 2.2 路径 A：call_llm (待改造 ⚠️)

`tools/core/llm_client.py` 中的 `call_llm()` 当前硬编码了 `google.genai.Client`：

```python
# llm_client.py 当前实现
from google import genai
_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))

def call_llm(prompt, ...):
    response = _client.models.generate_content(
        model=use_model,           # ← Gemini 模型名
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            thinking_config=...,   # ← Gemini 专属
            response_schema=...,   # ← Gemini 专属
        ),
    )
```

**改造方案**: 将 `call_llm` 改为 litellm 统一接口：

```python
# 改造后 (示意)
import litellm

def call_llm(prompt, model=None, response_schema=None, ...):
    use_model = model or LLM_MODEL  # "deepseek/deepseek-v4-pro" 等

    # response_schema → JSON mode (DeepSeek 不支持 schema 约束)
    if response_schema:
        prompt += "\n\nOutput strictly as JSON matching this schema:\n" + json.dumps(response_schema)
        kwargs["response_format"] = {"type": "json_object"}

    response = litellm.completion(
        model=use_model,
        messages=[{"role": "user", "content": prompt}],
        ...
    )
    return response.choices[0].message.content
```

**Gemini 专有参数处理**:
- `thinking_budget` → DeepSeek 不支持，仅在 `deepseek-reasoner` 模型中可通过 `reasoning_effort` 参数控制（v4-pro 可能支持，待确认）
- `response_schema` → 改为 prompt 内嵌 JSON Schema 指令 + `response_format={"type": "json_object"}`。项目已有 `parse_json_response()` 容错修复逻辑（截断修复、code-fence 清洗），可继续兜底

---

## 3. 配置入口

项目有两个核心配置入口：

### 3.1 `tools/core/models.py` — 数据结构层

```python
# GlobalPlannerConfig (global_planner 模型配置)
@dataclass
class GlobalPlannerConfig:
    model: Literal[...] = "deepseek/deepseek-v4-pro"  # ← 改默认值
    timeout_s: float = 15.0
    max_focuses: int = 6
    fallback_to_v1: bool = True
    force_v1: bool = False

# AgentConfig (外部统一入口)
@dataclass
class AgentConfig:
    planner_model: str = ""     # 空→GlobalPlannerConfig.model 默认 ("deepseek/deepseek-v4-pro")
    eval_model: str = ""        # 空→llm_contracts.MODEL 默认 ("deepseek/deepseek-v4-flash")
    google_api_key: str = ""    # 保留兼容；DeepSeek 场景可复用为 API key 注入通道
    # 新增:
    deepseek_api_key: str = ""  # DeepSeek API Key；空时 fallback 到 DEEPSEEK_API_KEY 环境变量
    deepseek_base_url: str = "" # 自定义 base_url；空时用 litellm 默认 (https://api.deepseek.com/v1)

# RetrieveConfig (内部行为配置)
@dataclass
class RetrieveConfig:
    MODEL: ClassVar[str] = "deepseek/deepseek-v4-flash"  # ← 改默认值
```

### 3.2 `tools/core/llm_contracts.py` — LLM 契约层

```python
# llm_contracts.py
MODEL = "deepseek/deepseek-v4-flash"  # ← 改默认值 (当前 "gemini-3.1-flash-lite")

def set_model(model: str = "") -> None:
    """运行时注入 eval-stage LLM 模型。"""
    global MODEL
    MODEL = model or _DEFAULT_MODEL
```

### 3.3 `root_agent_config.py` — 本地启动入口

```python
# root_agent_config.py
def build_root_retrieve_agent(name="retrieve_v1"):
    load_project_env()
    # ...从环境变量读取 RAG_PRESET / RAG_ENABLED_SOURCES
    # 模型现在通过 RetrieveConfig 默认值走 DeepSeek，无需改动本文件
```

---

## 4. 环境变量

```bash
# .env 文件需要新增 / 变更的字段

# ── DeepSeek API ──────────────────────────────────────
DEEPSEEK_API_KEY=sk-xxxxxxxx          # DeepSeek API Key (litellm 自动读取)
# DEEPSEEK_BASE_URL=                  # 可选：自定义 API 网关地址

# ── Google API (过渡期保留) ──────────────────────────
GOOGLE_API_KEY=...                    # 保留，但 DeepSeek 主路径不再使用
GEMINI_MODEL=...                      # 保留，但 llm_contracts.MODEL 覆盖
GEMINI_FALLBACK_MODEL=...             # 保留，但不再使用

# ── Feishu API (不变) ─────────────────────────────────
FEISHU_UAT=t-xxx                      # 飞书 User Access Token
FEISHU_MCP_ENDPOINT=https://mcp.feishu.cn/mcp  # 已验证可用

# ── Rerank (不变) ────────────────────────────────────
DASHSCOPE_API_KEY=sk-xxx              # Qwen3-Rerank API Key
```

> **注**: litellm 自动从 `DEEPSEEK_API_KEY` 环境变量读取 API Key。
> ADK LiteLlm → litellm 链路不需要额外配置。

---

## 5. 依赖

```txt
# requirements-integration.txt 新增
litellm                    # ADK LiteLlm 底层 + call_llm 改造后使用
openai                     # litellm 自动安装 (已含)
```

`litellm` 已安装 (v1.91.1)，联通性测试通过。

---

## 6. 改造步骤 (建议顺序)

### Phase 1: 路径 B — ADK LlmAgent (低风险，可立即执行)
1. 改 `tools/core/llm_contracts.py`: `MODEL = "deepseek/deepseek-v4-flash"`
2. 改 `tools/core/models.py`: `GlobalPlannerConfig.model` 默认值 → `"deepseek/deepseek-v4-pro"`
3. 改 `tools/core/models.py`: `RetrieveConfig.MODEL` → `"deepseek/deepseek-v4-flash"`
4. 改 `tools/core/models.py`: `AgentConfig` 新增 `deepseek_api_key` / `deepseek_base_url` 字段
5. 改 `retrieve_agent.py` `_apply_runtime_config`: 注入 `DEEPSEEK_API_KEY` 环境变量

### Phase 2: 路径 A — call_llm (需要适配层)
1. 改 `tools/core/llm_client.py`: `call_llm` 底层从 `google.genai.Client` 切换到 `litellm`
2. 处理 `response_schema` → prompt 内嵌 + `response_format={"type": "json_object"}`
3. 处理 `thinking_budget` → DeepSeek 兼容策略
4. 改 `GlobalPlannerConfig.model` 类型标注 (Literal 扩展为 str，支持任意 litellm 模型名)
5. 回退兼容：保留 `model.startswith("gemini")` 时走老 `google.genai` 路径

### Phase 3: 清理 (全部验证后)
1. 移除 `google_api_key` 强制依赖（改为可选）
2. 清理 `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` 环境变量引用

---

## 7. 连通性测试结果 (2026-07-10)

| 测试项 | 模型 | 结果 |
|---|---|---|
| litellm 直调 | `deepseek/deepseek-v4-flash` | ✅ 通过 |
| ADK LlmAgent | `deepseek/deepseek-v4-flash` | ✅ 通过 — 回复 "DeepSeek works!" |
| ADK LlmAgent | `deepseek/deepseek-v4-pro` | ✅ 通过 — 回复 "DeepSeek works!" |

测试脚本已删除 (`_test_deepseek_adk.py`)，验证逻辑保留在本节供参考：

```python
# 最简 ADK DeepSeek 验证代码 (已通过)
from google.adk.agents.llm_agent import LlmAgent
agent = LlmAgent(
    name="test",
    model="deepseek/deepseek-v4-flash",  # 或 deepseek/deepseek-v4-pro
    instruction="Reply to the user's message concisely.",
)
# 通过 InMemoryRunner 运行即可
```

---

## 8. 已知差异与风险

| Gemini 特性 | DeepSeek 替代方案 | 风险 |
|---|---|---|
| `response_schema` (强制 JSON Schema) | `response_format={"type": "json_object"}` + prompt 内嵌 schema | 🟡 中等：不保证 key/结构，但项目已有 `parse_json_response` 容错 |
| `thinking_budget` | `reasoning_effort` (仅 deepseek-reasoner) | 🟢 低：planner 场景可暂时禁用 thinking |
| `GoogleLlm` 原生支持 | ADK LiteLlm 适配层 | 🟢 低：litellm 成熟稳定，ADK 官方支持 |
| Gemini safety filters | DeepSeek 无等效 | 🟢 低：本项目不依赖 safety filter |

---

## 9. 参考

- ADK LiteLlm 源码: `google/adk/models/lite_llm.py` (LiteLlm 类, line 2262)
- ADK 模型注册: `google/adk/models/__init__.py` → `_LAZY_PROVIDERS["LiteLlm"]`
- litellm DeepSeek 文档: https://docs.litellm.ai/docs/providers/deepseek
- 项目 LLM 契约: `tools/core/llm_contracts.py`
- 项目配置模型: `tools/core/models.py` (AgentConfig / RetrieveConfig / GlobalPlannerConfig)
