"""
folder_scanner.py — 飞书文件夹扫描器

递归遍历飞书云文档文件夹，将文件清单序列化为 Doc_task_list.json。
依赖 FeishuClient，输出 FileRecord 列表。
"""

import logging
import os
from typing import Dict, List, TypedDict

from .client import FeishuClient
from .basic_tool.utils import sanitize_filename, FileError

log = logging.getLogger(__name__)

ALLOWED_TYPES = {"folder", "docx", "doc", "file", "sheet", "bitable", "slides"}


class FileRecord(TypedDict):
    document_token: str    # 文件唯一标识（shortcut 已解引用）
    file_name: str         # 原始文件名
    file_type: str         # 文件类型（docx / doc / sheet / bitable / file / slides / folder）
    file_path: str         # 逻辑路径，如 Feishu/根文件夹/子文件夹/文件名
    file_outputpath: str   # 本地输出路径，如 /output/文件名.md
    edited_time: int       # 文件最后编辑时间（Unix 时间戳，来自飞书 API）


class FolderScanner:
    def __init__(self, client: FeishuClient, output_dir: str):
        """初始化扫描器

        Args:
            client: 飞书 API 客户端
            output_dir: 本地输出目录
        """
        self.client = client
        self.output_dir = output_dir

    def _normalize_entry(self, entry: Dict) -> FileRecord | None:
        """将原始 API 条目规范化为 FileRecord。

        处理 shortcut 解引用和 ALLOWED_TYPES 过滤。

        Args:
            entry: 原始 API 文件条目字典

        Returns:
            FileRecord 或 None（类型不合法 / shortcut 缺字段时）
        """
        if entry.get("type") == "shortcut":
            shortcut_info = entry.get("shortcut_info")
            if not shortcut_info:
                log.warning(
                    "Shortcut entry '%s' is missing shortcut_info, skipping.",
                    entry.get("name", "<unknown>"),
                )
                return None
            document_token = shortcut_info["target_token"]
            file_type = shortcut_info["target_type"]
        else:
            document_token = entry["token"]
            file_type = entry["type"]

        file_name = entry["name"]

        if file_type not in ALLOWED_TYPES:
            return None

        return FileRecord(
            document_token=document_token,
            file_name=file_name,
            file_type=file_type,
            file_path="",
            file_outputpath="",
            edited_time=entry.get("edited_time"),
        )

    def _build_paths(self, file_name: str, path_prefix: List[str]) -> tuple[str, str] | None:
        """构造 file_path 和 file_outputpath。

        Args:
            file_name: 文件名
            path_prefix: 从根文件夹到当前层级的路径段列表

        Returns:
            (file_path, file_outputpath) 元组，sanitize_filename 失败时返回 None
        """
        try:
            safe_name = sanitize_filename(file_name)
        except FileError:
            log.warning(
                "Cannot sanitize filename '%s', skipping entry.",
                file_name,
            )
            return None

        file_path = "Feishu/" + "/".join(path_prefix + [file_name])
        file_outputpath = os.path.join(self.output_dir, safe_name + ".md")
        return file_path, file_outputpath


    def _scan_folder(self, folder_token: str, path_prefix: List[str]) -> List[Dict]:
        """递归扫描单个文件夹，返回该子树下所有 FileRecord。

        Args:
            folder_token: 当前文件夹的 token
            path_prefix: 从根到当前层级的路径段列表

        Returns:
            该子树下所有非文件夹 FileRecord 的列表

        Raises:
            APIError: 由上层 scan 方法捕获
        """
        entries = self.client.list_folder_files(folder_token)
        results = []

        for entry in entries:
            record = self._normalize_entry(entry)
            if record is None:
                continue

            if record["file_type"] == "folder":
                child_prefix = path_prefix + [entry["name"]]
                results += self._scan_folder(entry["token"], child_prefix)
            else:
                paths = self._build_paths(record["file_name"], path_prefix)
                if paths is None:
                    continue
                record["file_path"], record["file_outputpath"] = paths
                results.append(record)

        return results

    def scan(self, folder_tokens: str | List[str]) -> List[Dict]:
        """扫描一个或多个文件夹，写出 Doc_task_list.json，返回 FileRecord 列表。

        Args:
            folder_tokens: 单个 folder_token 字符串或字符串列表

        Returns:
            所有成功扫描文件夹中的 FileRecord 列表
        """
        from .client import APIError

        if isinstance(folder_tokens, str):
            folder_tokens = [folder_tokens]

        results: List[Dict] = []
        for token in folder_tokens:
            try:
                results += self._scan_folder(token, [])
            except APIError as e:
                log.warning("跳过文件夹 %s: %s", token, e)

        output_path = os.path.join(self.output_dir, "Doc_task_list.json")
        os.makedirs(self.output_dir, exist_ok=True)

        import json
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        return results


# ── 独立函数（不依赖 FeishuClient，直接用 requests + uat）──────────

import requests


def get_folder_meta(folder_token: str, user_access_token: str) -> dict:
    """获取文件夹元数据（名称、ID 等）。

    Args:
        folder_token:       文件夹 token
        user_access_token:  飞书用户访问令牌

    Returns:
        {"id": str, "name": str, "token": str, ...}

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = f"https://open.feishu.cn/open-apis/drive/explorer/v2/folder/{folder_token}/meta"
    headers = {"Authorization": f"Bearer {user_access_token}"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") != 0:
        raise RuntimeError(f"get_folder_meta 失败 {body.get('code')}: {body.get('msg')}")
    return body["data"]


def _list_folder_children(folder_token: str, user_access_token: str) -> list[dict]:
    """列举文件夹下所有子项（自动分页）。"""
    url = "https://open.feishu.cn/open-apis/drive/v1/files"
    headers = {"Authorization": f"Bearer {user_access_token}"}
    results = []
    page_token = None

    while True:
        params = {"folder_token": folder_token, "page_size": 200}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            raise RuntimeError(f"list_folder_children 失败 {body.get('code')}: {body.get('msg')}")

        data = body["data"]
        results.extend(data.get("files", []))

        if not data.get("has_more"):
            break
        page_token = data.get("next_page_token")

    return results


def scan_folder_files(
    folder_token: str,
    user_access_token: str,
    path_prefix: str = "",
) -> list[dict]:
    """递归遍历文件夹，返回所有文件信息（不写文件，不依赖 FeishuClient）。

    Args:
        folder_token:       文件夹 token
        user_access_token:  飞书用户访问令牌
        path_prefix:        当前路径前缀（用于构造 source_folder）

    Returns:
        [{"document_token": str, "file_name": str, "file_type": str, "source_folder": str}, ...]
    """
    entries = _list_folder_children(folder_token, user_access_token)
    results = []

    for entry in entries:
        entry_type = entry.get("type", "")
        entry_name = entry.get("name", "")
        entry_token = entry.get("token", "")

        # shortcut 解引用
        if entry_type == "shortcut":
            shortcut_info = entry.get("shortcut_info", {})
            if not shortcut_info:
                continue
            entry_token = shortcut_info.get("target_token", entry_token)
            entry_type = shortcut_info.get("target_type", entry_type)

        if entry_type == "folder":
            child_prefix = f"{path_prefix}/{entry_name}" if path_prefix else entry_name
            results.extend(scan_folder_files(entry_token, user_access_token, child_prefix))
        elif entry_type in ALLOWED_TYPES:
            results.append({
                "document_token": entry_token,
                "file_name": entry_name,
                "file_type": entry_type,
                "source_folder": path_prefix,
            })

    return results


def batch_get_file_meta(
    files: list[dict],
    user_access_token: str,
) -> dict[str, dict]:
    """批量获取文件元数据（URL、编辑时间、创建时间）。

    Args:
        files:              文件列表，每条需含 document_token 和 file_type
        user_access_token:  飞书用户访问令牌

    Returns:
        {document_token: {"url": str, "latest_modify_time": str,
                          "create_time": str, "title": str}, ...}
        获取失败的 token 不在返回结果中

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = "https://open.feishu.cn/open-apis/drive/v1/metas/batch_query"
    headers = {
        "Authorization": f"Bearer {user_access_token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    result = {}

    # 分批，每批最多 200 个
    for i in range(0, len(files), 200):
        batch = files[i:i + 200]
        request_docs = [
            {"doc_token": f["document_token"], "doc_type": f["file_type"]}
            for f in batch
        ]
        payload = {"request_docs": request_docs, "with_url": True}

        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"batch_get_file_meta 失败 {body.get('code')}: {body.get('msg')}")

        for meta in body["data"].get("metas", []):
            result[meta["doc_token"]] = {
                "url": meta.get("url", ""),
                "latest_modify_time": meta.get("latest_modify_time", ""),
                "create_time": meta.get("create_time", ""),
                "title": meta.get("title", ""),
            }

    return result
