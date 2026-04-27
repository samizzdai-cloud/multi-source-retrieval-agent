"""
Google Drive 文件夹并发递归遍历工具。

从指定母文件夹开始，使用 BFS + ThreadPoolExecutor 并发遍历所有子文件夹和文件，
收集完整元数据和路径信息（path_names + path_ids）。

核心设计：
- 按层并发：同一深度的所有文件夹并行查询
- 路径预携带：每个查询任务自带 parent_path，结果直接拼接
- 循环引用防护：threading.Lock 保护全局 visited_ids

认证说明：
本模块不自行处理认证，必须由调用方传入已授权的 Credentials 对象。
"""

import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Callable
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 文件夹 MIME 类型
_MIME_FOLDER = "application/vnd.google-apps.folder"

# 默认返回字段（注意：Drive v3 不支持 'hidden' 字段）
DEFAULT_FIELDS = (
    "nextPageToken, files("
    "id, name, mimeType, description, createdTime, modifiedTime, viewedByMeTime, "
    "starred, trashed, parents, "
    "owners(displayName, emailAddress), lastModifyingUser(displayName, emailAddress), "
    "webViewLink, webContentLink, iconLink, thumbnailLink, "
    "size, md5Checksum, shared, permissions(id, type, role, emailAddress), "
    "driveId, teamDriveId, shortcutDetails"
    ")"
)

# 线程本地存储：每个线程拥有独立的 service 实例
_thread_local = threading.local()


def _get_thread_service(creds):
    """获取当前线程的 Drive service 实例（线程安全）。"""
    if not hasattr(_thread_local, "service"):
        _thread_local.service = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _thread_local.service


def _list_children(
    creds,
    folder_id: str,
    path_names: list,
    path_ids: list,
    depth: int,
    include_trashed: bool,
    fields: str,
) -> dict:
    """
    查询单个文件夹下的所有子项（内部 worker，支持分页，线程安全）。

    Returns:
        {
            "folder_id": "...",
            "path_names": [...],
            "path_ids": [...],
            "depth": int,
            "children": [file_obj, ...],
            "error": None | str,
        }
    """
    service = _get_thread_service(creds)
    all_children = []
    page_token = None

    while True:
        q = f"'{folder_id}' in parents"
        if not include_trashed:
            q += " and trashed = false"

        try:
            response = (
                service.files()
                .list(
                    q=q,
                    spaces="drive",
                    fields=fields,
                    pageSize=1000,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except HttpError as e:
            return {
                "folder_id": folder_id,
                "path_names": path_names,
                "path_ids": path_ids,
                "depth": depth,
                "children": [],
                "error": str(e),
            }

        all_children.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if page_token is None:
            break

    return {
        "folder_id": folder_id,
        "path_names": path_names,
        "path_ids": path_ids,
        "depth": depth,
        "children": all_children,
        "error": None,
    }


def traverse_folder(
    folder_id: str,
    creds=None,
    fields: Optional[str] = None,
    include_trashed: bool = False,
    max_depth: Optional[int] = None,
    max_workers: int = 8,
    callback: Optional[Callable[[dict, int], bool]] = None,
) -> list[dict]:
    """
    从指定文件夹开始并发递归遍历所有子文件夹和文件。

    使用 BFS 按层并发：同一深度的所有文件夹用 ThreadPoolExecutor 并行查询。

    Args:
        folder_id: 母文件夹 ID，如 "root" 表示我的云端硬盘根目录
        creds: 外部传入的已授权 Credentials 对象，必填
        fields: 文件元数据返回字段，默认覆盖常用字段
        include_trashed: 是否包含回收站中的文件
        max_depth: 最大递归深度，None 表示无限制
        max_workers: 并发线程数，默认 8
        callback: 可选回调函数，签名 fn(item: dict, depth: int) -> bool
                  返回 False 则跳过该文件夹分支的继续遍历

    Returns:
        扁平化列表，每个元素：
        {
            "id": "...",
            "name": "...",
            "mimeType": "...",
            "path_names": ["母文件夹", "子文件夹A", "当前文件"],
            "path_ids": ["folder_id_0", "folder_id_1", "file_id"],
            "depth": 2,
            "is_folder": false,
            "shortcut_skipped": false,
            "meta": {...},
        }

    Raises:
        ValueError: 当未传入 creds 时
        HttpError: 当 Google API 返回错误时
    """
    if creds is None:
        raise ValueError("creds 为必填参数。请在外部完成 OAuth2 授权后传入 Credentials 对象。")

    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    fields = fields or DEFAULT_FIELDS

    # 获取根文件夹名称
    root_name = folder_id
    try:
        root_meta = service.files().get(fileId=folder_id, fields="name").execute()
        root_name = root_meta.get("name", folder_id)
    except HttpError:
        pass

    results = []
    visited_ids = set()
    visited_lock = threading.Lock()

    # BFS 队列元素: (folder_id, path_names, path_ids, depth)
    queue = deque()
    queue.append((folder_id, [root_name], [folder_id], 0))

    round_num = 0
    while queue:
        round_num += 1

        # ---- 取出当前轮次的所有文件夹 ----
        current_batch = []
        while queue:
            item = queue.popleft()
            cid, cpn, cpid, cd = item

            # 深度限制：在取出时就检查
            if max_depth is not None and cd >= max_depth:
                continue

            # 防循环：立即标记 visited，避免多个线程重复查询同一文件夹
            with visited_lock:
                if cid in visited_ids:
                    continue
                visited_ids.add(cid)

            current_batch.append(item)

        if not current_batch:
            break

        # ---- 并发查询当前批次的所有文件夹 ----
        next_folders = []  # 收集下一轮要加入队列的文件夹

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _list_children, creds, cid, cpn, cpid, cd,
                    include_trashed, fields
                ): (cid, cpn, cpid, cd)
                for cid, cpn, cpid, cd in current_batch
            }

            for future in as_completed(futures):
                result = future.result()

                # 查询失败，记录错误项后继续
                if result["error"] is not None:
                    results.append({
                        "id": result["folder_id"],
                        "name": result["path_names"][-1] if result["path_names"] else result["folder_id"],
                        "mimeType": _MIME_FOLDER,
                        "path_names": list(result["path_names"]),
                        "path_ids": list(result["path_ids"]),
                        "depth": result["depth"],
                        "is_folder": True,
                        "shortcut_skipped": False,
                        "meta": {"error": result["error"]},
                    })
                    continue

                parent_path_names = result["path_names"]
                parent_path_ids = result["path_ids"]
                depth = result["depth"]

                for child in result["children"]:
                    child_id = child.get("id")
                    child_name = child.get("name", "")
                    child_mime = child.get("mimeType", "")
                    is_folder = child_mime == _MIME_FOLDER

                    # 构建当前项的完整路径
                    child_path_names = list(parent_path_names) + [child_name]
                    child_path_ids = list(parent_path_ids) + [child_id]

                    item = {
                        "id": child_id,
                        "name": child_name,
                        "mimeType": child_mime,
                        "path_names": child_path_names,
                        "path_ids": child_path_ids,
                        "depth": depth + 1,
                        "is_folder": is_folder,
                        "shortcut_skipped": False,
                        "meta": child,
                    }

                    # 快捷方式循环检测
                    shortcut = child.get("shortcutDetails", {})
                    target_id = shortcut.get("targetId")
                    if target_id and target_id in visited_ids:
                        item["shortcut_skipped"] = True

                    results.append(item)

                    # 如果是文件夹且未被跳过，评估是否继续遍历
                    if is_folder and not item["shortcut_skipped"]:
                        should_traverse = True
                        if callback is not None:
                            should_traverse = callback(item, depth + 1)

                        if should_traverse:
                            # 深度限制已在取出时检查，这里只需判断是否超限
                            if max_depth is None or (depth + 1) < max_depth:
                                next_folders.append(
                                    (child_id, child_path_names, child_path_ids, depth + 1)
                                )

        # ---- 将新发现的文件夹加入队列，进入下一轮 ----
        for folder_item in next_folders:
            queue.append(folder_item)

    return results


if __name__ == "__main__":
    from auth_utils import get_credentials

    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = get_credentials(SCOPES)

    # 遍历 My Drive 根目录，限制深度 2
    items = traverse_folder(folder_id="root", creds=creds, max_depth=2)

    print(f"共发现 {len(items)} 个文件/文件夹\n")
    for item in items:
        indent = "  " * item["depth"]
        icon = "📁" if item["is_folder"] else "📄"
        path = "/".join(item["path_names"])
        print(f"{indent}{icon} {path}")
