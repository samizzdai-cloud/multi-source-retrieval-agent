# Sheet Reader 使用指南

## 概述

`sheet_reader.py` 是 Google Sheets 专用读取器，提供从**结构探查**到**内容读取**的完整能力，支持渐进式阅读策略。

---

## 核心功能

| 功能 | 函数 | 典型耗时 |
|------|------|---------|
| 获取 Spreadsheet 结构 | `get_spreadsheet_info()` | ~0.5-1s |
| 读取单个 Range | `read_one(range_name=...)` | ~1s |
| 批量读取多个 Ranges | `read_one(range_names=[...])` | ~1-1.5s |
| 读取全部 Sheets | `read_one()` | ~0.6s × sheet_count |
| 串行批量读取 | `read_batch()` | 累加 |
| 并发批量读取 | `read_concurrent()` | 加速比 ~4x |

---

## 渐进式阅读流程

### Phase 1: 探查结构（~1s）

先获取文件有哪些 sheet，决定后续读取策略。

```python
from readers import sheet_reader

# 获取完整结构（含行列数）
info = sheet_reader.get_spreadsheet_info(file_id, creds)
print(f"标题: {info['title']}")
print(f"Sheet 数量: {info['sheet_count']}")
for s in info['sheets']:
    gp = s['gridProperties']
    print(f"  - {s['title']}: {gp.get('rowCount')}×{gp.get('columnCount')}")

# 只获取标题和 sheet 名称（更快）
info = sheet_reader.get_spreadsheet_info(
    file_id, creds,
    fields="properties.title,sheets.properties.title"
)
```

**字段定制** (`fields` 参数):

| 场景 | fields 值 | 获取内容 |
|------|----------|---------|
| 最小 | `properties.title,sheets.properties.title` | 标题 + sheet 名 |
| 标准 | `properties(title,locale,timeZone),sheets.properties(title,gridProperties)` | + 行列数 |
| 完整 | (不传，默认) | 所有元数据 |

---

### Phase 2: 选择性读取（~1-2s）

基于结构信息，只读取需要的 sheet。

```python
# 只读前 3 个 sheet（通过 batchGet，效率极高）
sheet_names = [s['title'] for s in info['sheets'][:3]]
ranges = [f"'{name}'" for name in sheet_names]

result = sheet_reader.read_one(
    file_id=file_id,
    creds=creds,
    range_names=ranges,
)

# 查看结果
for name, data in result['sheets'].items():
    print(f"{name}: {data['row_count']} 行")
```

**batchGet 效率**: 3 个 sheet 批量读取 ≈ 1 个 sheet 的时间（~1.1s）

---

### Phase 3: 全部读取（~3-10s）

当需要完整数据时，读取所有 sheets。

```python
result = sheet_reader.read_one(file_id=file_id, creds=creds)

# 结果包含所有 sheet 的数据
for name, data in result['sheets'].items():
    print(f"{name}: {data['row_count']} 行, {data['col_count']} 列")
```

**时间预估**: `0.6s × sheet_count`（实测：7sheets→5s, 10sheets→6.5s, 15sheets→9s）

---

## 输出格式

```python
result = sheet_reader.read_one(
    file_id=file_id,
    creds=creds,
    output_format="md",  # md | csv | json | plain | raw
)
```

| 格式 | 说明 | 适用场景 |
|------|------|---------|
| `md` (默认) | Markdown 表格 | LLM 输入、文档展示 |
| `csv` | CSV 文本 | 数据分析、导入 Excel |
| `json` | JSON 结构化 | 程序处理、API 传输 |
| `plain` | 制表符分隔纯文本 | 快速查看 |
| `raw` | Python dict | 自定义处理 |

**统一返回结构**:

```python
{
    "file_id": "...",
    "mime_type": "application/vnd.google-apps.spreadsheet",
    "title": "文件标题",
    "content": "根据 output_format 格式化的内容",
    "structured_md": "Markdown 格式（始终存在）",
    "content_type": "md",
    "error": None,           # 错误信息
    "mode": "full",          # full | timeout | skipped
    "drive_size": 123456,    # Drive 文件大小（字节）
    "skip_reason": None,
    "sheets": {              # 各 sheet 的原始数据
        "Sheet1": {
            "range": "'Sheet1'!A1:Z100",
            "values": [[...], ...],
            "row_count": 100,
            "col_count": 26,
        }
    },
    "metadata": {...},       # Spreadsheet 元数据
}
```

---

## 批量读取

### 串行批量（控制速率）

```python
file_ids = ["id1", "id2", "id3"]
results = sheet_reader.read_batch(file_ids=file_ids, creds=creds)
```

### 并发批量（加速）

```python
file_ids = ["id1", "id2", "id3", "id4", "id5"]
results = sheet_reader.read_concurrent(
    file_ids=file_ids,
    creds=creds,
    max_workers=5,  # 并发数
)
```

**加速比**: 5 个文件并发 ≈ 单文件的 1.5-2x（受 API 配额限制）

---

## 与 drive_content_reader 配合使用

`drive_content_reader.py` 提供统一入口，包含**预估算**和**超时兜底**。

```python
from drive_content_reader import read_drive_content

# 自动处理：预估算 → 超时兜底 → 读取
result = read_drive_content(
    mime_type="application/vnd.google-apps.spreadsheet",
    file_id=file_id,
    creds=creds,
    max_read_time=7,  # 7 秒超时
)

if result['mode'] == 'timeout':
    print("文件过大，读取超时")
elif result['mode'] == 'skipped':
    print(f"跳过原因: {result['skip_reason']}")
elif result['mode'] == 'full':
    print(result['structured_md'])
```

**预估算规则**: sheet_count > 20 时自动跳过（预估读取时间 > 10.5s）

---

## 完整示例

```python
from auth_utils import get_credentials
from readers import sheet_reader
from drive_content_reader import read_drive_content

# 1. 认证
creds = get_credentials(scopes=[
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
])

file_id = "YOUR_SPREADSHEET_ID"

# 2. 探查结构
info = sheet_reader.get_spreadsheet_info(file_id, creds)
print(f"找到 {info['sheet_count']} 个 sheets")

# 3. 判断读取策略
if info['sheet_count'] > 20:
    # 大文件：只读前 3 个 sheet
    sheet_names = [s['title'] for s in info['sheets'][:3]]
    ranges = [f"'{name}'" for name in sheet_names]
    result = sheet_reader.read_one(file_id, creds, range_names=ranges)
else:
    # 小文件：全部读取
    result = sheet_reader.read_one(file_id, creds)

# 4. 使用结果
if result['error']:
    print(f"错误: {result['error']}")
else:
    print(result['structured_md'])
```

---

## 性能参考

| 操作 | 7 sheets | 10 sheets | 15 sheets |
|------|---------|----------|----------|
| 获取结构 | 0.5s | 0.5s | 0.5s |
| 读前 3 sheets (batchGet) | 1.1s | 1.1s | 1.1s |
| 全部读取 | 5.2s | 6.6s | 9.1s |

---

## 注意事项

1. **Sheet 名称含空格**: 自动处理，无需手动加引号
2. **空 Sheet**: 返回 `*(空表 — 无数据)*`
3. **读取失败**: 单个 sheet 失败不影响其他 sheet（错误记录在 `sheets[sheet_name]["error"]`）
4. **API 配额**: 默认每秒 100 次请求，大批量读取建议降低 `max_workers`
