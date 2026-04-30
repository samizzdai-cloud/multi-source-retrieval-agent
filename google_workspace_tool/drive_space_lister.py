"""
Google Drive 云盘空间列举工具。

列举用户当前可访问的所有顶级空间，包括：
- 我的云端硬盘（My Drive）
- 共享驱动器（Shared Drives / Team Drives）
- 与我共享的文件夹（Shared with Me 中的文件夹）

认证说明：
本模块不自行处理认证，必须由调用方传入已授权的 Credentials 对象。
"""

import asyncio
import time
import concurrent.futures
from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 默认返回字段
DEFAULT_FOLDER_FIELDS = (
    "nextPageToken, files(id, name, mimeType, description, createdTime, modifiedTime, "
    "owners(displayName, emailAddress), shared, driveId, teamDriveId)"
)


async def list_drive_spaces_async(
    creds=None,
    include_shared_drives: bool = True,
    include_shared_folders: bool = True,
    include_my_drive_folders: bool = True,
    fields: Optional[str] = None,
) -> list[dict]:
    """
    列举用户可访问的所有顶级云盘空间（异步并发版本）。

    包含：
    1. 我的云端硬盘（根目录 + 一级文件夹）
    2. 共享驱动器
    3. 与我共享的文件夹
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    spaces = []
    fields = fields or DEFAULT_FOLDER_FIELDS

    # 固定包含 My Drive 根目录
    spaces.append({
        "space_type": "my_drive",
        "id": "root",
        "name": "My Drive",
        "meta": None,
    })

    async def _fetch_my_drive_folders():
        results = []
        if not include_my_drive_folders:
            return results
        try:
            page_token = None
            while True:
                response = await asyncio.to_thread(
                    service.files().list(
                        q="'root' in parents and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                        spaces="drive",
                        fields=fields,
                        pageSize=100,
                        pageToken=page_token,
                    ).execute
                )
                for folder in response.get("files", []):
                    results.append({
                        "space_type": "my_drive_folder",
                        "id": folder.get("id"),
                        "name": folder.get("name"),
                        "meta": folder,
                    })
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
        except HttpError:
            pass
        return results

    async def _fetch_shared_drives():
        results = []
        if not include_shared_drives:
            return results
        try:
            page_token = None
            while True:
                response = await asyncio.to_thread(
                    service.drives().list(
                        pageSize=100, 
                        pageToken=page_token
                    ).execute
                )
                for drive in response.get("drives", []):
                    results.append({
                        "space_type": "shared_drive",
                        "id": drive.get("id"),
                        "name": drive.get("name"),
                        "meta": drive,
                    })
                page_token = response.get("nextPageToken")
                if page_token is None:
                    break
        except HttpError:
            pass
        return results

    async def _fetch_shared_folders():
        results = []
        if not include_shared_folders:
            return results
        try:
            page_token = None
            while True:
                response = await asyncio.to_thread(
                    service.files().list(
                        q="sharedWithMe = true and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                        spaces="drive",
                        fields=fields,
                        pageSize=1000,
                        pageToken=page_token,
                    ).execute
                )
                for folder in response.get("files", []):
                    results.append({
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
        return results

    # 并发执行所有查询
    my_drive_folders, shared_drives, shared_folders = await asyncio.gather(
        _fetch_my_drive_folders(),
        _fetch_shared_drives(),
        _fetch_shared_folders()
    )

    spaces.extend(my_drive_folders)
    spaces.extend(shared_drives)
    spaces.extend(shared_folders)

    return spaces


def list_drive_spaces(
    creds=None,
    include_shared_drives: bool = True,
    include_shared_folders: bool = True,
    include_my_drive_folders: bool = True,
    fields: Optional[str] = None,
) -> list[dict]:
    """同步包装器，兼容旧代码。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    asyncio.run,
                    list_drive_spaces_async(
                        creds, include_shared_drives, include_shared_folders, include_my_drive_folders, fields
                    )
                )
                return future.result()
        else:
            return loop.run_until_complete(
                list_drive_spaces_async(
                    creds, include_shared_drives, include_shared_folders, include_my_drive_folders, fields
                )
            )
    except RuntimeError:
        return asyncio.run(
            list_drive_spaces_async(
                creds, include_shared_drives, include_shared_folders, include_my_drive_folders, fields
            )
        )


if __name__ == "__main__":
    from auth_utils import get_credentials

    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = get_credentials(SCOPES)

    spaces = list_drive_spaces(creds=creds)
    print(f"发现 {len(spaces)} 个云盘空间：\n")
    for s in spaces:
        print(f"  [{s['space_type']}] {s['name']} ({s['id']})")
