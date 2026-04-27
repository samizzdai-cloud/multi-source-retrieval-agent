"""
Google Drive 云盘空间列举工具。

列举用户当前可访问的所有顶级空间，包括：
- 我的云端硬盘（My Drive）
- 共享驱动器（Shared Drives / Team Drives）
- 与我共享的文件夹（Shared with Me 中的文件夹）

认证说明：
本模块不自行处理认证，必须由调用方传入已授权的 Credentials 对象。
"""

from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 默认返回字段
DEFAULT_FOLDER_FIELDS = (
    "nextPageToken, files(id, name, mimeType, description, createdTime, modifiedTime, "
    "owners(displayName, emailAddress), shared, driveId, teamDriveId)"
)


def list_drive_spaces(
    creds=None,
    include_shared_drives: bool = True,
    include_shared_folders: bool = True,
    fields: Optional[str] = None,
) -> list[dict]:
    """
    列举用户可访问的所有顶级云盘空间。

    Args:
        creds: 外部传入的已授权 Credentials 对象，必填。
        include_shared_drives: 是否包含共享驱动器（Team Drives）
        include_shared_folders: 是否包含"与我共享"的文件夹
        fields: 共享文件夹的返回字段，默认覆盖常用元数据

    Returns:
        空间列表，每个元素：
        {
            "space_type": "my_drive" | "shared_drive" | "shared_folder",
            "id": "...",
            "name": "...",
            "meta": {...},   # 原始元数据（shared_drive 和 shared_folder 有）
        }

    Raises:
        ValueError: 当未传入 creds 时
        HttpError: 当 Google API 返回错误时
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    spaces = []
    fields = fields or DEFAULT_FOLDER_FIELDS

    # 1. 我的云端硬盘（固定 root）
    spaces.append({
        "space_type": "my_drive",
        "id": "root",
        "name": "My Drive",
        "meta": None,
    })

    # 2. 共享驱动器（Shared Drives）
    if include_shared_drives:
        try:
            page_token = None
            while True:
                response = (
                    service.drives()
                    .list(pageSize=100, pageToken=page_token)
                    .execute()
                )
                for drive in response.get("drives", []):
                    spaces.append({
                        "space_type": "shared_drive",
                        "id": drive.get("id"),
                        "name": drive.get("name"),
                        "meta": drive,
                    })
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
        except HttpError:
            # 无权限访问共享驱动器时静默跳过
            pass

    # 3. 与我共享的文件夹
    if include_shared_folders:
        try:
            page_token = None
            while True:
                response = (
                    service.files()
                    .list(
                        q="sharedWithMe = true and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                        spaces="drive",
                        fields=fields,
                        pageSize=1000,
                        pageToken=page_token,
                    )
                    .execute()
                )
                for folder in response.get("files", []):
                    spaces.append({
                        "space_type": "shared_folder",
                        "id": folder.get("id"),
                        "name": folder.get("name"),
                        "meta": folder,
                    })
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
        except HttpError:
            pass

    return spaces


if __name__ == "__main__":
    from auth_utils import get_credentials

    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = get_credentials(SCOPES)

    spaces = list_drive_spaces(creds=creds)
    print(f"发现 {len(spaces)} 个云盘空间：\n")
    for s in spaces:
        print(f"  [{s['space_type']}] {s['name']} ({s['id']})")
