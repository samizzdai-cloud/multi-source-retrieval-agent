"""Google Drive 文件元数据获取工具。

支持通过 URL 或 file_id 获取文件的完整元数据，包括文件夹路径、时间信息等。
"""

from typing import Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .url_parser import resolve_file_id


def get_file_metadata(
    url: Optional[str] = None,
    file_id: Optional[str] = None,
    creds=None,
    drive_service=None,
    supports_all_drives: bool = True,
) -> dict:
    """获取 Drive 文件的完整元数据，包括文件夹路径、时间信息等。

    Args:
        url: Google Drive URL（与 file_id 二选一）
        file_id: Drive 文件 ID（与 url 二选一）
        creds: 已授权的 Credentials 对象（drive_service 未传时必填）
        drive_service: 已构建的 drive(v3) service 对象（可选）
        supports_all_drives: 是否支持共享驱动器。默认 True。

    Returns:
        {
            "file_id": str,
            "name": str,
            "mime_type": str,
            "size": int | None,
            "created_time": str | None,       # ISO 8601
            "modified_time": str | None,      # ISO 8601
            "viewed_by_me_time": str | None,  # ISO 8601
            "folder_path": str,               # 完整文件夹路径，如 "Drive/Project/Doc"
            "parent_id": str | None,
            "owners": list[str],
            "last_modifying_user": str | None,
            "web_view_link": str | None,
            "error": str | None,
        }
    """
    # 解析 file_id
    try:
        resolved_id = resolve_file_id(url=url, file_id=file_id)
    except ValueError as e:
        return {"file_id": file_id or "", "error": str(e)}

    if drive_service is None:
        if creds is None:
            return {"file_id": resolved_id, "error": "creds 或 drive_service 至少传一个"}
        drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        meta = drive_service.files().get(
            fileId=resolved_id,
            fields=(
                "id,name,mimeType,size,createdTime,modifiedTime,"
                "viewedByMeTime,parents,owners(displayName),"
                "lastModifyingUser(displayName),webViewLink"
            ),
            supportsAllDrives=supports_all_drives,
        ).execute()
    except HttpError as e:
        return {"file_id": resolved_id, "error": str(e)}

    # 递归获取文件夹路径
    path_parts = []
    current_id = resolved_id
    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        try:
            m = drive_service.files().get(
                fileId=current_id,
                fields="name,parents,mimeType",
                supportsAllDrives=supports_all_drives,
            ).execute()
            name = m.get("name", "")
            mime = m.get("mimeType", "")
            parents = m.get("parents", [])
            path_parts.insert(0, name)
            if not parents:
                break
            current_id = parents[0]
        except HttpError:
            break

    folder_path = "/".join(path_parts) if len(path_parts) > 1 else ""
    parent_id = meta.get("parents", [None])[0]

    return {
        "file_id": meta.get("id", resolved_id),
        "name": meta.get("name", ""),
        "mime_type": meta.get("mimeType", ""),
        "size": int(meta["size"]) if meta.get("size") else None,
        "created_time": meta.get("createdTime"),
        "modified_time": meta.get("modifiedTime"),
        "viewed_by_me_time": meta.get("viewedByMeTime"),
        "folder_path": folder_path,
        "parent_id": parent_id,
        "owners": [o.get("displayName", "") for o in meta.get("owners", [])],
        "last_modifying_user": meta.get("lastModifyingUser", {}).get("displayName"),
        "web_view_link": meta.get("webViewLink"),
        "error": None,
    }
