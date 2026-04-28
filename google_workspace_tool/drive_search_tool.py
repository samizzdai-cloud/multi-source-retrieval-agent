"""
Google Drive API v3 搜索封装工具。

提供薄封装层，用户直接传入 Google 原生 q 查询字符串，
工具负责分页、错误处理和结构化输出。

认证说明：
本模块不自行处理认证，必须由调用方传入已授权的 Credentials 对象。
请在外部统一完成 OAuth2 授权流程后，将 creds 传入本模块的各函数。

Drive `files().list()` q 参数支持的关键字段：
- name (=, !=, contains)          文件名
- mimeType (=, !=, contains)      MIME 类型
- modifiedTime / createdTime      修改/创建时间（支持 < > <= >=）
- viewedByMeTime                  上次查看时间
- trashed / starred / hidden      布尔状态
- parents (in)                    父文件夹
- owners / writers / readers (in) 权限用户
- sharedWithMe                    是否与我共享
- fullText (contains)             全文内容搜索（Google 原生格式有效）
- properties / appProperties (has) 自定义属性
- shortcutDetails.targetId / .targetMimeType  快捷方式

组合操作符：and, or, not，支持括号分组。

常用 MIME 类型：
- application/vnd.google-apps.folder      文件夹
- application/vnd.google-apps.document    Google Docs
- application/vnd.google-apps.spreadsheet Google Sheets
- application/vnd.google-apps.presentation Google Slides
- application/pdf                         PDF
"""

from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 默认返回字段：覆盖常用元数据
DEFAULT_FIELDS = (
    "nextPageToken, files("
    "id, name, mimeType, description, createdTime, modifiedTime, viewedByMeTime, "
    "starred, trashed, parents, "
    "owners(displayName, emailAddress), lastModifyingUser(displayName, emailAddress), "
    "webViewLink, webContentLink, iconLink, thumbnailLink, "
    "size, md5Checksum, shared, permissions(id, type, role, emailAddress), "
    "driveId, teamDriveId, shortcutDetails, contentHints"
    ")"
)


def search_drive(
    q: Optional[str] = None,
    fields: Optional[str] = None,
    spaces: str = "drive",
    page_size: int = 100,
    order_by: Optional[str] = None,
    corpora: Optional[str] = None,
    include_items_from_all_drives: bool = False,
    supports_all_drives: bool = False,
    max_pages: Optional[int] = None,
    creds=None,
) -> list[dict]:
    """
    搜索 Google Drive 文件，返回文件元数据列表。

    Args:
        q: Drive q 查询字符串，例如：
           "mimeType='application/vnd.google-apps.document' and name contains '预算'"
           "fullText contains 'Q3 营收' and modifiedTime > '2024-01-01T00:00:00'"
           传 None 时返回所有可访问文件（按 page_size 分页）。
        fields: 返回字段，默认覆盖常用元数据。详见 DEFAULT_FIELDS。
                如需精简："nextPageToken, files(id, name, mimeType, modifiedTime)"
        spaces: 搜索空间，"drive"（默认）或 "appDataFolder"
        page_size: 每页大小，默认 100，最大 1000
        order_by: 排序规则，例如 "modifiedTime desc", "name", "viewedByMeTime desc"
        corpora: "user" | "domain" | "drive" | "allDrives"
        include_items_from_all_drives: 是否在结果中包含所有共享驱动器的项目
        supports_all_drives: 是否支持共享驱动器中的文件操作
        max_pages: 最大拉取页数，None 表示拉取全部
        creds: 外部传入的已授权 Credentials 对象，必填。
               请在外部统一完成 OAuth2 授权后传入。

    Returns:
        文件列表，每个元素为 dict（Drive File 资源）。
        扁平化返回，所有页的结果合并为一个列表。

    Raises:
        ValueError: 当未传入 creds 时
        HttpError: 当 Google API 返回错误时（如 400 查询语法错误、403 权限不足）
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    fields = fields or DEFAULT_FIELDS

    files = []
    page_token = None
    page_count = 0

    try:
        while True:
            params = {
                "spaces": spaces,
                "fields": fields,
                "pageSize": min(page_size, 1000),
                "pageToken": page_token,
            }
            if q is not None:
                params["q"] = q
            if order_by is not None:
                params["orderBy"] = order_by
            if corpora is not None:
                params["corpora"] = corpora
            if include_items_from_all_drives:
                params["includeItemsFromAllDrives"] = True
            if supports_all_drives:
                params["supportsAllDrives"] = True

            response = service.files().list(**params).execute()

            batch = response.get("files", [])
            files.extend(batch)

            page_token = response.get("nextPageToken")
            page_count += 1

            if max_pages is not None and page_count >= max_pages:
                break
            if page_token is None:
                break

    except HttpError as e:
        # 增强错误信息，附加查询条件方便调试
        raise HttpError(
            resp=e.resp,
            content=e.content,
            uri=e.uri,
        ) from e

    return files


if __name__ == "__main__":
    # 本地快速测试：列出最近的 10 个文件
    result = search_drive(
        q=None,
        fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
        page_size=10,
    )
    print(f"找到 {len(result)} 个文件")
    for f in result:
        print(f"  {f.get('name')} ({f.get('mimeType')}) — {f.get('modifiedTime')}")
