---
chunk_metadata:
  chunk_id: "19dce39b8ad52b3e"
  title: "It‘s thread test"
  message_count: 3
---

# It‘s thread test

### [第 1 轮] From: Javen Dai <javen.dai@fstln.io> | Date: Mon, 27 Apr 2026 17:16:51 +0800
**To**: Javen Dai <javen.dai@fstln.io>

you are A

---

### [第 2 轮] From: Javen Dai <javen.dai@fstln.io> | Date: Mon, 27 Apr 2026 17:17:15 +0800
**To**: Javen Dai <javen.dai@fstln.io>

It is B

---

### [第 3 轮] From: Javen Dai <javen.dai@fstln.io> | Date: Mon, 27 Apr 2026 17:21:17 +0800
**To**: Javen Dai <javen.dai@fstln.io>

hello

**(?? 附件内容)**
```
--- 附件: Google_Workspace_API_Reference_Summary.md ---
# Google Workspace API 参考代码总结

> 本文档基于 Google_workspace_reference 目录中的官方示例代码整理，涵盖 Drive、Docs、Sheets、Gmail、Slides 五大 Google Workspace API 的 Python 参考实现。
>
> 生成日期：2026-04-23

---

## 目录

1. [通用说明](#通用说明)
2. [Google Drive API](#google-drive-api)
3. [Google Docs API](#google-docs-api)
4. [Google Sheets API](#google-sheets-api)
5. [Google Gmail API](#google-gmail-api)
6. [Google Slides API](#google-slides-api)

---

## 通用说明

### 认证方式

所有示例代码主要使用两种认证方式：

#### 1. OAuth2 桌面应用流（Quickstart 示例）

适用于需要用户交互授权的场景：

```python
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
import os.path

SCOPES = ["https://www.googleapis.com/auth/..."]

creds = None
if os.path.exists("token.json"):
    creds = Credentials.from_authorized_user_file("token.json", SCOPES)
if not creds or not creds.valid:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
        creds = flow.run_local_server(port=0)
    with open("token.json", "w") as token:
        token.write(creds.to_json())
```

**依赖文件：**
- `credentials.json` - 从 Google Cloud Console 下载的 OAuth2 客户端凭据
- `token.json` - 自动生成的用户访问令牌（首次运行后生成）

#### 2. Application Default Credentials（ADC，Snippets 示例）

适用于服务器环境或预授权场景：

```python
import google.auth
from googleapiclient.discovery import build

creds, _ = google.auth.default()
service = build("api_name", "version", credentials=creds)
```

**配置方式：**
- 设置环境变量 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号 JSON 文件
- 或在 Google Cloud 环境中自动获取

### 通用依赖

```
google-auth-oauthlib>=0.4.1
google-auth-httplib2>=0.0.3
google-api-python-client>=1.7.8
```

### 错误处理

所有示例统一使用 `googleapiclient.errors.HttpError` 进行异常捕获：

```python
from googleapiclient.errors import HttpError

try:
    result = service.xxx().execute()
except HttpError as error:
    print(f"An error occurred: {error}")
```


---

## Google Drive API

### API 版本
- **Drive API v3**（推荐，当前主要版本）
- **Drive API v2**（遗留版本，示例中仍有对照实现）
- **Drive Activity API v2**（活动记录查询）

### 参考文件位置

```
Google_workspace_reference/drive/
├── quickstart/quickstart.py              # 基础入门：列出文件
├── activity-v2/quickstart.py             # Drive Activity API 入门
├── driveapp/main.py                      # 遗留 v2 上传示例
└── snippets/
    ├── drive-v3/                         # v3 API 示例（推荐）
    │   ├── file_snippet/                 # 文件操作
    │   ├── app_data_snippet/             # 应用数据文件夹
    │   ├── change_snippet/               # 变更追踪
    │   ├── drive_snippet/                # 共享驱动器
    │   └── team_drive_snippets/          # 团队驱动器（遗留）
    └── drive-v2/                         # v2 API 对照示例
```

### 功能分类与参考文件

#### 1. 基础入门

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 列出文件 | `drive/quickstart/quickstart.py` | `service.files().list()` | 列出前10个可访问文件 |
| 查询活动记录 | `drive/activity-v2/quickstart.py` | `service.activity().query()` | 查询最近10条 Drive 活动 |

**Quickstart 关键代码：**
```python
service = build("drive", "v3", credentials=creds)
results = service.files().list(
    pageSize=10, 
    fields="nextPageToken, files(id, name)"
).execute()
```

**Drive Activity 关键代码：**
```python
service = build("driveactivity", "v2", credentials=creds)
results = service.activity().query(body={"pageSize": 10}).execute()
```

#### 2. 文件操作

| 功能 | 参考文件 (v3) | 核心 API | 关键参数 |
|------|--------------|----------|----------|
| 创建文件夹 | `file_snippet/create_folder.py` | `files().create()` | `mimeType: application/vnd.google-apps.folder` |
| 上传文件 | `file_snippet/upload_basic.py` | `files().create()` | `MediaFileUpload(filename, mimetype)` |
| 上传到指定文件夹 | `file_snippet/upload_to_folder.py` | `files().create()` | `parents: [folder_id]` |
| 上传并转换格式 | `file_snippet/upload_with_conversion.py` | `files().create()` | `mimeType: application/vnd.google-apps.spreadsheet` |
| 更新文件内容 | `file_snippet/upload_revision.py` | `files().update()` | `media_body=MediaFileUpload(...)` |
| 下载文件 | `file_snippet/download_file.py` | `files().get_media()` | 使用 `MediaIoBaseDownload` 分块下载 |
| 导出为 PDF | `file_snippet/export_pdf.py` | `files().export_media()` | `mimeType="application/pdf"` |
| 移动文件 | `file_snippet/move_file_to_folder.py` | `files().update()` | `addParents` / `removeParents` |
| 搜索文件 | `file_snippet/search_file.py` | `files().list(q=...)` | 支持 `mimeType='image/jpeg'` 等查询条件 |
| 修改时间戳 | `file_snippet/touch_file.py` | `files().update()` | `modifiedTime: ISO格式时间` |
| 创建快捷方式 | `file_snippet/create_shortcut.py` | `files().create()` | `mimeType: application/vnd.google-apps.drive-sdk` |

**上传文件示例：**
```python
from googleapiclient.http import MediaFileUpload

file_metadata = {"name": "download.jpeg"}
media = MediaFileUpload("download.jpeg", mimetype="image/jpeg")
file = service.files().create(
    body=file_metadata, 
    media_body=media, 
    fields="id"
).execute()
```

**下载文件示例：**
```python
from googleapiclient.http import MediaIoBaseDownload
import io

request = service.files().get_media(fileId=file_id)
file = io.BytesIO()
downloader = MediaIoBaseDownload(file, request)
done = False
while done is False:
    status, done = downloader.next_chunk()
    print(f"Download {int(status.progress() * 100)}%.")
```

**移动文件示例：**
```python
# 先获取现有父文件夹
file = service.files().get(fileId=file_id, fields="parents").execute()
previous_parents = ",".join(file.get("parents"))

# 移动到新文件夹
file = service.files().update(
    fileId=file_id,
    addParents=folder_id,
    removeParents=previous_parents,
    fields="id, parents"
).execute()
```

**搜索文件示例：**
```python
response = service.files().list(
    q="mimeType='image/jpeg'",
    spaces="drive",
    fields="nextPageToken, files(id, name)",
    pageToken=page_token
).execute()
```

#### 3. 分享与权限

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 批量分享 | file_snippet/share_file.py | permissions().create() | 使用 new_batch_http_request() 批量添加权限 |

**批量分享示例：**
```python
def callback(request_id, response, exception):
    if exception:
        print(exception)
    else:
        print(f"Permission Id: {response.get('id')}")

batch = service.new_batch_http_request(callback=callback)

user_permission = {
    "type": "user",
    "role": "writer",
    "emailAddress": "user@example.com"
}
batch.add(service.permissions().create(
    fileId=file_id, body=user_permission, fields="id"
))

domain_permission = {
    "type": "domain",
    "role": "reader",
    "domain": "example.com"
}
batch.add(service.permissions().create(
    fileId=file_id, body=domain_permission, fields="id"
))

batch.execute()
```

#### 4. 应用数据文件夹（App Data Folder）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 获取应用数据文件夹 | app_data_snippet/fetch_appdata_folder.py | files().get(fileId="appDataFolder") | 获取应用数据文件夹 ID |
| 列出应用数据文件 | app_data_snippet/list_appdata.py | files().list(spaces="appDataFolder") | 列出应用数据文件夹中的文件 |
| 上传应用到数据文件夹 | app_data_snippet/upload_appdata.py | files().create() | parents: ["appDataFolder"] |

**应用数据文件夹特点：**
- 对用户不可见，仅应用可访问
- 适合存储应用配置、缓存等数据
- 需要 https://www.googleapis.com/auth/drive.appdata 权限

#### 5. 变更追踪（Changes）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 获取起始页令牌 | change_snippet/fetch_start_page_token.py | changes().getStartPageToken() | 获取当前状态的 page token |
| 获取变更列表 | change_snippet/fetch_changes.py | changes().list() | 轮询获取文件变更 |

**变更追踪示例：**
```python
page_token = saved_start_page_token
while page_token is not None:
    response = service.changes().list(
        pageToken=page_token, spaces="drive"
    ).execute()
    for change in response.get("changes"):
        print(f"Change found for file: {change.get('fileId')}")
    if "newStartPageToken" in response:
        saved_start_page_token = response.get("newStartPageToken")
    page_token = response.get("nextPageToken")
```

**使用场景：** 同步本地文件状态、监听 Drive 文件变化、增量备份

#### 6. 共享驱动器（Shared Drives / Team Drives）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建共享驱动器 | drive_snippet/create_drive.py | drives().create() | 需要 requestId（UUID）保证幂等性 |
| 恢复无组织者的驱动器 | drive_snippet/recover_drives.py | drives().list() + permissions().create() | 查找 organizerCount=0 的驱动器并添加组织者 |
| 创建团队驱动器（遗留） | team_drive_snippets/create_team_drive.py | teamdrives().create() | 遗留 API |
| 恢复团队驱动器（遗留） | team_drive_snippets/recover_team_drives.py | teamdrives().list() + permissions().create() | 遗留 API |

**创建共享驱动器示例：**
```python
import uuid

drive_metadata = {"name": "Project Resources"}
request_id = str(uuid.uuid4())
drive = service.drives().create(
    body=drive_metadata, 
    requestId=request_id, 
    fields="id"
).execute()
```

**注意事项：**
- useDomainAdminAccess=True 需要管理员权限
- supportsAllDrives=True 用于处理共享驱动器中的文件

### Drive API 使用注意事项

1. **v2 vs v3 差异：**
   - v3 简化了响应字段，默认只返回少量字段，需通过 fields 参数指定
   - v3 使用 files().create() 替代 v2 的 files().insert()
   - v3 使用 permissions().create() 替代 v2 的 permissions().insert()

2. **文件上传模式：**
   - 简单上传：直接 files().create(body=..., media_body=...)
   - 可恢复上传：设置 resumable=True（大文件推荐）

3. **常用 MIME 类型：**
   - 文件夹：application/vnd.google-apps.folder
   - Google Docs：application/vnd.google-apps.document
   - Google Sheets：application/vnd.google-apps.spreadsheet
   - Google Slides：application/vnd.google-apps.presentation

4. **分页处理：** 所有 list() 操作都支持分页，需处理 nextPageToken

---

## Google Docs API

### API 版本
- **Docs API v1**

### 参考文件位置

```
Google_workspace_reference/docs/
├── quickstart/
│   ├── quickstart.py                     # 基础入门：读取文档标题
│   ├── requirements.txt
│   └── README.md
├── output-json/
│   ├── output_json.py                    # 现代版：输出文档 JSON
│   └── output-json.py                    # 遗留版：使用 oauth2client
└── mail-merge/
    ├── docs_mail_merge.py                # 邮件合并示例
    ├── requirements.txt
    └── README.md
```

### 功能分类与参考文件

#### 1. 基础入门

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 读取文档 | quickstart/quickstart.py | documents().get(documentId=...) | 获取文档完整内容 |

**Quickstart 关键代码：**
```python
SCOPES = ["https://www.googleapis.com/auth/documents.readonly"]
service = build("docs", "v1", credentials=creds)
document = service.documents().get(documentId=DOCUMENT_ID).execute()
print(f"The title of the document is: {document.get('title')}")
```

#### 2. 输出文档 JSON

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 输出 JSON（现代） | output-json/output_json.py | documents().get() | 使用 google.auth.default() |
| 输出 JSON（遗留） | output-json/output-json.py | documents().get() | 使用 oauth2client，支持 includeTabsContent=True |

**现代版示例：**
```python
import json
import google.auth
from googleapiclient.discovery import build

creds, _ = google.auth.default()
service = build("docs", "v1", credentials=creds)
result = service.documents().get(documentId=DOCUMENT_ID).execute()
print(json.dumps(result, indent=4, sort_keys=True))
```

**遗留版示例：**
```python
from apiclient import discovery
from httplib2 import Http
from oauth2client import client, file, tools

store = file.Storage("token.json")
creds = store.get()
if not creds or creds.invalid:
    flow = client.flow_from_clientsecrets("credentials.json", SCOPES)
    creds = tools.run_flow(flow, store)
service = discovery.build(
    "docs", "v1",
    http=creds.authorize(Http()),
    discoveryServiceUrl="https://docs.googleapis.com/$discovery/rest?version=v1"
)
result = service.documents().get(
    documentId=DOCUMENT_ID, 
    includeTabsContent=True
).execute()
```

#### 3. 邮件合并（Mail Merge）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 邮件合并 | mail-merge/docs_mail_merge.py | Drive API + Docs API + Sheets API | 批量生成个性化文档 |

**功能描述：**
1. 读取收件人数据（支持纯文本或 Google Sheet 数据源）
2. 复制 Google Docs 信函模板
3. 使用 batchUpdate 批量替换占位符变量（如 {{TO_NAME}}）
4. 生成个性化文档到 Google Drive

**所需权限：**
```python
SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
)
```

**核心代码：**
```python
# 复制模板
copy_id = DRIVE.files().copy(
    body={"name": "Merged form letter"},
    fileId=tmpl_id,
    fields="id"
).execute().get("id")

# 构建替换请求
reqs = [
    {
        "replaceAllText": {
            "containsText": {
                "text": "{{TO_NAME}}",
                "matchCase": True,
            },
            "replaceText": value,
        }
    }
    for key, value in merge.items()
]

# 执行批量替换
DOCS.documents().batchUpdate(
    body={"requests": reqs},
    documentId=copy_id,
    fields=""
).execute()
```

### Docs API 使用注意事项

1. **API 能力限制：**
   - Docs API 主要用于批量更新（batchUpdate），不支持直接创建新文档（需通过 Drive API 创建）
   - 读取操作使用 documents().get()，返回完整的文档结构 JSON

2. **占位符约定：**
   - 邮件合并示例中使用双大括号格式：{{VARIABLE_NAME}}
   - 替换时使用 replaceAllText 请求类型

3. **多 API 协作：**
   - 创建文档：Drive API files().create()
   - 复制文档：Drive API files().copy()
   - 读取/更新内容：Docs API
   - 读取数据源：Sheets API

4. **遗留代码注意：**
   - output-json.py 使用 oauth2client（已弃用），建议迁移到 google-auth

---

## Google Sheets API

### API 版本
- **Sheets API v4**

### 参考文件位置

```
Google_workspace_reference/sheets/
├── quickstart/quickstart.py              # 基础入门：读取电子表格数据
├── quickstart/requirements.txt
├── quickstart/README.md
└── snippets/
    ├── sheets_create.py                  # 创建电子表格
    ├── sheets_get_values.py              # 读取单个范围
    ├── sheets_batch_get_values.py        # 批量读取多个范围
    ├── sheets_update_values.py           # 更新单个范围
    ├── sheets_batch_update_values.py     # 批量更新多个范围
    ├── sheets_append_values.py           # 追加数据
    ├── sheets_batch_update.py            # 批量结构更新
    ├── sheets_conditional_formatting.py  # 条件格式
    ├── sheets_pivot_tables.py            # 数据透视表
    └── sheets_filter_views.py            # 筛选视图
```

### 功能分类与参考文件

#### 1. 基础入门

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 读取数据 | quickstart/quickstart.py | spreadsheets().values().get() | 读取指定范围的数据 |

**Quickstart 关键代码：**
```python
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
service = build("sheets", "v4", credentials=creds)
result = service.spreadsheets().values().get(
    spreadsheetId=SAMPLE_SPREADSHEET_ID,
    range=SAMPLE_RANGE_NAME
).execute()
values = result.get("values", [])
```

#### 2. 电子表格创建与读取

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建电子表格 | sheets_create.py | spreadsheets().create() | 创建空白电子表格 |
| 读取单个范围 | sheets_get_values.py | spreadsheets().values().get() | 读取一个 A1 范围 |
| 批量读取 | sheets_batch_get_values.py | spreadsheets().values().batchGet() | 同时读取多个范围 |

**创建电子表格示例：**
```python
spreadsheet = {"properties": {"title": title}}
spreadsheet = service.spreadsheets().create(
    body=spreadsheet, fields="spreadsheetId"
).execute()
print(f"Spreadsheet ID: {spreadsheet.get('spreadsheetId')}")
```

**读取数据示例：**
```python
result = service.spreadsheets().values().get(
    spreadsheetId=spreadsheet_id, range=range_name
).execute()
rows = result.get("values", [])
```

**批量读取示例：**
```python
result = service.spreadsheets().values().batchGet(
    spreadsheetId=spreadsheet_id, ranges=range_names
).execute()
ranges = result.get("valueRanges", [])
```

#### 3. 数据写入

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 更新单个范围 | sheets_update_values.py | spreadsheets().values().update() | 覆盖写入数据 |
| 批量更新 | sheets_batch_update_values.py | spreadsheets().values().batchUpdate() | 同时更新多个范围 |
| 追加数据 | sheets_append_values.py | spreadsheets().values().append() | 在末尾追加行 |

**更新数据示例：**
```python
body = {"values": [["A", "B"], ["C", "D"]]}
result = service.spreadsheets().values().update(
    spreadsheetId=spreadsheet_id,
    range=range_name,
    valueInputOption="USER_ENTERED",
    body=body
).execute()
```

**valueInputOption 说明：**
- USER_ENTERED：值像用户输入一样被解析（公式会被计算）
- RAW：值按原样写入（公式作为文本）

#### 4. 结构更新（Batch Update）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 批量结构更新 | sheets_batch_update.py | spreadsheets().batchUpdate() | 修改标题、查找替换等 |

**批量结构更新示例：**
```python
requests = []
requests.append({
    "updateSpreadsheetProperties": {
        "properties": {"title": new_title},
        "fields": "title"
    }
})
requests.append({
    "findReplace": {
        "find": "old_text",
        "replacement": "new_text",
        "allSheets": True
    }
})
body = {"requests": requests}
response = service.spreadsheets().batchUpdate(
    spreadsheetId=spreadsheet_id, body=body
).execute()
```

#### 5. 条件格式

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 条件格式 | sheets_conditional_formatting.py | spreadsheets().batchUpdate() | addConditionalFormatRule |

#### 6. 数据透视表

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建透视表 | sheets_pivot_tables.py | spreadsheets().batchUpdate() | addSheet + updateCells |

#### 7. 筛选视图

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 筛选视图 | sheets_filter_views.py | spreadsheets().batchUpdate() | addFilterView / duplicateFilterView / updateFilterView |

### Sheets API 使用注意事项

1. **两种更新方式：**
   - values() 方法：用于读写单元格值（简单高效）
   - batchUpdate() 方法：用于结构修改（格式、透视表、筛选等）

2. **A1 表示法：**
   - 单范围：Sheet1!A1:C2
   - 跨工作表：使用 sheetId（数字 ID）

3. **颜色格式：**
   - 使用 RGB 值（0.0 - 1.0 的浮点数）

4. **GridRange 结构：**
   - sheetId, startRowIndex, endRowIndex, startColumnIndex, endColumnIndex

## Google Gmail API

### API 版本
- Gmail API v1

### 参考文件位置

```
Google_workspace_reference/gmail/
├── quickstart/quickstart.py
├── quickstart/requirements.txt
├── quickstart/README.md
└── snippet/
    ├── list_messages.py
    ├── send mail/
    │   ├── create_draft.py
    │   ├── create_draft_with_attachment.py
    │   └── send_message.py
    ├── settings snippets/
    │   ├── create_filter.py
    │   ├── enable_auto_reply.py
    │   ├── enable_forwarding.py
    │   └── update_signature.py
    ├── smime snippets/
    │   ├── create_smime_info.py
    │   ├── insert_smime_info.py
    │   ├── insert_cert_from_csv.py
    │   ├── update_smime_cert.py
    │   └── update_smime_from_csv.py
    └── thread/
        └── threads.py
```

### 功能分类与参考文件

#### 1. 基础入门

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 列出标签 | quickstart/quickstart.py | users().labels().list() | 列出所有 Gmail 标签 |
| 列出邮件 | list_messages.py | users().messages().list() | 列出收件箱邮件 |

**Quickstart 关键代码：**
```python
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
service = build("gmail", "v1", credentials=creds)
results = service.users().labels().list(userId="me").execute()
labels = results.get("labels", [])
```

#### 2. 发送邮件与草稿

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建草稿 | send mail/create_draft.py | users().drafts().create() | 创建无附件草稿 |
| 创建带附件草稿 | send mail/create_draft_with_attachment.py | users().drafts().create() | 创建带附件草稿 |
| 发送邮件 | send mail/send_message.py | users().messages().send() | 直接发送邮件 |

**邮件编码方式：**
```python
import base64
from email.message import EmailMessage

message = EmailMessage()
message.set_content("This is automated draft mail")
message["To"] = "recipient@example.com"
message["From"] = "sender@example.com"
message["Subject"] = "Automated draft"

encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
create_message = {"raw": encoded_message}
```

#### 3. 设置管理

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建过滤器 | settings snippets/create_filter.py | users().settings().filters().create() | 自动分类邮件 |
| 启用自动回复 | settings snippets/enable_auto_reply.py | users().settings().updateVacation() | 设置假期自动回复 |
| 启用转发 | settings snippets/enable_forwarding.py | users().settings().updateAutoForwarding() | 设置邮件转发 |
| 更新签名 | settings snippets/update_signature.py | users().settings().sendAs().patch() | 修改邮件签名 |

#### 4. 邮件会话（Threads）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 查看会话 | thread/threads.py | users().threads().list() / .get() | 查找长会话邮件 |

#### 5. S/MIME 证书管理

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建 S/MIME 信息 | smime snippets/create_smime_info.py | 纯辅助函数 | 读取证书文件并编码 |
| 插入证书 | smime snippets/insert_smime_info.py | users().settings().sendAs().smimeInfo().insert() | 上传证书 |
| 批量插入 | smime snippets/insert_cert_from_csv.py | 调用辅助函数 | 从 CSV 批量处理 |
| 更新证书 | smime snippets/update_smime_cert.py | smimeInfo().list() / .setDefault() | 智能更新默认证书 |
| 批量更新 | smime snippets/update_smime_from_csv.py | 调用辅助函数 | 从 CSV 批量更新 |

### Gmail API 使用注意事项

1. 用户 ID：始终使用 "me" 表示当前认证用户
2. 邮件编码：必须使用 base64.urlsafe_b64encode() 编码邮件内容
3. 附件处理：使用 mimetypes.guess_type() 猜测 MIME 类型
4. 转发验证：必须先创建转发地址并等待验证状态为 accepted
5. S/MIME：需要 Workspace 企业版支持

## Google Slides API

### API 版本
- Slides API v1

### 参考文件位置

```
Google_workspace_reference/slides/
├── quickstart/quickstart.py
├── quickstart/requirements.txt
├── quickstart/README.md
└── snippets/
    ├── slides_create_presentation.py
    ├── slides_copy_presentation.py
    ├── slides_create_slide.py
    ├── slides_create_textbox_with_text.py
    ├── slides_create_image.py
    ├── slides_create_sheets_chart.py
    ├── slides_simple_text_replace.py
    ├── slides_text_merging.py
    ├── slides_image_merging.py
    ├── slides_text_style_update.py
    ├── slides_create_bulleted_text.py
    └── slides_refresh_sheets_chart.py
```

### 功能分类与参考文件

#### 1. 基础入门

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 读取演示文稿 | quickstart/quickstart.py | presentations().get() | 获取幻灯片和元素数量 |

#### 2. 演示文稿生命周期

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建演示文稿 | slides_create_presentation.py | presentations().create() | 创建空白演示文稿 |
| 复制演示文稿 | slides_copy_presentation.py | drive.files().copy() | 通过 Drive API 复制 |

#### 3. 幻灯片管理

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 添加幻灯片 | slides_create_slide.py | batchUpdate() + createSlide | 使用指定布局添加幻灯片 |

#### 4. 添加内容

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 创建文本框 | slides_create_textbox_with_text.py | batchUpdate() + createShape + insertText | 创建文本框并插入文字 |
| 插入图片 | slides_create_image.py | batchUpdate() + createImage | 从 URL 插入图片 |
| 插入 Sheets 图表 | slides_create_sheets_chart.py | batchUpdate() + createSheetsChart | 嵌入可刷新图表 |

#### 5. 文本操作与格式化

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 简单文本替换 | slides_simple_text_replace.py | batchUpdate() + deleteText + insertText | 替换形状内所有文本 |
| 文本样式更新 | slides_text_style_update.py | batchUpdate() + updateTextStyle | 设置粗体、颜色、字体等 |
| 项目符号 | slides_create_bulleted_text.py | batchUpdate() + createParagraphBullets | 添加项目符号列表 |

#### 6. 合并操作（Mail Merge）

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 文本合并 | slides_text_merging.py | batchUpdate() + replaceAllText | 从 Sheet 数据批量替换 |
| 图片合并 | slides_image_merging.py | batchUpdate() + replaceAllShapesWithImage | 替换形状为图片 |

#### 7. 图表刷新

| 功能 | 参考文件 | 核心 API | 说明 |
|------|----------|----------|------|
| 刷新图表 | slides_refresh_sheets_chart.py | batchUpdate() + refreshSheetsChart | 刷新链接的 Sheets 图表 |

### Slides API 使用注意事项

1. 单位系统：
   - PT（点）：用于文本和形状大小
   - EMU（English Metric Units）：用于图片和图表，1 EMU = 1/360000 厘米

2. 所有修改操作都通过 batchUpdate() 批量执行

3. 复制演示文稿需使用 Drive API，Slides API 不提供复制功能

4. replaceAllText 和 replaceAllShapesWithImage 支持邮件合并场景

5. createSheetsChart 的 linkingMode=LINKED 可实现数据联动


---

## 附录：各 API 所需权限范围（Scopes）

| API | 常用 Scope | 说明 |
|-----|-----------|------|
| Drive API | `https://www.googleapis.com/auth/drive` | 完整读写权限 |
| Drive API | `https://www.googleapis.com/auth/drive.metadata.readonly` | 只读元数据 |
| Drive API | `https://www.googleapis.com/auth/drive.appdata` | 应用数据文件夹 |
| Drive Activity | `https://www.googleapis.com/auth/drive.activity.readonly` | 活动记录只读 |
| Docs API | `https://www.googleapis.com/auth/documents` | 完整读写 |
| Docs API | `https://www.googleapis.com/auth/documents.readonly` | 只读 |
| Sheets API | `https://www.googleapis.com/auth/spreadsheets` | 完整读写 |
| Sheets API | `https://www.googleapis.com/auth/spreadsheets.readonly` | 只读 |
| Gmail API | `https://www.googleapis.com/auth/gmail.modify` | 读写（除删除外） |
| Gmail API | `https://www.googleapis.com/auth/gmail.readonly` | 只读 |
| Gmail API | `https://www.googleapis.com/auth/gmail.send` | 仅发送 |
| Gmail API | `https://www.googleapis.com/auth/gmail.settings.basic` | 基础设置 |
| Slides API | `https://www.googleapis.com/auth/presentations` | 完整读写 |
| Slides API | `https://www.googleapis.com/auth/presentations.readonly` | 只读 |

## 快速参考：各 API 核心方法

| API | 核心类 | 常用方法 |
|-----|--------|----------|
| Drive v3 | `service.files()` | create(), get(), update(), delete(), list(), copy() |
| Drive v3 | `service.permissions()` | create(), delete(), list() |
| Drive v3 | `service.drives()` | create(), get(), list(), delete() |
| Drive v3 | `service.changes()` | getStartPageToken(), list(), watch() |
| Docs v1 | `service.documents()` | get(), batchUpdate(), create() |
| Sheets v4 | `service.spreadsheets()` | create(), get(), batchUpdate() |
| Sheets v4 | `service.spreadsheets().values()` | get(), update(), append(), batchGet(), batchUpdate() |
| Gmail v1 | `service.users().messages()` | send(), get(), list(), delete(), modify() |
| Gmail v1 | `service.users().drafts()` | create(), get(), list(), send(), delete() |
| Gmail v1 | `service.users().threads()` | get(), list(), modify(), delete() |
| Gmail v1 | `service.users().settings()` | getVacation(), updateVacation(), getAutoForwarding(), updateAutoForwarding() |
| Gmail v1 | `service.users().settings().filters()` | create(), get(), list(), delete() |
| Gmail v1 | `service.users().settings().sendAs()` | list(), get(), create(), patch(), delete() |
| Slides v1 | `service.presentations()` | create(), get(), batchUpdate() |

---

*本文档由自动分析工具根据 Google Workspace 官方示例代码生成，仅供参考。实际使用时请以最新官方文档为准。*

```

---


=====

---
chunk_metadata:
  chunk_id: "19d9cd5fb460e3fe"
  title: "Meta Pivots From Open Weights, Big Pharma Bets On AI, Regulatory Patchwork, Simulating Human Cohorts"
  message_count: 1
---

# Meta Pivots From Open Weights, Big Pharma Bets On AI, Regulatory Patchwork, Simulating Human Cohorts

### [第 1 轮] From: "The Batch @ DeepLearning.AI" <thebatch@deeplearning.ai> | Date: Fri, 17 Apr 2026 12:05:29 -0700
**To**: javen.dai@fstln.io

AI-native software engineering teams operate very differently than traditional teams.

View in browser (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2v5m_5PW8wM7ks6lZ3mjVptkY23bwlQfW1dbD2s3X64MXW5-cjB15WvWcLW3Y29WH4l4HdmW8Q4gMQ27tnyZVjjZGh3pslPMN2vjdMhy73zsW7xfGvn3myhKgW3ZzWMR8kv_Q9W1vVxGd8b6jPpW3QMgV42gMRPKW6y-FNq92hRlGW8cQ69t9kcvGnVtTXm27QwgBRVdg_g02RYzfYW5bdzgx7p9SVgW96BwfT1kNYhkW3yQZ6H6N---3W7mGGbk1T8rSqW3Z8v7T2TC9txW5rtqkf1jkFJQW3PyD081DxKqnW8dJ9Hb5shCgNW83-_W968QdzVW8nKwRQ64ZcqBN60lqBML3fSVW7sxl6y34f--MW3cqCnT6151N2W6Qrt3q5WWL3WW6ScdwS2LYtmcW4thk4t8V4sB1W1fZh3b6n8fPNW3NLq3G8q4HdhW947tDX3XjL8RW5bjkWg8h7kyvW6MjbM98cP7lrW5lZ1Rl6KxP4jVK9vlF5RxGRhW7Q4wp77YsDZGW7lLhmd8md23YVCF-Zl607KCNW872xpp8WKlmPW6yWcZv53GHvzW8gqjRP3GWWxjf8gknNl04 )

The Batch top banner - April 17, 2026 (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3prCCW6N1vHY6lZ3nZW4YYBWb6pNgKgVp0ytf8KcCfCW2tGD0w5ZNkVYW7kNmZh6GcB7XVcCvFD8jsBNGW6P2fJC3VnQS_W3s1qLP4cc8G4W6-SWkf3TKRflW6b1vwq1XG8-fVjXtC93kc7GnVnpmw95nxrsxW7RYg0B4scRr-W8_Gx6d3kCfB1N3qkNwc8Llk6N6zMBmfJH6B8W59RlZB3K-bgmVH0S8y7vPMzYW7dGTw41h8YDPW77-VNl2C4pxJW7v_wHd1xy8dYW7W9GLh3993Z9W5qfxrZ5Z4QQLf7pBT5F04 )

Subscribe (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3l5QzW6N1vHY6lZ3kCVLXPjv22Psw6W5nx_6K36LCtrW2rB9w51550r7W49ZwrN5fChSbW4t1M_m7K2bqlW2RDMYy6NvWDCW8z-DS45VydRnW3Vhyqw5Ys1R6W3_fyyQ7QgbTLW4QfQpS3Jh6PDW2fdR2Z4cknZHW6skp1w6fQ6cvVvt70j7_Y9xXW61DmzY7pQnTDW8qTdYr2J0nbNW57gT557btXxmW3xlF1_5s_t3pN3B2qyvy0tyZN1TgDNSRZkphN4xBg4q1Dtq8W5JnXbh5ns9ZRW3gy6771XyPvGf3NCZtz04 ) Submit a tip (mailto:thebatch@deeplearning.ai?subject=RE%3A%20Tips%20and%20News)

Dear friends,

AI-native software engineering teams operate very differently than traditional teams. The obvious difference is that AI-native teams use coding agents to build products much faster, but this leads to many other changes in how we operate. For example, some great engineers now play broader roles than just writing code. They are partly product managers, designers, sometimes marketers. Further, small teams who work in the same office, where they can communicate face-to-face, can move incredibly quickly.

Because we can now build fast, a greater fraction of time must be spent deciding what to build. To deal with this project-management bottleneck (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2v3prCCW8wLKSR6lZ3lkW5FzhGh2LxDbVW2XWPVK9cgl9DW3S5mZH23rsSbW98b9d04F-l5_VfR6ls7Wt5szW8GSlnk76TyDzW84w8RG5Nd092W6Gm0678rMLCDTgh5m5bm3N7W2P0xjs4b9-wbW1zr0G_4c-yMPW8lwYmc81yPYHW7ncq307mNS7HW9c1rsk5dqrQYW7tz-_428plfwW2hqj6s1r9w2rVsL1Yn2QbNR_W3F1Zg11lDzhfW6WRdll7Rr7WsW1kcDrw84Lf8kW73rWDd3Jzqc7W7j682L6ycPzDW1lgSWB7V1G74W85wRDD8qPbPQVmQLL02TQmmCW6tP6sd92bH35W6hGs-56GnD9wMtL8hjGn3CWf46FKVT04 ) , some teams are pushing engineer:product manager (PM) some teams are pushing engineer:product manager (PM) ratios downward from, say, 8:1 to as low as 1:1. But we can do even better: If we have one PM who decides what to build and one engineer who builds it, the communication between them becomes a bottleneck. This is why the fastest-moving teams I see tend to have engineers who know how to do some product work (and, optionally, some PMs who know how to do some engineering work). When an engineer understands users and can make decisions on what to build and build it directly, they can execute incredibly quickly.

I’ve seen engineers successfully expand their roles to including making product decisions, and PMs expand their roles to building software. The tech industry has more engineers than PMs, but both are promising paths. If you are an engineer, you’ll find it useful to learn some product management skills, and if you’re a PM, please learn to build!

Looking beyond the product-management bottleneck, I also see bottlenecks in design, marketing, legal compliance, and much more. When we speed up coding 10x or 100x, everything else becomes slow in comparison. For example, some of my teams have built great features so quickly that the marketing organization was left scrambling to figure out how to communicate them to users — a marketing bottleneck. Or when a team can build software in a day that the legal department needs a week to review, that’s a legal compliance bottleneck. In this way, agentic coding isn’t just changing the workflow of software engineering, it’s also changing all the teams around it.

When smaller, AI-enabled teams can get more done, generalists excel. Traditional companies need to pull together people from many specialties — engineering, product management, design, marketing, legal, etc. — to execute projects and create value. This has resulted in large teams of specialists who work together. But if a team of 2 persons is to get work done that require 5 different specialities, then some of those individuals must play roles outside a single speciality. In some small teams, individuals do have deep specializations. For example, one might be a great engineer and another a great PM. But they also understand the other key functions needed to move a project forward, and can jump into thinking through other kinds of problems as needed. Of course, proficiency with AI tools is a big help, since it helps us to think through problems that involve different roles.

Even in a two-person team, to move fast, communication bottlenecks also must be minimized. This is why I value teams that work in the same location. Remote teams can perform well too, but the highest speed is achieved by having everyone in the room, able to communicate instantaneously to solve problems.

This letter focuses on AI-native teams with around 2-10 persons, but not everything can be done by a small team. I'll address the coordination of larger teams in the future.

I realize these shifts to job roles are tough to navigate for many people. At the same time, I am encouraged that individuals and small teams who are willing to learn the relevant skills are now able to get far more done than was possible before. This is the golden age of learning and building!

Keep building,

Andrew

A MESSAGE FROM DEEPLEARNING.AI (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1j3prCCW69sMD-6lZ3ltW5HKcNb6Vzqs2W2wn-Kg2BSnvLW6H2rzr3q4TNTW4Xzrdj7cw5LXW5bt0_M6j5-TfW1PjGK65Vk5wjW7cY5H02fkygPW2Ypj0x7Rk8vrW8bYxSg1ZvnyWV2sP4x5TxQ0SW9cbdFS87BJqkW8bSWT44v16vWW2fbDKf7rL_FgW19X5gG5QzHrdMJfFnkTKTWJW8pzwTC1fY62LW8SCTn982JyMwW4qdW1F4zNcrWVtfbYG8nLvwwW4Z7T8d3jlnXtf93HFwq04 )

Promo banner for: "Spec-Driven Development" (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mwW2g44DM8td-6TW8NJMts1fs1QgW1bbc4L3GjByYW65YqG_4ylQ0fW6lxCzL4N5tYVW2SN9sz9cchNxW1TN8v83xk-B3W6lXhtC3DGNVwW6DRdwN2mDKfxVzc6QC4yWynDW4Lv_fH405zQWW5TK2sz1d6q1YW3zD5j08WZsBKN7_lgHCpqCR4Vkbym24y1sFxW59WW0b82l-N8W7_bn6V6v4fW8W33qZz84FPcqfW5fVYp_3tP5BQW8hpqXL6_xHqDW2tbTPG5wMct2N7LKztXQ7fR_W2V8LGy8nwfqgW3Z8C6S2qLmqXW7MkH-j4Xh9wFW3fTH8c6CZg6sf7G90c604 )

In “Spec-Driven Development,” you will learn a disciplined workflow for working with coding agents. Write specs, guide implementation step by step, and stay in control of what you build! Join in for free (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mwW2g44DM8td-6TW8NJMts1fs1QgW1bbc4L3GjByYW65YqG_4ylQ0fW6lxCzL4N5tYVW2SN9sz9cchNxW1TN8v83xk-B3W6lXhtC3DGNVwW6DRdwN2mDKfxVzc6QC4yWynDW4Lv_fH405zQWW5TK2sz1d6q1YW3zD5j08WZsBKN7_lgHCpqCR4Vkbym24y1sFxW59WW0b82l-N8W7_bn6V6v4fW8W33qZz84FPcqfW5fVYp_3tP5BQW8hpqXL6_xHqDW2tbTPG5wMct2N7LKztXQ7fR_W2V8LGy8nwfqgW3Z8C6S2qLmqXW7MkH-j4Xh9wFW3fTH8c6CZg6sf7G90c604 )

News

Life After Llama

Meta pivoted from its open-weights strategy to deliver a closed alternative.

What’s new: Meta introduced (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1W3prCCW7lCdLW6lZ3nNW6Jyp7d7LSGxzW51T2N23y7HlkW1GC9xC1w4tnBV4fK333svkDrW54zf2S5SBMXRW2r8FH05yXQJxVsb4FB1bQfmkW6Xqg2R91DH2lW2_-zpl8SF4LZW6--znR4l_z12N6knf2FWDNnlW9ldxRr1Yx5bXW8Q2S9555xV-BW87McSw9lhrfLW858kjS5pRqtvW87NPll8h42gVW4W6S8f6wrLcZV6cBZy1zpQKVW5V2g224jDMxBV_1mG61l1Wz3W1Ly2c72tbSR4W56wd9j5l_CllW1hy0vF8q62cXW8wK5W91rwjw0f7G85Gv04 ) its first AI model in a year and the first product of its nine-month-old Superintelligence Labs. Muse Spark is a natively multimodal reasoning model with support for tool use and multi-agent orchestration. It leads in some health and multimodal benchmarks but falls short in coding and agentic work, which Meta frames as validating an architectural redesign on which the company plans to build larger models.

- Input/output: Text, image, speech in (up to 262,000 tokens), text out
- Performance: Fourth place on the Artificial Analysis (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mPW1jwrrp81Tt52W5TxFQm3Q0yccN8TGFWcBJHWRW3hZFFv7yfrJGVKWLPx6SF1KjW6GVYhz7v30mNN1mDKdYFYxgzW77qDvF2knMH6W8bVVpW8FtYW1N3DrHM0lY3FWVzbJ-G8cFJKpW6Shh3F2JLxQPW1kCLn75MgfR3W3b98Qr2WSdh0W8GX61m64L32mVLCwLr5xKlCsW4llg8w7ZZCNzW2kt-LH5PGPKlW65fvXY4V3Y1kN5D5gXp8MwrJW5TsKl654Q4_FW6RXfSN1XZzGqW5xsQsH7mXQzxW5j-6mj4yDv8xW2TlNZ_879jg0N4PHKk1nR6lyf2X5Rpv04 ) Intelligence Index
- Availability: Free via meta.ai (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1j3prCCW69sMD-6lZ3lxW15RNVQ7SZWC0W1_25mb53zBRDV9tGY02XMWH5W1DxKJg3BMVy5W8nkb991jY0_wW6wkZwq3zJm7RW2n0c4c5x8qFlW7L4c_q2X1-SkW4-TXyT6MXRh3W7SjMkT1lmzmSW6Q_WwZ6vBCL0W2HCztT5VjL0qW96898Q59mDypW4K8VfH5CwfYVW2yb1VL8W_p64W2w7xdl1BrY6nW4nG78225bM87W2gjsF-1skVp8W4qff0m8qB1cHW77SfZ55jt3WSf8Gft3Y04 ) and Meta AI app; coming to WhatsApp, Instagram, Facebook, Messenger, and Ray-Ban Meta AI glasses; API preview for selected partners
- Features: Three reasoning modes (instant, thinking, contemplating), shopping mode
- Undisclosed: Parameter count, architecture, training data and methods, output size limit

How it works: Meta disclosed limited technical details about Muse Spark but highlighted gains in training efficiency and multi-agent orchestration plus a domain-specific investment in health.

- The company reworked its pretraining approach, model architecture, optimization, and data curation. Meta says Muse Spark matches Llama 4 Maverick’s capabilities with over an order of magnitude less processing devoted to training.
- Post-training involved reinforcement learning in which the team penalized the model for using excessive reasoning tokens, a process the team calls thought compression. Under this penalty, the model first improved by reasoning longer, then learned to compress its reasoning, and then extended its reasoning for further improvement.
- Rather than processing a single chain of thought, contemplating mode launches multiple agents that propose solutions, refine them, and aggregate the results in parallel. Meta says this achieves better performance while incurring comparable latency.
- To improve health reasoning, Meta enlisted more than 1,000 physicians to help curate training data aimed at producing more accurate and thorough health responses.

Results: Muse Spark’s benchmark performance is generally competitive and notably token-efficient. Meta acknowledged that it shows gaps in coding and agentic performance.

- On the Artificial Analysis (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mPW1jwrrp81Tt52W5TxFQm3Q0yccN8TGFWcBJHWRW3hZFFv7yfrJGVKWLPx6SF1KjW6GVYhz7v30mNN1mDKdYFYxgzW77qDvF2knMH6W8bVVpW8FtYW1N3DrHM0lY3FWVzbJ-G8cFJKpW6Shh3F2JLxQPW1kCLn75MgfR3W3b98Qr2WSdh0W8GX61m64L32mVLCwLr5xKlCsW4llg8w7ZZCNzW2kt-LH5PGPKlW65fvXY4V3Y1kN5D5gXp8MwrJW5TsKl654Q4_FW6RXfSN1XZzGqW5xsQsH7mXQzxW5j-6mj4yDv8xW2TlNZ_879jg0N4PHKk1nR6lyf2X5Rpv04 ) Intelligence Index, a composite of 10 benchmarks of economically useful tasks, Muse Spark set to reasoning (52) places fourth overall behind the tied-for-third Gemini 3.1 Pro Preview set to high reasoning and GPT-5.4 set to xhigh reasoning (both 57), and Claude Opus 4.6 set to max reasoning (53). Muse Spark used around 59 million tokens to complete the index, compared to roughly 158 million tokens for Claude Opus 4.6 and 116 million tokens for GPT-5.4.
- Muse Spark earns top marks in at least one multimodal benchmark. On CharXiv Reasoning (understanding charts and figures), Muse Spark (86.4 percent) outperformed GPT-5.4 (82.8 percent) and Gemini 3.1 Pro (80.2 percent), according to Meta. On MMMU Pro (solving multidisciplinary visual problems), Muse Spark (81 percent) placed second behind Gemini 3.1 Pro (82 percent), according to Artificial Analysis.
- On Artificial Analysis’ Coding Index, a weighted average of coding benchmarks, Muse Spark (47) fell behind GPT-5.4 (57), Gemini 3.1 Pro Preview (56), and Claude Sonnet 4.6 set to max reasoning (51).
- Artificial Analysis independently measured (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mPW1jwrrp81Tt52W5TxFQm3Q0yccN8TGFWcBJHWRW3hZFFv7yfrJGVKWLPx6SF1KjW6GVYhz7v30mNN1mDKdYFYxgzW77qDvF2knMH6W8bVVpW8FtYW1N3DrHM0lY3FWVzbJ-G8cFJKpW6Shh3F2JLxQPW1kCLn75MgfR3W3b98Qr2WSdh0W8GX61m64L32mVLCwLr5xKlCsW4llg8w7ZZCNzW2kt-LH5PGPKlW65fvXY4V3Y1kN5D5gXp8MwrJW5TsKl654Q4_FW6RXfSN1XZzGqW5xsQsH7mXQzxW5j-6mj4yDv8xW2TlNZ_879jg0N4PHKk1nR6lyf2X5Rpv04 ) Muse Spark in Thinking mode at 39.9 percent on Humanity’s Last Exam, trailing Gemini 3.1 Pro Preview (44.7 percent) and GPT-5.4 (41.6 percent). However, Meta reports 58 percent when Muse Spark used contemplating mode.
- In Meta’s tests, Muse Spark outperformed all models on HealthBench Hard, a subset of OpenAI’s health benchmark, at 42.8 percent, ahead of second-best GPT-5.4 (40.1 percent). Muse Spark also led DeepSearchQA, an agentic browsing evaluation, at 74.8 percent, ahead of Claude Opus 4.6 Max (73.7 percent).

Behind the news: Muse Spark is the Meta’s first new model since it reorganized (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3nFW4L1VYQ8QvxdRW5NQ0sd779WpcW75r2Zw8YLg_pW2gv24F351v81N8RS4YP8jy9xN576pNb-BWBcW1wr8z38V8Hl3N7Dn1k5rSw-HW6bNY3012jrtKW24rBtk94HvtWW3zjmZ75nkWycW2XVfLL6YsBhfW86Y3Qx3rvwnlV8jM4T43x6QNW8Z0rM485sJ1-W76dk1m8lzw1HVwQJsf5RdVPTW1361Cp9b8C0GW4tj2Xt5M5DHkW7WQDNC9cKfFXVXfWj-7ldThfW1G9lV63_2fZ5W1khd7p6P1BLnW4-60Nt57Xk7CW7G_-kw1ZK8DnN8dwynyNP98RW7rRkR33NvXSPW8s46vY6svD5CW7DV_Hj2LMyjQW3D5SMv50qRQdW50wVLf1zfl1-W16dscm15PwJqf4T0SxH04 ) its AI labs after critics alleged that the training data for Llama 4 been contaminated with benchmark answers. In June 2025, Meta spent $14.3 billion for a 49 percent stake in Scale AI, brought in cofounder Alexandr Wang as chief AI officer, and launched a hiring spree with pay packages worth hundreds of millions of dollars. The proprietary release has raised concerns among developers, many of whom have built projects on open-weights Llama models.

Why it matters: Meta is investing in the capabilities that matter most for its product ambitions: multimodal perception for billions of camera-equipped users, health reasoning for one of the most common categories of AI queries, and multi-agent coordination for multi-step tasks. With a private API preview in progress, it’s positioning itself to compete for business customers alongside OpenAI, Google, and Anthropic. However, its pivot away from being the leading U.S. champion of open weights is a significant loss for the developer community.

We’re thinking: Muse Spark’s contemplating mode and Kimi K2.5’s (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv105m_5PW5BWr2F6lZ3kyW142yBC7MRC0qW5XXv4d1xjxcKW8bC92L5_KqzBW67N93c4YLf6TW1jFf752VHyZ0W5lsbyc7spG5zW5G4zmN97Gx3WW18pcTr5vcrbKW5WkWCs8_5gXBW7WfwGs8Qs3GLW1SG9VN1gklXjW2M6WmV4qzHNfW5w3v782-PdrxW1Z3gWN2rzyw9W6c45283Hj9dHW4NhHbN6m2kQ4N9dJrwjfKDnrW5zPjVY1HsMMgW1P_CVm9b2CxWW5JH-mx6cVv8cVjPww814kLFkW2n66Gh8czlKGW53_sKD8wlzxjW8FC6dC6CPvNtW7fsTJm5j2kTtW4yhrJf8s7_5KW5yYmn16Txnj3W775XrG52BZlXW2yGGKl1MkDXjW19dms55kRhxWN343j946xsjCW5x_dR38zpfTQW2fM9z28MM9X-W6ydPPj5x6Q41f25QPjC04 ) Agent Swarm point to an emerging pattern: More labs are scaling performance by training models to orchestrate multiple agents at inference time rather than training ever-larger single models.

Big Pharma Bets Big on AI

Generative AI has proven that it can produce text, images, audio, video, and code. The world’s most valuable pharmaceutical company is betting billions that it can produce drugs as well.

What’s new: Pharma giant Eli Lilly agreed (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3pLVHZKYM39-yj7W18t0DD5GPCVNW7_KxLF4q-DFSV5Dfkk58xfSDW7LkkXr5WJDQ9W7nchcr9cdyYgW7Bnpn_4DY3JCW68DJ9-8g6X8mW8FVNvg2WWCMZW932NbH8b58xdMyQGNTgn43sW83wW158Vt0rRW11c3C21Px25qW1L3ldW78Hw5pW1fxVKS9j4lDhW5CS7yX4CnfWDW5607GQ1Q0-zrW6ZnhmN8YCwYtW6Mfy8F1NqGmCW1b4qH54MJS7kW48jdrc8Bh0SBW44k_By1XW-HBVJztwk7rTB-tW7snKsr84033nW8pmF6t4Vp_w4W93LCnq3k46nFf3qRvK-04 ) to give as much as $2.75 billion to Insilico Medicine, a Hong Kong-based biotechnology company that applies generative AI across its drug-discovery pipeline. Initially, Lilly will pay $115 million for exclusive rights to develop and sell undisclosed drugs that have not yet been tested in humans, while further payments will be tied to developmental, regulatory, and commercial milestones, Fierce Biotech reported (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3lxW7WVKnX1VCGSRW7mkkQw7bJPgwW3xjB5h3NfQbLVq7sbS35mQ9qW91z2Vg7nXVhhVXFbMH8tq3rYVZpMCT7w1Sv2W2Sl74N9dTCcGW2569lf6z2Wh8W5crXnF1_T6D1W1-H1s-5MVXX6V87p1Q1x6by8VbRq6_6tNnYRVtb4w31GMx5dW7c-s595Zz6pXW1Dq2Kk4shZmgV6-rJ66J84WpW7gD-w65WwbLTW6xMVH177XVHnF5c7f9GtYB9W3MCrwh7vS8khW1jQ3GR2vTsK5W7pjPqN4fLGVmVJ4ymB1W2DWLW3R5fnC3Zs0DPW84Nx0b2Zr5SbW1zXnbX9j6K6dW8-z2lf6RNq7YW7Rfk7n4l9BhTN50cpS48psxVW88T_Vc8gDvBXW6zgF0s8YyBqvf6B4c0H04 ) . This is the third agreement between the companies following an AI software license in 2023 and a $100 million research collaboration in November 2025.

AI drug-discovery: Founded in 2014, Insilico has used AI to develop (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2P3prCCW95jsWP6lZ3m0W7lfWPc6kJKfHW8zs7WR6lcHGVN4fNWJ5TbW4BN8XGm3R98x5GN6y84DSP52c6W396mkx1T77vkW5SsT6j3ZqD0nW9jrP2c3T64nWVv4f151mnG5kW3Dnjf13bryNLVfwP5f5r2434W4B4v3c61TvcRW7MvXr43VcrxrW9hpypC4bCdmbW32Sjhs3JWpSsW8myyyT1km_F7W1wXj-z5fDxC3W4j5mgh5Xpj9HW2XtkMd8HBMkxW6SWG0s8ZHBh3W6fCRsl9cV1FHW8YCCnL4kKRfLW1-CBwM8fbJJ_W7Gvys336Mq8PVQYqNP1GwSnWW3fbvNV2wNR1pVQ7rzD3R7lgsW6-wzLN2rkksKMfMcX1RNR8xW1lHbvS8CSXPkf3zDPXP04 ) 28 candidate drugs, roughly half of which are in clinical trials. The most advanced one, Rentosertib, targets idiopathic pulmonary fibrosis (IPF), a disease in which scarring progressively reduces lung function. A Phase 2a trial (an early, small-scale test of efficacy) showed (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1W3prCCW7lCdLW6lZ3lqW7dspFV1R11ccW8Sb2ff86k84TW156zxQ5VqN-dN3hdYTbK_sp8N59JQg7M25nCN86B5SNDcqQCMyL7rq_6gjZV1HRGS56dq-QW55WpSW5qDrYXW8wmvv98BM1FrVrkbhy338qMwW6LRHyx5vTMTRW1bYcl-63MJZ-W3Ss52G4_M-KGW3GC8jW8F6hmwW1Cqk4q4WgBN4W7wyL9c6W5SP9W8hZbgl6h3ptyW56fKzn6jZf1DN4vMJY781FKtW5KHRpw3tykTMW5HS6pH17FBtfW3FNjSp474fn7VPztW44zWxVmf7FgsY404 ) positive results. A second drug, Garutadustat, which is intended to treat inflammatory bowel disease, entered (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3p8W8yVfdx4VrVXvW7HHFSL19M-PfW1RMPXQ6069F8W3hnSSg5Cqyv4W4gvFM42lYfLzW7SfBj13Q30V6Mf60smLt2hPW330L2v6bkWGzW8MVbtl4QZ8_5N4ytNL19Lq79W5WMG-Z7d9QkrW9hfL_R4PZyJ3W8KxvKy7VxnCVW772Lqc5m32nPMdTfSgyFHD-VyBd8Y8lZ_WtW6RSD_Y2C-HjpN5Yg8y0b9QCyW8dBLmb7LkLK0W2GcLWm9gv4VwVnlLkX7zrjhkW2Z4-YK39NSdLV248Qt6T3z9rW27TCVm6MXJNDW6lw5Ww87KlvVW1f7BB71zLWDRf14QS3F04 ) Phase 2a in January 2026.

How it works: After choosing a disease, Insilico applies (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1W3prCCW7lCdLW6lZ3pYW4pWkQr96LKlQW8gzMnK5js2BgLsgP3FfrR1W4WTsTX2td_7MW2bcLWf94HzLyW4TjRLq4cwQJpVz-G5T8x9XBNN3cJ94tX1NBHW7S_PZB7CjwL5W4hKbp056yTPsN72yY8J_Ly1ZW6d7mHb4-yG55W3Hhcz87YqZSTW7bKkB69jT_VWW3B61yt5YPN49W432Hf442WvjgW8QfjSb2pqcFZTMTK27q_SDZW74CwD37MQsxLW1rlswL8v3GpHW2crVvt3D__6fW7w09bB8qZDlQW6YLkqc7ZmHtMW4TtpCB5tnpmCd5Thv804 ) proprietary generative models to two stages of drug discovery: identifying which protein to target and designing a molecule to act on that protein.

- To find targets, Insilico uses a tool called PandaOmics to analyze biological datasets, published research, patents, clinical trials, and grant applications. Deep learning models rank candidate targets by relevance to a disease, suitability as drug targets, and novelty. For IPF, PandaOmics identified TNIK, a protein involved in the scarring that characterizes IPF and related diseases, as the top candidate. No one had previously tried to treat IPF by blocking TNIK.
- To design a molecule to block TNIK, the team used Chemistry42. Roughly 30 generative models ran in parallel to produce candidate molecular structures, each one optimized for binding strength, toxicity, solubility, and other properties. Scientists evaluated and refined the output over multiple rounds. The process yielded a lead molecule after Insilico synthesized and tested fewer than 80 compounds. In conventional drug discovery, teams often screen 200,000 to 1 million existing compounds before synthesizing and testing hundreds of candidates.
- The time from identifying targets to synthesizing molecules that are ready for preclinical safety testing took roughly 18 months (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3mKN4csfRWTwHqpW239JCt3lW7WqN3TGl9rph44pW2bndx91K4qPpW2Vnr3-7_Lf7NW5TnB3K3mpmn-W1HPTxJ81YbvWW4VCJMg7TGpZRMq7dTDLP8g_W6g8zCY1tRjnXW7WtyPY2GhgffW9jmFGj29LJgdW6PW6053MnPKxN1tD6BTSGf4ZW1qvJgB6bscVpW3jPyvZ5Rz2XvVZ9wBT4Y2kfYN2tVsjl_8JvPW7KjnJZ5gWGB2W35YB7w4JhG5kW60D1dj2WWp6fW3tK9Hr6H03vnW8t7TC56JMh6YW3bd2DH60bpyDN7vF4thNhhQ9W7zmdmX35DnNCf8fKrq004 ) , compared to a typical five to six years (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3lBW1K_rq31vPmCQW15p14B1k7S--VJjD2q8VFrs7VWKvyd77myffW8tnPcs6-wR4TW33wgLN8fL97nW1kN1MW4BmgM9W4sSZC38JBk7jW5LX9hm6034jPW58JRY-8hSKDpW4hGg227LplFwW8LbqF32vM72JW91MXG73gCVLgW8dLjPP5RHGglW8LPZ7K1FTXn9TRhZ04B4Z7hW18W9fV4dmm_MW3NHVGF4HN7cnW40xBdL25tVmwW1vz_zd2xsLCMW4BS10D4MYdrjW72pcnS25JhC4W4JXB2J2cnBHGN57WJY8Vhr92Vt9ywY6Yg2R9W93J8T25kQbv1VlwXBz1Wf-gZW3K4dZB4zHgy5W8x5YW21bbtLfW1LDQcs38lthRW5lk47K5R_QlvW3ydRDW6qnnr8f94NwlW04 ) . That pace held steady across more than 20 Insilico programs between 2021 and 2024, each of which synthesized and tested around 60 to 200 molecules to find one preclinical candidate.

Behind the news: Developing a new drug typically takes 10 to 15 years and costs (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3ptW6hyN3l40M7CWW3_N-ry8Z5ZbXW8QG__z2pLrBkW81PRQp4gp_yvW3yZ27h9lfr3jW53z7Rm8gLQkqW6jlx2J7SdzV6W2-2HWM8pMtCQN58FYF26jyVnW3X3N-z2wbKB8N9fG-_n39cjDW9kV1Q65h1LWvW2LCsSW5jqZglW6T5jq12c0G-VW2Z1cvj1GPxb3W6JGBwC4w7BQ8W18HnzV7JKc9qN9m5pRzzdJZFN6jt3HMbDS6tW4VRMxR2SrQrWW7fhz014wdyb7W5D6HKM7WyFblW8LMTZb1ZVZkDN7ldjrfpzVNWW1fX45d6700s3W8G_jNs1MWYs6f3r16rK04 ) more than $2 billion, and roughly 86 percent of candidates fail to reach (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3nCW1pvFGB1RwKHqW1dPbjl4nVJWLW2VpJnD60MC9rW5bHDxS82Qgc7W4FMHq856VMxVW8gp_Jk3LMPFcW6xRj_l86Dy-fW89BF9s18k4DfW5jn52W6JJ_dqW3cTwb45N4nLSW5fVbx54z21VLW7zT0tl2S44SDW3QhSzp5hVx-bW1qgBm64D18jYN74vWvsD96fTW1MgQXX1CjZQ-W6PQ3zn4CS6_QW7s_H7h6k4m-tW5Yr2tv83CbPdW7Nc-L93hMR5GW6RhgMR5JNHxDW4tyb1b2yLl75W3fPDw85CCDD6W6nDD1v3CFJyKW1tfYyb5Hgq-TW7330BX735Txcf6r5r8R04 ) approval. A growing number of drug developers apply AI to accelerate the process. A peer-reviewed analysis catalogued (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1W3prCCW7lCdLW6lZ3lSW6sSkMq1-1ltHW4bxZBx88pV9FW66jLrH2NJ6TBW2773Xb34LHt4W627hXb4vwW06N24FDz_L9rStW1g_0QD21rBrLW2F3D1t5BC871W4qFMZ_5gFPvTW6tdFMn4KHN0dW5gLv2d8KDqfnW6-BLNS56dxbfW4740-Y1xb5SzN2VRs9wFnqYJW27NGTJ2mwQ5nW6THYgl7HdvSMW5MD7_t4Ngp6vW5ZKK1c5r-QMXVkXS0S2ntYdwW8Vw2k-8PvVfXW2JsLmp1_8BqBW7SBbnv2X5dwxW8zHyzW8vvMdTW5WdZj99kbk-2f75xG8n04 ) 173 AI-enabled drug programs across clinical stages as of mid-2025. Nonetheless, no AI-discovered drug has received regulatory approval. Of the drug candidates that reach Phase 2, 70 percent fail to reach the next phase, including AI-designed drugs from BenevolentAI (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3mTVljCW33zYkm7W3QSTTJ7s_99-VvqpCl1mJckcW1VCCml7-dgWXW2xqh-p4n14h_W7g87FS8hZcqQW8W-hy33MZVv1V_zWkW3c4g20W35jFm_39cY0hW3-xkNM7xGYTQW7DtqGm82TV8xW6Vg2jk1LCClkW96wxsP4Zwwg8W3hWGhb1qjZR9W1SV8x23kkMsmW3MxVc96j9972W17VKgt3rSYlzW8R6kvY8vRVhqW2l3j7Z76rHTpW8--KBf5bwxF6W50X7F_8nD8bhW142FYW5cLWk3W2RzRGn2wJlFWW7dJhb51F86jlW2cPKnZ3n56VyW4NBTDp2c8W0kW3MXFXW2tmsjcW7KRY2p7B6FlcW7ZF35s8y5MlZW8pVbdH87KXyRM7QqxYwHr_vMDh-bkR9pNkf8p2j1b04 ) and Recursion Pharmaceuticals (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3lMW4wRXFN6qm_5kW5wPcb-93Fc1NW4GCHyT29ZWcmN2y4GQ410x4nW5NRb754WYCJkN2NmCdpHhC-fW4XlhhN6s9Xh5W19Vk2s4ygJRrV6GtLG52VZp0N9dV5phnnzzMVm23Xl659-85W3VGTmp9dtxcZW8D0pN-6bvlwpW6z-cVv8m6MLhW6HpHY846csXgW2h8p9m6RRjBBVW0mGw3n5-HtW87B4HR7CvVxSW174WrM1zY4qYW4LbsGM5n1ck7W2XlmZp8sg6YZW4RpMhv7r9c38W5QYLv31_GMqJW21sS_k8rTChlW4qhFvs84Dz2wN7y4NRs6s-PdW6r9lW234V1zkW1q8bC51J4XrXW5FxF_H3Ggvf_W8L2cpT23lQWLW3crwlN1rQgHsW2b45fD5nw5Vrf5WFK5b04 ) .

Why it matters: Insilico’s pipeline suggests generative AI can tackle one of the hardest problems in science: finding a molecule that binds to a particular protein, is absorbed by the body, isn’t toxic, and helps patients. In Rentosertib’s Phase 2a trial, participants who took the highest dose gained an average of 98.4 milliliters in forced vital capacity (a measure of lung function), while those who took a placebo declined by 20.3 milliliters. That is early but concrete evidence that AI-generated drugs can help patients.

We’re thinking: AI is accelerating drug development, but it remains to be seen whether those accelerated compounds will pass clinical trials at a higher rate than those developed in traditional ways.

Security engineer with a rubber duck on her desk. (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv109bqx4W5BXf_W6lZ3npW7nskpW7BmrhNW3kxqCn4PjSPNN8-pd9HB_WdSW5LKdjp5gJ3MvW4w8FZR8sQDy3N6z38qpM1_VmW7sKDrg8F3lJpW2kXnMC8QtRYtW7b1TNT7WFLQ2W5QMwwf2GDwcgW158pF01-Mcr1N3H9xql1YdRLW7WNjjw7s9gjrW20Lkcv6WKy-qW7lw6Wl87HCFgW7b78vP8Znr0yW6gyc4L3yPLVKW7PJG02969LLWW6yjJGy2zkgYxW5XBcY71yGDRGW2WCS9w55jfdKW67ln3w2cNrgkW80-xrn5vLxm4W1cJX_y78GL2_Vr4bQZ1g3q-jW485qtf1-H_snN4HLf4LF-4RGN5v3RRmnknGJW3b3XCq69vSMFW1wxTQm457Zq1W2Vc6Lw4RmGKJW4qrW76860QwdW43Qp_Z8mx3tTW2ZqnRW8nlzmPN32mwxCx0Xb3Mv5TcYTtjCnW8v4_fZ1PMvGyW8PrrdM5nDvC1W3JVlp04y9HRLN8v_fMjhCsNMW4pZkvK2YRcdnW8Z_6HL4ypW3yN87TxN27pvqqV9L3ct5g7mntW8lzZhW7t48JRW90zvhp3cwYLNN2v1VPvRf5lSW2BSWTn8TZV9PW2xMF5s5hcjlyW1-HpZV354xzKW3RxFYn5mYRw0W1p4rx-6XJWKrW7BVHRm1Sn05vW4VfzxC3TdW5TW5tgQnP4BchvHVyzrLz6TGz-GW2H4LZH6SQNbZN9j5T_YpR9MnW4XyfnF4Tj-9pW7SkP576-YsH_W3yyb4T98cD46W53FNYr7qHKtxW1VRQ5c1gmDyrW66n3fH3j_JppVMPXR774FwvpW8_j6vp2HfqNBdFFn4804 )

Learn More About AI With Data Points!

AI is moving faster than ever. Data Points helps you make sense of it just as fast. Data Points arrives in your inbox twice a week with six brief news stories. This week, we covered how Nvidia is using AI to tackle key challenges in quantum computing, making systems faster, more accurate, and closer to real-world use. Subscribe today (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv109bqx4W5BXf_W6lZ3npW7nskpW7BmrhNW3kxqCn4PjSPNN8-pd9HB_WdSW5LKdjp5gJ3MvW4w8FZR8sQDy3N6z38qpM1_VmW7sKDrg8F3lJpW2kXnMC8QtRYtW7b1TNT7WFLQ2W5QMwwf2GDwcgW158pF01-Mcr1N3H9xql1YdRLW7WNjjw7s9gjrW20Lkcv6WKy-qW7lw6Wl87HCFgW7b78vP8Znr0yW6gyc4L3yPLVKW7PJG02969LLWW6yjJGy2zkgYxW5XBcY71yGDRGW2WCS9w55jfdKW67ln3w2cNrgkW80-xrn5vLxm4W1cJX_y78GL2_Vr4bQZ1g3q-jW485qtf1-H_snN4HLf4LF-4RGN5v3RRmnknGJW3b3XCq69vSMFW1wxTQm457Zq1W2Vc6Lw4RmGKJW4qrW76860QwdW43Qp_Z8mx3tTW2ZqnRW8nlzmPN32mwxCx0Xb3Mv5TcYTtjCnW8v4_fZ1PMvGyW8PrrdM5nDvC1W3JVlp04y9HRLN8v_fMjhCsNMW4pZkvK2YRcdnW8Z_6HL4ypW3yN87TxN27pvqqV9L3ct5g7mntW8lzZhW7t48JRW90zvhp3cwYLNN2v1VPvRf5lSW2BSWTn8TZV9PW2xMF5s5hcjlyW1-HpZV354xzKW3RxFYn5mYRw0W1p4rx-6XJWKrW7BVHRm1Sn05vW4VfzxC3TdW5TW5tgQnP4BchvHVyzrLz6TGz-GW2H4LZH6SQNbZN9j5T_YpR9MnW4XyfnF4Tj-9pW7SkP576-YsH_W3yyb4T98cD46W53FNYr7qHKtxW1VRQ5c1gmDyrW66n3fH3j_JppVMPXR774FwvpW8_j6vp2HfqNBdFFn4804 ) !

US States Move Forward With AI Laws

U.S. states are continuing to enact laws that regulate AI, despite President Trump’s efforts to discourage state-by-state legislation in favor of national laws.

What’s new: Many states have moved to regulate AI this year, contributing to a growing patchwork of legislation that stands to complicate developers’ efforts to meet legal requirements. Collectively, the states are considering numerous bills — more than 1,500, by one tally (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3ppW1BGWPX1n7sWZW2T_5qQ6j51g7N1zBvYGp1_2_W7ryV7-6Tkx4JW7Q59XB2DMBpjW5-B9-x6RxKSNV2D3Fz1V0181W7HSyR134FRjLW9blvHK2MHZqSW7ZRTY92-KDmTW12hd6-2W04QmW8_pB0M7xq5mqN2344s088qb3W4HtTHS8kMPm4W5g2rcx1hLj_5W2zGJwF53vKQhW1gd3cd8rYJh0W4fh_nb5nsFkjW6HZ3rJ38DLGyW7gWvxg4M8jZ7W4kBtTR11wYklW5hsY-135t9fhW6b5z6l3X5xj6W64mhrn3C_02xN126M4gsL4qfN4LpdH24Q4Qvf5vQC9l04 ) — in addition to more than 100 existing laws enacted by 40 states that are designed to discourage use of chatbots by young people, require permission to train AI systems on copyrighted material, or require security testing of AI systems, The New York Times reported (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2v3prCCW8wLKSR6lZ3phW5yZtG_5KWZSLW29fRz-8BBYGdW2ClNY57nF56XW39vW4F2kvMbJW74m8Qd7JcGXtW4sV_nD8PCmkfW2zybHh71nvkQW7XQbq_6f2SrfN5Ld1szk6lFpW1xmR-r5C4Qq6W4Fk4-B3lnx0_W5yJdtl8f5pWxW3S8GL377xNGyN385t2ng0tnmW60d5gD79TyJhW7GMTd63lJYzpW2Lh36B2Tsyb2W77T0tP7RL4pZN1xH1-QlWrcMMzPx42dFJBJW8MV_Lq4BGmW1W4DS-y38jcmgyN8xNrQC8Lf7sW1DF03D2VM0y8W61tfWj24Rt82W4vt_Sv5NGHd-W2rkLg_986l-8W7HlRTd2JJjd6f74K7NT04 ) .

How it works: California governor Gavin Newsom has been the most visible opponent of the Trump Administration’s effort to discourage state-by-state regulation of AI. But more than 40 states are in the process of passing their own laws. Among them are:

- California. Often a bellwether for regulation in the U.S. and beyond, California has established the nation’s most comprehensive AI laws. On March 30, Governor Newsom issued (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2P3prCCW95jsWP6lZ3nJMXxGbKDHP0fN2JrcpcZmzFmW2NWq5s8MFkM8W4sk_NJ4bdBfhVH4nMv49ynMgW3XDHlK8bXBHzW2lKm7h1ST_rKW75ySkD8-s1J0W6rxdsg1tpDHZW8nHkdH62KTkpN1X48PS6VMWNW71XcWq6G6yWSW4RMxvq1Vv_cRW5KKV0Z1LGpbsN59BJl6F6Sw-W532qsH8-8LXmW8w-3B1926yKsW5k9Rwt8R82Z7W6P-2Vr4tVmxNVc0wwn77986vW6K_mLy6mm6MVVGRWzf7Wnk2vW11jv0j1lpPqZW3LyvQ74shVxxVkSvr98g5_3pW8XFqfb8KSjKbN4wSSbbzyhbYW7V7gqW4KjMT9V_-CNX4Zh_xZW8P8Vjq5B2kDFf3Mnbvx04 ) an executive order requiring that AI tools used by the state protect privacy, support civil rights, and mitigate bias. Starting in August, large tech platforms and AI providers must apply (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2v3prCCW8wLKSR6lZ3mTW62DWm_5zH8f7W7L9mrk6b4XlFN2zhxmsZvBqlVvQ18H7lPM-xW6SG4nd53_NJ9W84cQ2F5XS4b4V9tmMX4llWsRW9l3_xZ3qYyVvW6r4-J92H-mmPN4sLKqyvRG5WW5xnT624h2W0xMjZ8XyNl8slW4x_gFh1njP36W89gwNZ4wwzkHV3STsp5-r2-YW30453r2m90h5W4RF1CH55WtcwW5hRR8S8SQPxhW7vnvV02T5ZwDW2yFWrb6BNdxGN3QTMtNq6k9TW86DbnG3dGR0ZV2ZfBn5XVxTlW33zjGz1wZXdKW2yVp7v4XGlcVW49N-DJ7JF8m9VMYDcV8rqv2qW5GDKTg50g3y7f8vT6w604 ) an invisible watermark to AI-generated output. These provisions add to a variety of laws that took effect in January. For instance, developers of advanced AI models must assess catastrophic risks and report serious safety incidents. LLM providers must prevent chatbots from discussing self-harm or sex with minors and remind users periodically when they are chatting with AI.
- Colorado. In 2024, Colorado passed a sweeping AI law with some of the most stringent regulations in the county. Scheduled to go into effect in July, it requires “developers and deployers of high-risk AI systems” to protect consumers from algorithmic discrimination by systems that are designed to make decisions in high-stakes fields such as education, employment, finance, healthcare, and housing. Developers must document system limitations, training data, and efforts to mitigate risks, while those who deploy models must assess their impact annually and alert consumers when AI makes a decision that affects them. However, pressure from businesses and tech companies has prompted the General Assembly to consider relaxing a requirement for annual impact assessments and other burdens.
- Minnesota. Minnesota moved early in 2023 by prohibiting deepfake election interference. Now the legislature is considering a bill that would ban use of AI to remove clothing from photos of people or set prices dynamically based on personal behavior. In August, a law will take effect that prohibits (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2b3prCCW7Y8-PT6lZ3prW5zCLYp2C_B6nW1GSXRG4xyky8W15kXXC41y7lhN3X6RHzBDXG8W4YqGG65sydZcW3Q02cC6bv5BSW6_rWYY1mm2s6N6Z4BgRTj7NRW2pKbGC7VWTNrW92pmPb4LgSnrN413hlDQ1BX4W2JHqNn7W7l72W3Pw6mf3l5x0qW1ZGwpn6p2KptW7DbB7F1v-sZ7N8G_RcWBfs-tW8QMhK41FsvlFVmm-634YC2Z1W4sKmHM6bM82RN8H7Dxkr_FV-N5bjCPS52PL8W769Q-244pdqnN3ljxHdrKqqJVp77r42vfP_RW860khN4bKtW4W8bdBpg33sX7MdT3lfl04 ) health insurance companies from using AI to deny care without a review by a relevant doctor.
- New York. This state has established some of the nation’s most stringent AI regulations, from early protections against deepfakes to broader restrictions in 2026. Starting in January 2027, model makers that have revenue of over $500 million must observe strict protocols to block users from creating bioweapons or autonomous hacking tools. They must audit these efforts annually and report incidents promptly.
- Ohio. A law that took effect in late March prohibits use of AI to replicate a person’s voice or likeness to sell a product or produce intimate images without permission. Ohio is considering a bill that would deny AI systems legal personhood and legal rights in the roles of spouse, manager, or property owner. It is also considering a ban on using AI to coordinate retail and rental prices among competitors.
- Utah. In 2026 alone, the Utah legislature passed several bills that refined the state’s 2024 Artificial Intelligence Policy Act. For instance, a bill that’s scheduled to take effect in coming months prohibits platform companies from distributing nonconsensual, sexually explicit deepfakes. Another prohibits health insurers from using AI to deny care without a doctor’s input. The state lets AI companies apply for temporary relief from certain regulations while they test new technology under regulatory supervision.

Behind the news: The Trump Administration started promoting national regulations over state laws as worries grew that a state-by-state patchwork could impede U.S. leadership in AI. In December, President Trump signed an executive order designed to discourage state-level legislation. The order targets laws that would stifle innovation as well as anti-bias regulations that could be perceived to have a political slant. It threatens to withhold federal funds from states that pass or enforce “onerous” AI laws and urges Congress to block state regulations. In March, it followed up with guidelines (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv0K5m_5PW50kH_H6lZ3l6W4k1Hb56BdX-sW3pQH-j74M6t5W4RBT5g6mSp_VN2HQtSv9X43WW2-_PtS4KvRMsW3Pks0b5Mqd7YW65gpxw3pfB_XVsvPCQ2t92F-W2DXP606tn6y6W6BCXvM5F_ZPWW77M0F24zXRGJW1sGWDJ8dGgZ3W3CM_Xz1yC83WW28dFYR2wR8GnN746xCDSxt4HW3cHNj75RQg8-W6Dtf684kkDtlN3DGB3WhHXD0W8YM3wr4fPqz1MXQM_YrJRtDW4WHvmY1Q-Pk_W8gNQgy92xhsQW5l6QXt6Mnp7HW4JsXvY8rw6MQW2JVLNn8zKb5MVwd8Yd1HVxztW8t51jD3CtJrkW2t26bf4y5C05W79rZBY4x2KLmW3Cyxn92XPLBpW7b7jP860Kj6LW2CxWHp39zjkbf9l62KH04 ) for federal legislation. The guidelines support protections for children and controls on electricity price hikes driven by AI data centers’ increasing consumption of energy.

Why it matters: An increasingly complex regulatory landscape around AI creates a potential minefield for compliance in the U.S. and contributes to unfocused, contradictory regulation worldwide. A given AI model may be required to pass a bias audit in Colorado, provide watermarking in California, and meet reporting thresholds in New York — all while the federal government moves to preempt these requirements. This jurisdictional tug-of-war increases the cost of building AI systems, adds to the legal risk of deploying new applications and services, and raises the possibility that government funding may be withheld for complying with state mandates the federal government considers onerous.

We’re thinking: Some current state-level mandates are sensible. Users should be able to rely on AI companies to preserve their privacy, for instance, and children should be protected from AI slop generated by and for adults. But such requirements should be imposed at the national level. We call on Congress to build a more cohesive, stable regulatory environment.

Simulating Diverse Human Cohorts

If you want to understand how the public will respond to your offerings, large language models can simulate users who answer questions about capabilities, features, promotions, or prices. However, LLMs don't respond with the range of variations that humans do. Researchers developed a method that prompts LLMs to take on personas with a customizable variety of attitudes.

What’s new: Davide Paglieri, Logan Cross, and colleagues at Google proposed Persona Generators (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3prCCW6N1vHY6lZ3kZW3v62Cx1c__ZpW2WBFTX5WNClVW3n780z2MB1gWW9fnpm267-XSkVYz88k57yYFGW6QN1WZ5nmhXBW3N31VN7kJkYfW1NDglN32C_fdW6MjvnK5B0lMTW6bD69B4zksznW3kB8cn41HcBqN2fSMfZYyp4jW4Y8S9M3WVkH0W7gZ2LR3DxxgSW5J48-l78s5TsN581XHsMvDTTN5fjb2k8bKYFW874L556LrgRqVDpyXJ7PjtSwW26BY5B8DzCcPW8YmnRz7J1Lm0W9g_7wz78L8rkf9j7G1q04 ) . Their approach produces code that prompts an LLM to compose prompts for 25 personas that cover the map.

Key insight: Making an LLM take on a human persona typically is a matter of composing an effective prompt (for instance, “Answer the following question as if in politics today, you considered yourself a Democrat. . . .”). However, this approach tends to elicit average responses that don’t reflect the range that a human population would provide — even if the prompt explicitly directs (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3prCCW6N1vHY6lZ3nTW6g9p4R9lxr6XW8-6LPR52q7LfW5rjMV42CGW65W5BvL-K4zs5C2W1N4q889924wcW5qNcM31cRgHNW6SS1h-5KmkpNV4jjww6whbwJW6RNbH06wQdl9VKFtS38TB5MXVwy06b8Hr_gVW5XFR-r5ndLJ2W5-zX8y19nT5DW4_P_sq30WGN4W4p8vtn12WFHwW4f3NM72Q-yZSN1LgjVJwvqZkW8tlHjz11cts_VYszg39kFTntW79M05D3qC1xMN1Zs94cYNj5sVlDRp32yvTkbf2fpN6W04 ) the LLM to adopt specific demographic characteristics. An alternative is to direct a model to modify persona prompts programmatically until they produce output that covers a specific range of opinions, attitudes, or concerns. Given guidelines that define the scope of the persona population (specifically attitudes ranked by degrees of agreement to disagreement), an evolutionary algorithm can push the model to produce a set of prompts that elicit the full range of responses.

How it works: The authors used the evolutionary method AlphaEvolve (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv105m_5PW5BWr2F6lZ3mNW64V1GK832dX3W2LZdX46SgCbZW6bcBP06dd0GsW4fyq6w3pXv9tVfh5-F54mWnfW8lLRgr29b806W3fTjHx5gLhRvVvwWD074jStzN1CP-z-_-F8yW6HKmZ919YSmRW6nzsh56WfQlGW1HQD0P2sXgVbW51L8ST92dyW0W6vxYjZ6qp3wyW1rRTf170nCVBW8kR1gH71bmm4W5nl_3B8lsWg5W6F0lNR3Gr53hW2GNsxT6zXJDSW4lz2Dj17v4j1W8wvQ7-44KL8DW6c7fhF7BC4ByW1ZPF3y3jC0_nW82L3Vj77lH-TW706gxY3BP2fKW87gH2W79wYhDW94VBqx6_t7RPVN057S6hGZj_W1HjGgX4qHbWpVqCCGd48xB21W2Wc9vH132z6PW3HpNH72Qk1nnN50JkRQy5m76W60YR-K6KQlQPf4Y9fp604 ) to generate code that (i) generated 25 prompts for personas and (ii) maximized the diversity of their attitudes based on their answers to a set of generated questionnaires.

- The authors started by using Gemini 2.5 Pro (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv2v3prCCW8wLKSR6lZ3p7W4Kc4_81LQ73mW34QlxQ9lnSZyW3CDtwJ4nkPtnVmP_Bl29P9CyW8Jmz091FPNbkW683dtB8g6hGRW7qVhPC4fWJvMW4q1l_V6F6mF_W7_MM0f7_z1tYW7QM3TP6p5qMxW5wqhm41Y0FBCW4_7TSj5rjc71W5Hmlnh2FkstPW6sVV5W8f4YcNW3HlQz97Vj343W7vHcgs24PZ20W4YqPt03wNZWPW9bp73S1KLWh6W92sK637WpmdzV750628nscJBW95wsVS3_SYDgW1W7JqJ2j5Vf1W8yMgcd5km9YXN3Z1FHjnV_LTW8LJRNb3RY8H8W31f-2k4pQbGsVWrqq75HL57vW6gPdLk7NNYMzf42d0Fn04 ) to generate 30 questionnaires on a variety of subject matter such as health care, financial literacy, and conspiracy theories. Each questionnaire included a context (description of the topic), a set of “diversity axes" (such as tolerance of risk or trust in institutions), and questions related to the axes to be answered on a scale between 1 (strongly agree) to 5 (strongly disagree).
- They created code (initially written by the authors, then updated iteratively by AlphaEvolve) to produce 25 persona prompts per questionnaire.
- To automate production of the personas’ responses, the authors used Concordia, a library for building agent-based simulations, to prompt Gemma 3-27B-IT. The LLM adopted each persona in turn and responded to the corresponding questionnaire. For each persona, they converted its answers into a vector.
- To evaluate diversity among the personas that answered each questionnaire, they computed six metrics, such as average distance between any two vectors and the degree to which the population of personas covered all possible responses.
- AlphaEvolve worked in parallel on 10 different versions of the code, iteratively updating them to maximize the diversity metrics across all the personas. After 500 iterations, the authors chose the code that maximized the average of all diversity metrics.
- At inference, given a context and a set of diversity axes, the system created 25 diverse personas.

Results: Given a fresh context and diversity axes, the resulting personas consistently exceeded the diversity metrics of Nemotron Personas (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1W3prCCW7lCdLW6lZ3kBN6DqM0ndth2hW4fhKdD6CkX2hW9fLxn37ll2NLV_c9r774H80GW8xMFjQ28r5hjVX_7c_6wNmbJN4Jpg0V84-3NW4CW3Kw8_W4fJW6Bp-T94mwYbBW7SJ2mq1cDybZW360VjT4XtvqJW8dg2qy5BCQwBV8dr3q3LpkFbW8k9vf-8zx3X3N4mlHx5Cx33lW2nKf6y3xrl9DW5pFgWR4HWszkW18P-wM4G0gD6N8KsB_T_XyqRW7Th7XR499_B1W5gNd5Y10Tb5wW61Gqcg9chs6hW94xBwn2xBvMsW1kHT9j5G6jdJf8bN60W04 ) , a large dataset of persona prompts that are based on U.S. demographic statistics, and persona prompts produced by a Concordia memory generator (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3prCCW6N1vHY6lZ3m6W4fdBb83Pw7qnW1PH4pn1vNZ45V_SC_613szY2W3jClmz8hk-2fV55RkB1g9zH5W91m3jV2ml4YMW7YHFYC5HTbntM-1JnxNWT_3W6Mp5ND6d1r73VXq31D35JMdXW33LPnd7pW27SW1sP7Wd7CD-YRW85Vlkd8TCzxDW6_J-DY6vs459W21nbmg6VwgwSW8_P2dF3c7KNZVkZcfK6vThmzW4qfDTS53lkbYN5-tDRcfDXkBW3HFNVg6Rhq4fW1dY-885h-GxNW98cH997zJnxwf5DqZ5604 ) based on generated memories from childhood to adulthood. Given a set of test questionnaires, the authors’ personas covered 82 percent of possible responses, while Nemotron Personas covered 76 percent and Concordia memory generator covered 46 percent.

Why it matters: Organizations that aim to expand their audiences can benefit from synthetic personas that broadly reflect public sentiment, and those that create synthetic personas to match their real-world audiences can gain insights from a more diverse crowd. This work shifts the objective from matching training data (which tends to generate the most probable outputs and not the outliers) to covering all desired possibilities. Optimizing the persona generator, rather than individual personas, unlocks a broader representation of likely user behavior.

We’re thinking: Synthetic personas offer an intriguing possibility for navigating the product-management bottleneck, the difficulty of deciding what to build when you can build easily by prompting an LLM.

Work With Andrew Ng

Join the teams that are bringing AI to the world! Check out job openings at DeepLearning.AI (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3prCCW6N1vHY6lZ3mrW84Q0x09kqnHwW4fsj__5Mnwp1W8M7h6645cPjLW2HTXr_6LzgNzW2ygyNZ2PCgjTW42tqJ63PTwFrW3mNy5N3tBYy2W8_Cdht6HWCnSVmD3Xf4FJkTvW5jxWlm8-PpydW62yPSy4t1cg-W4V-ddk83rnDNW2rvXpj7gDpGZW9jYqZd5FMnznW4tCM-p7M43fdW1bnYXL7rmBqgW8DhGG23Cdz7MW79T2cQ3rb2QPW7GmVr-7rvrn_W1tLP2Z2mWcZsW5pCdBQ8bvmzLW4CPcJB8DYZnWf7nqXrl04 ) , AI Fund (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1j3prCCW69sMD-6lZ3pLN7_gSgXXwwWxW3GF9JJ7_XBSbW3MKNqf5kB33JW2sn3GF73QHPpW7ZpSXx6lJcl3W1Dk3f771GkZ2W6Dg2lD5GXmqMW3vfjst7TjG7TW53QTQ177l396W4XzPKM3h7GzDW1vV0rH1z6LwVW2rpw7T8dm3R3W2ZnWps8dqHFZVSL0RC3MFmJLVsZBwx4CPbd5Vk6KJj6VwYgMW14ZZQ85y29MWW8VdpzZ45HX9WW1rKhYD7BgGdZW8ryXy77XN3zTf7Dqcg004 ) , and Landing AI (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1j3prCCW69sMD-6lZ3mZW743L1t5fP0DbW8P9crz9kF-6HN1Cjx76bPhzyW2rw4BK47gW9NW6F-lSz5XsbbPF7hYrdhnmZVVTpsp64qklX4VBRxpy6MXXNKW830gcq6TSZY5W39VJ1Y77QppCVGKGP45NfS1PW1swCYV8pRy6NW1rhsmV8wdc4mW8PZl154w-M5nW2plm1N1K0cvbM8Z6wtWDPgVW9cC7r885Wtl5W61W9p57pCMdbN4mrl8_fX7YSW91QDBy6rh-4Mf7KDlP404 ) .

Subscribe and view previous issues here (https://info.deeplearning.ai/e3t/Ctc/LX+113/cJhC404/VVFzpt1b632CW4fYNbx4Z_-XmW37H_hZ5M_2HMN2qdv1C3l5QzW6N1vHY6lZ3kCVLXPjv22Psw6W5nx_6K36LCtrW2rB9w51550r7W49ZwrN5fChSbW4t1M_m7K2bqlW2RDMYy6NvWDCW8z-DS45VydRnW3Vhyqw5Ys1R6W3_fyyQ7QgbTLW4QfQpS3Jh6PDW2fdR2Z4cknZHW6skp1w6fQ6cvVvt70j7_Y9xXW61DmzY7pQnTDW8qTdYr2J0nbNW57gT557btXxmW3xlF1_5s_t3pN3B2qyvy0tyZN1TgDNSRZkphN4xBg4q1Dtq8W5JnXbh5ns9ZRW3gy6771XyPvGf3NCZtz04 ) .

Thoughts, suggestions, feedback? Please send to thebatch@deeplearning.ai (mailto:thebatch@deeplearning.ai?subject=RE:%20Thoughts,%20Suggestions,%20feedback) . Avoid our newsletter ending up in your spam folder by adding our email address to your contacts list.

DeepLearning.AI, 400 Castro St., Suite 600, Mountain View, CA 94041, United States

Unsubscribe (https://info.deeplearning.ai/hs/preferences-center/en/direct?data=W2nXS-N30h-H5W3VNndb3NXQQWW34qhst2nZ32cW4pzD813jcpgBW4krWkK34mgvhW4mF2Yz2YJzQFW32HZqL43KjlpW2zMyGY2Fr3skW4p8vNc2xRXdLW4mJTc14mGnSMW1BL2GQ3BWrkNW3bcCVZ2RknFSW43GP0q1X6ntyW2qMZMT4kK1CMW4fgmW71Lmz9VW4fMYqY3BTPzyW1-Zg2L47ktJXW3g5R893j9kZ3W1LvcwW3g1HyBW2szLly2RrhNVW1Lwrgw43p_lJW2q_ntP2CJR01W3S-j_H2WNzCCW4rFYSw4prrzPW38xsMC3zcby_W3bzppF2-H-S2W3Cjr801SgK4LW1_rw4J3VwyXTW30LjyQ1Q3ktBW2xwVgB43y3w9W3f-Jxc1Zd2ZMW3JZ4zJ49wct7W23gpww49HRdRW1S113d30cR-4W3J-Qc634fyF_W1VtNY030psqNW4hvbzJ21213JW2vyHyK215PB9W1VqR414kH02xW3S_Ddw2KC98pW2KY44T4rnlBsW2sVldR4pp1hmW21stXM23pnBcW1Qjdmn3LV775W2sZ6l51BnPwNW4cy27W23h7yCW23r4f03QW1jYW3_w2MB4cJmG0W327rRR2523llW3yWySg2MSN8LW1S0_zh3VG4LQW2FC9D72nMGwSW3d8yLZ4mpd0vW2PnBlP1QgvfyW1NjlT64tt5CZW2TPzms2FMX3lW2HB_HB4r5QFrf4fnxDL04&_hsenc=p2ANqtz-_hwenuQ6RPvYK_ATy0yvCMwEX3syWKzPu_-wplCslfwKvIJm2nZOLfufp4FW0a_MN22hRAPZBbUR99YlzmZWjfgIeZHw&_hsmi=414231931 )
Manage preferences (https://info.deeplearning.ai/hs/preferences-center/en/page?data=W2nXS-N30h-H5W3VNndb3NXQQWW34qhst2nZ32cW4pzD813jcpgBW4krWkK34mgvhW4mF2Yz2YJzQFW32HZqL43KjlpW2zMyGY2Fr3skW4p8vNc2xRXdLW4mJTc14mGnSMW1BL2GQ3BWrkNW3bcCVZ2RknFSW43GP0q1X6ntyW2qMZMT4kK1CMW4fgmW71Lmz9VW4fMYqY3BTPzyW1-Zg2L47ktJXW3g5R893j9kZ3W1LvcwW3g1HyBW2szLly2RrhNVW1Lwrgw43p_lJW2q_ntP2CJR01W3S-j_H2WNzCCW4rFYSw4prrzPW38xsMC3zcby_W3bzppF2-H-S2W3Cjr801SgK4LW1_rw4J3VwyXTW30LjyQ1Q3ktBW2xwVgB43y3w9W3f-Jxc1Zd2ZMW3JZ4zJ49wct7W23gpww49HRdRW1S113d30cR-4W3J-Qc634fyF_W1VtNY030psqNW4hvbzJ21213JW2vyHyK215PB9W1VqR414kH02xW3S_Ddw2KC98pW2KY44T4rnlBsW2sVldR4pp1hmW21stXM23pnBcW1Qjdmn3LV775W2sZ6l51BnPwNW4cy27W23h7yCW23r4f03QW1jYW3_w2MB4cJmG0W327rRR2523llW3yWySg2MSN8LW1S0_zh3VG4LQW2FC9D72nMGwSW3d8yLZ4mpd0vW2PnBlP1QgvfyW1NjlT64tt5CZW2TPzms2FMX3lW2HB_HB4r5QFrf4fnxDL04&_hsenc=p2ANqtz-_hwenuQ6RPvYK_ATy0yvCMwEX3syWKzPu_-wplCslfwKvIJm2nZOLfufp4FW0a_MN22hRAPZBbUR99YlzmZWjfgIeZHw&_hsmi=414231931 )

---
