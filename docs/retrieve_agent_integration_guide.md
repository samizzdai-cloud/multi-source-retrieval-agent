# Retrieve Agent Integration Guide

> 适用入口：`retrieve_agent_client.py`
>
> 目的：给母项目一个单文件接入说明，涵盖：
> - 如何调用 `retrieve_agent`
> - `chunks` 返回格式
> - Feishu / Google 认证与依赖
> - Google scopes
> - 支持的解析类型（docx / sheet / slides / gmail / 等）
> - 大模型 `api_key` 从哪里传入
> - 推荐安装清单 `requirements-integration.txt`

---

## 1. 推荐调用入口

母项目直接调用：

- [retrieve_agent_client.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent_client.py:1)

推荐使用这几个函数：

- `config(...)`
- `request(...)`
- `invoke_retrieve_agent_sync(...)`
- `invoke_retrieve_agent(...)`

最小示例：

```python
from retrieve_agent_client import config, request, invoke_retrieve_agent_sync

cfg = config(
    preset="balanced",
    sources=["google", "feishu", "bailian"],

    google_api_key="YOUR_GOOGLE_API_KEY",
    dashscope_api_key="YOUR_DASHSCOPE_API_KEY",

    feishu_uat="YOUR_FEISHU_UAT",
    feishu_open_id="YOUR_FEISHU_OPEN_ID",

    bailian_workspace_id="YOUR_BAILIAN_WORKSPACE_ID",
    bailian_access_key_id="YOUR_BAILIAN_AK",
    bailian_access_key_secret="YOUR_BAILIAN_SK",
    bailian_index_map="nubia:idx_a,hypershell:idx_b",

    google_credentials_path="/abs/path/to/credentials.json",
    google_token_path="/abs/path/to/token.json",

    load_from_env=False,
)

req = request(
    user_query="本周期 Nubia Z80 Ultra 的净销量和净销售额分别是多少？"
)

result = invoke_retrieve_agent_sync(cfg, req)
print(result.metadata)
print(result.chunks[:2])
```

---

## 2. 能否成功接收 `retrieve_agent` 返回的 chunks

可以。

当前 `retrieve_agent_client.py` 会把 `retrieve_agent` 的 `state_delta["retrieval_result"]` 解析成：

- `result.raw_payload`
- `result.chunks`
- `result.metadata`
- `result.progress_events`

相关代码：

- [retrieve_agent_client.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent_client.py:95)
- [tools/core/final_assembler.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/final_assembler.py:409)

我们已经实际跑通过一次真实调用，成功拿到了：

- `chunk_count = 6`
- `result.chunks[0]` 为有效 `critical` chunk

---

## 3. `chunks` 返回格式

`retrieve_agent` 当前出口 schema 为：

```json
{
  "query": "用户原问题",
  "chunks": [
    {
      "text": "chunk正文",
      "source_name": "文档名",
      "source_url": "原文链接",
      "source_tool": "feishu | google | bailian | notion",
      "heading_path": "章节路径",
      "confidence_tier": "critical | important | noise",
      "focus_query": ["focus文本1", "focus文本2"],
      "sub_query": "命中的子查询",
      "chunk_id": "稳定chunk id"
    }
  ]
}
```

字段出处：

- [tools/core/final_assembler.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/final_assembler.py:417)

说明：

- `text`: 最终给回答层或母项目消费的正文
- `source_name`: 文档名 / 文件名
- `source_url`: 原始链接
- `source_tool`: 来源系统
- `heading_path`: 章节或结构路径
- `confidence_tier`: 检索层给出的置信等级
- `focus_query`: 该 chunk 回答的是哪些 focus
- `sub_query`: 该 chunk 命中的子查询
- `chunk_id`: 稳定主键，可用于日志、去重、追踪

---

## 4. 顶层返回对象

`invoke_retrieve_agent_sync(...)` 返回 `RetrieveInvocationResult`：

- `query`
- `raw_payload`
- `chunks`
- `metadata`
- `progress_events`

定义位置：

- [tools/core/models.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/models.py:116)

当前 `metadata` 主要来自 `raw_payload` 除去 `chunks` 后的顶层字段。
现阶段至少稳定包含：

- `query`

如果后续要扩展 `source_stats / total_time_ms / picked_count`，建议继续在 `retrieval_result` 顶层加字段，再由 `retrieve_agent_client.py` 透传。

---

## 5. 大模型 API Key 从哪里传入

现在推荐统一从 `config(...)` 传入，不依赖本地 `.env`。

### 5.1 Gemini / ADK

传入字段：

- `google_api_key`
- `gemini_model`
- `gemini_fallback_model`

用途：

- `google_api_key`: Gemini API / ADK LlmAgent 使用
- `gemini_model`: 传统 `llm_client` 默认模型
- `gemini_fallback_model`: fallback 模型

相关注入逻辑：

- [retrieve_agent.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent.py:61)
- [tools/core/llm_client.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/llm_client.py:28)

### 5.2 DashScope / Qwen Rerank

传入字段：

- `dashscope_api_key`

用途：

- Feishu / Google 等候选重排

相关注入逻辑：

- [retrieve_agent.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent.py:80)
- [tools/core/rerank.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/rerank.py:70)

结论：

- 母项目里**不需要**额外再设 `.env`
- 直接在 `config(...)` 里显式传：
  - `google_api_key`
  - `dashscope_api_key`

---

## 6. Google 依赖项

### 6.1 鉴权输入

Google 当前保留文件路径模式，不走原始 token 字符串模式。

母项目需传：

- `google_credentials_path`
- `google_token_path`

相关代码：

- [google_workspace_tool/auth_utils.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/auth_utils.py:21)

### 6.2 OAuth scopes

代码当前 `tools/google/google_retrieve.py` 里写死的 scopes 是：

```python
_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]
```

出处：

- [tools/google/google_retrieve.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/google/google_retrieve.py:28)

但从**实际 API 使用面**来看，这个说明还不够完整。当前代码不只是调 Drive 搜索，还实际调用了：

- `drive v3`
- `gmail v1`
- `docs v1`
- `sheets v4`
- `slides v1`

相关代码位置：

- [google_workspace_tool/drive_search_tool.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/drive_search_tool.py:35)
- [google_workspace_tool/gmail_search_tool.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/gmail_search_tool.py:44)
- [google_workspace_tool/readers/doc_reader.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/readers/doc_reader.py:88)
- [google_workspace_tool/readers/sheet_reader.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/readers/sheet_reader.py:42)
- [google_workspace_tool/readers/slide_reader.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/google_workspace_tool/readers/slide_reader.py:500)

因此建议你把 Google scopes 分成两档理解。

#### A. 当前代码常量（当前主线实际会申请）

- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/gmail.readonly`

#### B. 建议的完整授权范围（按实际 API 面）

- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/gmail.readonly`
- `https://www.googleapis.com/auth/documents.readonly`
- `https://www.googleapis.com/auth/spreadsheets.readonly`
- `https://www.googleapis.com/auth/presentations.readonly`

为什么建议补齐：

- 代码确实直接调用了 Docs / Sheets / Slides API
- 某些租户或 OAuth 配置下，只给 `drive.readonly` 可能仍能列文件，但在读取 Docs / Sheets / Slides 细节时出现 403 或 scope 不足
- 母项目如果要稳定接入，建议直接按完整授权范围申请，减少后续排障

作用说明：

- `drive.readonly`
  - 读取 Google Docs / Sheets / Slides
  - 搜索 Drive 文件
  - 下载或读取支持的 Office / text 文件
- `gmail.readonly`
  - 检索 Gmail 线程
  - 读取 Gmail 正文
  - 在开启附件路径时读取 Gmail 附件内容

- `documents.readonly`
  - 读取 Google Docs 正文与结构

- `spreadsheets.readonly`
  - 读取 Google Sheets metadata / values / batchGet

- `presentations.readonly`
  - 读取 Google Slides 页面与文本内容

如果母项目只想用 Drive，不想碰 Gmail，可以从代码层再裁掉 Gmail 路径；但如果保留当前 Google 主线实现，建议按完整授权范围一次性配齐。

### 6.3 Google 支持的文件类型

当前 Google 检索层支持：

- Google Docs
- Google Sheets
- Google Slides
- Word (`.docx` / `.doc`)
- Text (`.txt`)
- CSV
- Excel (`.xlsx` / `.xls` / macro-enabled`)
- PowerPoint (`.pptx`)
- Gmail 正文
- Gmail 附件

文件类型定义与 MIME 支持：

- [tools/google/google_retrieve.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/google/google_retrieve.py:5)
- [tools/google/google_retrieve.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/google/google_retrieve.py:34)

### 6.4 Google 解析链路

Google SourceAgent 解析族大致分为：

- `doc`
  - Google Doc / Word / txt
- `sheet`
  - Google Sheet / Excel / CSV
- `slide`
  - Google Slides / PPTX
- `gmail`
  - 正文
  - 附件

主分发逻辑在：

- [agents/google/google_source_agent.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/agents/google/google_source_agent.py:255)

可视作当前“Google 处理程序的解析包 / reader 族”：

- `google_workspace_tool/readers/doc_reader.py`
- `google_workspace_tool/readers/word_reader.py`
- `google_workspace_tool/readers/sheet_reader.py`
- `google_workspace_tool/readers/excel_reader.py`
- `google_workspace_tool/readers/slide_reader.py`
- `google_workspace_tool/readers/pptx_reader.py`
- `google_workspace_tool/readers/text_reader.py`
- `google_workspace_tool/gmail_rag_pipeline.py`

---

## 7. Feishu 依赖项

### 7.1 鉴权输入

母项目需要传：

- `feishu_uat`
- `feishu_open_id` 可选

说明：

- `feishu_uat` 是当前代码主用的 `user_access_token`
- `feishu_open_id` 主要用于 `author_filter="me"` / `personal_recent`

相关代码：

- [tools/core/models.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/tools/core/models.py:56)
- [retrieve_agent.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent.py:476)

### 7.2 Feishu 需要覆盖的读取范围

当前 FeishuSourceAgent 不是单纯只读 docx，它会覆盖：

- 云文档 `doc/docx`
- 电子表格 `SHEET`
- 多维表 `BITABLE`
- wiki / 知识空间节点
- 可选 comments
- 可选 whiteboard 嵌入块

综述文档：

- [docs/feishu_tool_spec.md](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/docs/feishu_tool_spec.md:1)

### 7.3 Feishu 所需权限范围

从代码和本地参考文档看，母项目对应的 Feishu 应用 / 用户授权至少要覆盖这些“读取能力”：

- 文档读取权限
- 表格读取权限
- 多维表读取权限
- wiki 节点访问权限
- 文档搜索权限
- 如果要保留 comment / whiteboard 嵌入解析：
  - 评论读取权限
  - 白板节点读取权限

重要说明：

- 当前代码使用的是 `user_access_token` 模式为主
- 除了 API scope，本身还要求该 token 对目标文档有实际可见权限

本地参考里能明确看到的点：

- 文档读取相关 API
- sheet metadata / sheet range / find_cells
- bitable records search
- wiki node info
- comments
- whiteboard nodes

参考：

- [docs/feishu_tool_spec.md](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/docs/feishu_tool_spec.md:255)
- [feishu_read_tool/reference/doc_folder_reference/获取所有块（区别获取子块).md](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/feishu_read_tool/reference/doc_folder_reference/获取所有块（区别获取子块).md:2638)

如果你后面要让平台同学配 Feishu Open Platform 权限，建议按“文档 + 表格 + 多维表 + 知识库 + 搜索 + 评论读”这组去申请，避免后续某条解析支路因为 scope 缺失退化。

### 7.4 Feishu 解析链路

Feishu 当前解析分两大类：

- `doc`
  - `block_api`
  - `MCP fetch-doc`
- `table`
  - `SHEET`
  - `BITABLE`

细节见：

- [docs/feishu_tool_spec.md](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/docs/feishu_tool_spec.md:18)

相关处理程序：

- `tools/feishu/feishu_retrieve.py`
- `tools/feishu/feishu_doc_pool.py`
- `tools/feishu/table_pool.py`
- `feishu_read_tool/docx_pipeline.py`
- `feishu_read_tool/sheet_reader.py`
- `feishu_read_tool/bitable_reader.py`

支持的主要解析对象：

- docx 文档块
- sheet tab
- bitable records
- wiki 命中后的实体文档
- 可选 embedded sheet / bitable / whiteboard / comments

---

## 8. 母项目最小必传字段

如果母项目只调用 `retrieve_agent`，建议最少传这些：

### 公共

- `preset`
- `sources`
- `google_api_key`
- `dashscope_api_key`

### Feishu

- `feishu_uat`
- `feishu_open_id` 可选

### Google

- `google_credentials_path`
- `google_token_path`

### Bailian

- `bailian_workspace_id`
- `bailian_access_key_id`
- `bailian_access_key_secret`
- `bailian_index_map`

### 单次请求

- `user_query`

---

## 9. 需要 pip 安装哪些东西

如果母项目只是“调用这个仓库提供的 retrieve 接口”，建议至少准备下面这些依赖。

### 9.1 核心运行依赖

- `google-adk`
- `google-genai`
- `python-dotenv`
- `requests`

用途：

- `google-adk` / `google-genai`
  - `retrieve_agent` / `answer_agent` / `InMemoryRunner` / `LlmAgent`
- `python-dotenv`
  - 本仓库里部分旧路径仍会读 `.env`
- `requests`
  - Feishu / rerank / 若干 HTTP 调用

### 9.2 Google 认证与 API 依赖

- `google-api-python-client`
- `google-auth`
- `google-auth-oauthlib`
- `google-auth-httplib2`
- `httplib2`

用途：

- Drive / Gmail / Docs / Sheets / Slides API
- 本地 OAuth2 refresh / browser flow

### 9.3 Google Office / 文档解析依赖

- `python-docx`
- `python-pptx`
- `openpyxl`
- `pandas`

用途：

- `python-docx`
  - 解析 `.docx`
- `python-pptx`
  - 解析 `.pptx`
- `openpyxl`
  - 解析 `.xlsx` / `.xlsm`
- `pandas`
  - Excel 兜底解析路径

已有一个现成文件可参考：

- [requirements-google.txt](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/requirements-google.txt:1)

但注意它**只覆盖了 Office 解析包**，没有覆盖 Google API / ADK / rerank / Feishu 依赖。

### 9.4 Feishu / Bailian / Rerank 依赖

- `alibabacloud-bailian20231229`
- `alibabacloud-tea-openapi`

用途：

- Bailian 检索 SDK

### 9.5 Eval / 脚本辅助依赖

- `PyYAML`

用途：

- eval runner / bench 配置读取

### 9.6 一个更稳的安装建议

如果母项目要稳定接入，建议安装这一组：

```bash
pip install \
  google-adk \
  google-genai \
  python-dotenv \
  requests \
  google-api-python-client \
  google-auth \
  google-auth-oauthlib \
  google-auth-httplib2 \
  httplib2 \
  python-docx \
  python-pptx \
  openpyxl \
  pandas \
  PyYAML \
  alibabacloud-bailian20231229 \
  alibabacloud-tea-openapi
```

---

## 10. 母项目如果不用某个 source

规则很简单：

- `sources` 里不包含某个 source
- 就不需要传这个 source 的认证信息

例如：

- 只跑 `["google", "feishu"]`
- 那就不需要传任何 bailian 凭证

---

## 11. 结论

后续母项目接入时，建议：

1. 只对接 [retrieve_agent_client.py](/Users/javendai/Documents/workplace/Multi-source_RAG_Agent/retrieve_agent_client.py:1)
2. 所有 key / token / path 都通过 `config(...)` 显式传入
3. 单次查询通过 `request(user_query=...)` 传入
4. 结果统一读取：
   - `result.chunks`
   - `result.metadata`
   - `result.raw_payload`
   - `result.progress_events`

这条链路已经真实跑通过，可以成功接收结构化 `chunks`。
