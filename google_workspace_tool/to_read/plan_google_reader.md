# Plan: Google Docs 结构化读取与 Markdown 转换模块

> 目标：解耦 `drive_content_reader.py` 的 Docs 读取能力，构建一个批量请求、结构感知、可过滤删除线内容的 Markdown 输出管道。

---

## 1. 核心结论：API 方法选择

### 1.1 `documents.get` JSON 是唯一可行方案

| 能力 | `documents.get` JSON | `files.export` text/markdown |
|------|---------------------|------------------------------|
| **多 Tabs 分离** | 完整获取所有 tabs（含 childTabs） | 只导出单一 tab，无分隔 |
| **删除线过滤** | 精确检测 `strikethrough` + `suggestedDeletionIds` | 无法区分，只能导出为 `~~text~~` |
| **建议修改控制** | `suggestionsViewMode` 直接忽略/接受 | 完全无感知 |
| **内联对象/公式/智能元素** | 可识别并占位 | 丢失或变为不可读文本 |
| **表格结构** | 完整行列 + 嵌套表格 | 简单表格，嵌套丢失 |
| **列表精确层级** | 查 `lists` 字典得精确格式 | 基本正确但无元信息 |

`files.export` 在 tabs、建议修改、删除线过滤、结构边界等方面存在不可弥补的缺陷。必须用 `documents.get` + JSON 解析，自行转译为 Markdown。

### 1.2 关键请求参数

```python
docs_service.documents().get(
    documentId=doc_id,
    includeTabsContent=True,                           # 必须：获取所有 tabs
    suggestionsViewMode='PREVIEW_WITHOUT_SUGGESTIONS', # 建议：直接忽略所有建议修改
    fields='documentId,title,tabs(tabProperties,documentTab(body(content)))'
).execute()
```

---

## 2. 性能基准

基于实际测试（3 个文档）：

| 文档 | MD 输出 | JSON 大小 | API Fetch | Parse | Render | 瓶颈 |
|------|---------|-----------|-----------|-------|--------|------|
| Small | 8 KB | 81 KB | 1.3s | 0.001s | 0.000s | **API** |
| Medium | 11 KB | 123 KB | 1.5s | 0.001s | 0.000s | **API** |
| Large | 372 KB | 6.4 MB | **28.0s** | 0.030s | 0.006s | **API** |

**关键发现**：
- API Fetch 是**绝对瓶颈**（占 99.9% 时间）
- Parse + Render 是本地处理，几乎瞬间完成
- `fields` 参数对 fetch 时间**无显著优化**（服务器端处理时间由文档复杂度决定，不因字段过滤而减少）
- 批量并发 10 workers 对 3 个文档（1 大 2 小）的提升有限（1.07x），因为大文档拖慢了整个批次

---

## 3. 模块架构（已实现）

```
google_doc_reader/
├── __init__.py
├── fetcher.py           # 批量请求层：10 并发、fields 优化、指数退避重试
├── parser.py            # 解析层：JSON → IR，支持 max_tabs 过滤
├── markdown_renderer.py # 渲染层：IR → Markdown
└── models.py            # 数据模型
```

### 3.1 fetcher.py

- 默认并发度：**10**
- `fields` 参数精简传输（虽然对时间帮助不大，但减少带宽）
- 429 指数退避重试（1s → 2s → 4s → 8s）
- 支持 `max_tabs` 过滤（解析层实现）

### 3.2 parser.py

- 递归扁平化 tabs（含 childTabs）
- 过滤 strikethrough & suggestedDeletionIds
- **空段落/空 heading 过滤**（减少垃圾空行）
- `max_tabs` 参数：只保留最后 N 个 tabs（按 index 排序）

### 3.3 markdown_renderer.py

- Tabs 分隔：`## Tab: {title}` + `---`
- 文档标题（doc.title）**不单独渲染为 #**，避免与 tab 内 TITLE 冲突
- 嵌套表格扁平化为缩进文本块
- 目录转链接列表
- 公式：`[equation] {text}`
- 图片：完全跳过，不占位

---

## 4. 渐进式阅读策略（新增需求）

### 4.1 背景

文档 2（ZTE Weekly Meeting）是一个**历史堆叠型长文档**：
- 单 tab，但包含 **40+ 周**的会议记录
- 372 KB Markdown 输出，6.4 MB JSON
- API Fetch 需要 **28 秒**

用户不可能每次等待 28 秒加载完整文档。需要**分层加载**能力。

### 4.2 需求设计

#### 阶段 1：大纲预览（Outline Mode）

**目标**：用户想快速了解文档结构，不加载全部内容。

**输出**：只提取 heading 层级，生成导航大纲。

```markdown
## Tab: ZTE Weekly Meeting | Thursday 16:30

- H1: Please check the outline on the left side to direct the latest content.
- H1: ZTE Weekly Meeting | X 16:30-17:00
  - H2: Agenda
  - H2: PSI Status - Bryce
  - H2: Amazon/Aliexpress Sales - Shelia/Mario
- H1: ZTE Weekly Meeting | 25 May 11:00-12:00
  - H2: Agenda
  - H2: Project Update - Kevin/Vivian
...
```

**实现**：在 IR 层面过滤，只保留 `type="heading"` 的 Block。无需重新调用 API（本地过滤已 fetch 的 JSON）。

#### 阶段 2：Tab 预选

**目标**：多 tab 文档中，用户只关心最近/特定的 tab。

**已有实现**：`max_tabs=N` 参数，只解析最后 N 个 tabs。

**扩展**：支持按 tab_title 正则匹配筛选。

#### 阶段 3：Heading Section 预选

**目标**：用户看完大纲后，只想读取某个 heading 下的内容。

**场景**：
- 文档 2 中，用户只想读 "ZTE Weekly Meeting | 28 July 16:30-17:00" 这一周的内容
- 文档 1 中，用户只想读 "B、Key highlights of the new gaming tablet" 这一节

**输出**：只渲染指定 heading 及其子内容，直到下一个同层级/更高层级 heading。

```python
# 示例：只读取 "ZTE Weekly Meeting | 28 July" 这一节
render_section(doc_content, tab_index=0, heading_text="ZTE Weekly Meeting | 28 July")
```

**实现思路**：
1. 在 Block 列表中定位目标 heading
2. 收集该 heading 到下一个同层级/更高层级 heading 之间的所有 blocks
3. 渲染为 Markdown

#### 阶段 4：组合策略

```python
# 完整调用链示例
doc_json = fetcher.fetch_one(doc_id)          # 28s（不可避免）

# 策略 A：只看大纲（本地处理，瞬间）
outline = parser.parse_outline(doc_json)

# 策略 B：只看最近 2 个 tabs
doc_content = parser.parse(doc_json, max_tabs=2)

# 策略 C：只看特定 section
section_md = renderer.render_section(doc_content, heading_text="...")

# 策略 D：完整内容（默认）
full_md = renderer.render(doc_content)
```

### 4.3 缓存策略（未来）

由于 API Fetch 是瓶颈，建议：
- 将 `doc_json` 本地缓存（key: doc_id + revisionId）
- 后续解析不同层级（outline / section / full）都从缓存 JSON 本地处理
- 无需重复调用 28s 的 API

---

## 5. 完整文档元素处理清单

### StructuralElement

| 元素 | JSON Key | 处理 |
|------|----------|------|
| 段落 | `paragraph` | 核心解析 elements + style + bullet |
| 分节符 | `sectionBreak` | 忽略或标记分页 |
| 表格 | `table` | 解析 rows → cells → content，处理嵌套 |
| 目录 | `tableOfContents` | 递归解析 content 为链接列表 |

### ParagraphElement

| 元素 | JSON Key | 处理 |
|------|----------|------|
| 文本运行 | `textRun` | 提取 content + textStyle，过滤 strikethrough |
| 自动文本 | `autoText` | 提取 content |
| 分页符 | `pageBreak` | 忽略 |
| 分栏符 | `columnBreak` | 忽略 |
| 脚注引用 | `footnoteReference` | 输出 `[^n]` |
| 水平线 | `horizontalRule` | 输出 `---` |
| 公式 | `equation` | 输出 `[equation] {text}` |
| 公式符号 | `equationSymbol` | 输出 `[equation] {text}` |
| 内联对象 | `inlineObjectElement` | **完全跳过**（图片等）|
| 人员 | `person` | 提取名称+邮箱 |
| 富链接 | `richLink` | 提取标题+链接 |
| 日期 | `dateElement` | 提取日期文本 |

### TextStyle 过滤

- `strikethrough: true` → **过滤整个 textRun**
- `suggestedDeletionIds` → **过滤整个元素**
- `bold` / `italic` / `underline` → 映射为 Markdown 格式
- `link.url` → 映射为 Markdown `[text](url)`
- **underline 不单独标记**（用户要求）
- **heading bold 不额外加 `**`**（用户要求）

---

## 6. 待实现项

### 6.1 渐进式阅读（Phase 2）

- [ ] `parser.parse_outline(doc_json)` → 只提取 heading 结构
- [ ] `renderer.render_section(doc_content, heading_text)` → 只渲染指定 section
- [ ] `renderer.render_section(doc_content, heading_level, heading_text)` → 更精确的 section 定位
- [ ] `parser.parse(doc_json, tab_filter=regex)` → 按 tab 标题正则筛选

### 6.2 缓存层（Phase 3）

- [ ] JSON 本地缓存（key: doc_id + revisionId）
- [ ] 缓存命中时跳过 API Fetch

### 6.3 批量优化（Phase 3）

- [ ] 大文档检测（JSON 预估 > 1MB）时自动降级并发度
- [ ] 批量请求中大小文档分离，小文档优先返回

---

---

## 7. Google Sheets Reader（已实现）

### 7.1 模块位置

```
readers/sheet_reader.py
```

### 7.2 核心 API 与元数据来源

| 信息类别 | API | 说明 |
|----------|-----|------|
| **Spreadsheet 结构** | Sheets API `spreadsheets().get()` | title, locale, timeZone, defaultFormat, spreadsheetTheme |
| **Sheet 单表信息** | Sheets API `spreadsheets().get()` | sheetId, title, **index**, sheetType, **gridProperties.rowCount/columnCount**, frozenRowCount, frozenColumnCount, tabColor, hidden |
| **文件大小/创建时间/所有者/路径** | Drive API `files().get()` | size, createdTime, modifiedTime, owners, parents, folder_path, permissions |
| **单元格值** | Sheets API `spreadsheets().values().get()` / `.batchGet()` | 原始格式为嵌套列表 `[["A","B"],["C","D"]]` |

#### 权限要求事实核查

项目中 `drive_content_reader.py` 通过 Drive API 获取文件元数据（size、folder_path 等），需要正确的 OAuth scope。但 **doc_reader / sheet_reader 本身不走 Drive API**，这是它们能读取共享文件的关键。

**核心发现：为什么 doc_reader 能读共享文件？**

`doc_reader.read_one()` 的工作流程：
1. 直接用 creds 构建 `docs(v1)` service
2. 调用 `docs_service.documents().get(documentId=doc_id)`
3. **完全不调用 Drive API**

Docs API / Sheets API 的权限模型与 Drive API 不同：
- **Docs/Sheets API**：只要用户对**该文档**有查看权限，无论文件是否在用户的 Drive 中，都能读取
- **Drive API**：`drive.readonly` 只能访问用户 Drive 中的文件，某些共享链接文件会 404

**实测验证**：
- `Logistic Issue`（共享 Spreadsheet）→ `drive.readonly` + Drive API `files().get()` = **404**
- `Logistic Issue`（共享 Spreadsheet）→ `documents.readonly` + Sheets API `spreadsheets().get()` = **200 OK**

**⚠️ 重要：doc_reader 的 `supports_all_drives` 参数从未被使用**

```python
# doc_reader.py 中虽然接收了 supports_all_drives，但从未使用
def read_one(file_id, creds, ..., supports_all_drives=True):
    docs_service = build("docs", "v1", credentials=creds)
    # supports_all_drives 没有被传递给任何 API 调用
    # 实际上 Docs API 的 documents().get() 也不支持 supportsAllDrives 参数
```

Docs API `documents().get()` 和 Sheets API `spreadsheets().get()` 均**不支持** `supportsAllDrives` 参数（传入会报 TypeError）。

**Scope 对比表**：

| Scope | 能力 | 对共享文件 (Drive API) | 对共享文件 (Docs/Sheets API) | 对原生文件 size |
|-------|------|----------------------|---------------------------|----------------|
| `drive.readonly` | 读取用户 Drive 文件 | 部分共享文件 **404** | N/A（不走 Drive API） | **返回 size / quotaBytesUsed** |
| `drive` | 完整读写所有 Drive 文件 | **可访问** | N/A | **返回 size / quotaBytesUsed** |
| `documents.readonly` | 读取 Docs 内容 | N/A | **可访问共享 Doc** | N/A |

**关键结论**：
- **Sheet 索引、最大行列数** → 只能由 **Sheets API** 提供
- **文件创建/修改时间、所有者、文件夹路径** → 由 **Drive API** 提供，需要 `drive` 或 `drive.readonly` scope
- **共享文件读取** → Docs/Sheets API 天然支持，无需特殊设置；Drive API 需要 `drive` 完整权限
- **`drive_content_reader.py` 的 size gate 对共享原生文件会失效** → 因为 Drive API 404 导致无法获取 size，但 doc_reader/sheet_reader 仍可正常读取内容
- **原生文件 size** → Drive API `files.get` / `files.list` **返回 `size` 与 `quotaBytesUsed`**（两者值相同，单位为字节）。size gate 对原生文件**可以触发**，已在 `google_source_agent.py` 的 doc filter 中用于跳过 >45MB 超大原生 Doc。
- **非原生文件 size** → 上传的 PDF/Word/Excel 等，`drive.readonly` / `drive` 权限均可正常获取 size，size gate 有效

### 7.3 原始数据格式

Sheets API `values().get()` / `batchGet()` 返回的原始数据结构是 **嵌套列表（list of lists）**，不是 JSON 对象：

```python
# API 原始返回
{
    "range": "Sheet1!A1:C2",
    "majorDimension": "ROWS",
    "values": [
        ["Header1", "Header2", "Header3"],   # ← 第1行
        ["value1",  "value2",  "value3"]     # ← 第2行
    ]
}
```

与 JSON 的关系：
- 原始数据结构是 Python `list[list[Any]]`，可以**轻松序列化为 JSON**
- `output_format="raw"` 直接返回 dict，content_type 为 `"dict"`
- `output_format="json"` 返回 JSON 字符串
- **API 调用时间占 99.9%**，数据格式转换（list → md/csv/plain/json）在本地瞬间完成

### 7.4 为什么 md 看起来"更慢"？

实测数据（同一 Range，多次测试）：

| 格式 | 耗时 | 内容长度 |
|------|------|----------|
| raw | ~0.97s | 3,299 |
| json | ~0.98s | 5,629 |
| csv | ~0.99s | 2,531 |
| plain | ~0.98s | 2,527 |
| md | ~1.71s | 3,092 |

**原因分析**：
1. **不是 Markdown 转换慢**：`_values_to_markdown()` 处理 100 行 x 10 列数据实测仅 **0.07ms**
2. **是网络/API 调用波动**：单次 API 调用本身在 0.9-1.7s 之间正常波动
3. **MD 和 RAW 的本地处理差异 < 1ms**，可以忽略

**结论**：md 输出不会比 raw/json 慢，差异完全来自网络波动。

### 7.5 功能接口

| 函数 | 功能 |
|------|------|
| `read_one()` | 单文件读取。支持：全部 Sheets / 单个 Range / 批量 Ranges |
| `read_batch()` | **串行**批量读取多个 Spreadsheet |
| `read_concurrent()` | **并发**批量读取（ThreadPoolExecutor） |
| `read_ranges()` | 便捷函数：对单个 Spreadsheet 批量读多个 Ranges |
| `get_spreadsheet_info()` | 仅获取元数据 |

### 7.6 输出格式

| 格式 | 说明 |
|------|------|
| `md` (默认) | Markdown 表格，适合 LLM 阅读 |
| `csv` | CSV 文本（多 Sheet 用注释行分隔） |
| `plain` | 制表符分隔纯文本 |
| `json` | JSON 字符串 |
| `raw` | 原始 Python dict |

### 7.7 性能实测

**测试文件**: `Logistic Issue`（13 Sheets，~1,000 行数据）

| 测试项 | 耗时 | 备注 |
|--------|------|------|
| 获取元数据 | ~1.0s | 13 Sheets 结构 |
| 读取单个 Range | ~1.7s | |
| **批量读取 3 个 Ranges** | **~1.4s** | **batchGet 一次 API 调用** |
| 读取整个 Spreadsheet | ~5.8s | 13 Sheets，210KB MD |
| 串行读取 10 个文件 | ~9.4s | |
| **并发读取 10 个文件 (5 workers)** | **~2.2s** | **加速比 4.4x** |

**并发度对比**（10 个文件）：

| max_workers | 耗时 |
|-------------|------|
| 1 | 9.6s |
| 3 | 3.5s |
| 5 | 2.3s |
| 10 | 1.3s |

### 7.8 使用示例

```python
from readers import sheet_reader
from auth_utils import get_credentials

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
creds = get_credentials(SCOPES)

# 读取整个 Spreadsheet
result = sheet_reader.read_one(file_id="1IO6orj...", creds=creds)

# 读取指定 Range
result = sheet_reader.read_one(file_id="...", creds=creds,
                               range_name="Sheet1!A1:D10")

# 批量读取多个 Ranges（一次 API 调用）
result = sheet_reader.read_one(file_id="...", creds=creds,
                               range_names=["Sheet1!A1:C3", "Sheet2!A1:C3"])

# 并发批量读取
results = sheet_reader.read_concurrent(
    file_ids=[id1, id2, id3],
    creds=creds,
    range_name="A1:C10",
    max_workers=5,
)

# 获取元数据
info = sheet_reader.get_spreadsheet_info(file_id="...", creds=creds)
# info → {title, locale, timeZone, sheet_count, sheets: [{sheetId, title, index, gridProperties}]}
```

---

## 8. 测试文档

1. Small: `19mNq3VjEJTqCszBXRdZtTxTDeqopSEsk-X08ZMj5mc0` (REDMAGIC Training)
2. Medium: `1O-ID77Zzv5zS0T7Ag8EVOR_Z_tcSZJjitKD8CnavOZA` (MWC 2020 QA)
3. Large: `1XQ_NSFisu5BQVcVgzD03nsPUHI2XTCYmRaKdBbCgW3w` (ZTE Weekly Meeting)
4. Sheet Test: `1IO6orjP3rnxvgaQq70y6Zu5ZfW_albSLg0bbrl6aZuQ` (Logistic Issue, 13 Sheets)
