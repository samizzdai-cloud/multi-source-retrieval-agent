"""Google Drive URL 解析工具。

支持从各类 Google Drive 分享链接中提取 file_id。
"""

import re


# 匹配常见 Google Drive / Docs / Sheets / Slides URL 中的 file_id
_FILE_ID_PATTERNS = [
    # https://docs.google.com/document/d/{FILE_ID}/edit
    # https://docs.google.com/spreadsheets/d/{FILE_ID}/edit
    # https://docs.google.com/presentation/d/{FILE_ID}/edit
    re.compile(r"/d/([a-zA-Z0-9_-]+)"),
    # https://drive.google.com/file/d/{FILE_ID}/view
    re.compile(r"/file/d/([a-zA-Z0-9_-]+)"),
    # https://drive.google.com/open?id={FILE_ID}
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
]


def extract_file_id_from_url(url: str) -> str:
    """从 Google Drive URL 中提取 file_id。

    Args:
        url: Google Drive / Docs / Sheets / Slides 分享链接

    Returns:
        提取到的 file_id 字符串

    Raises:
        ValueError: 当无法从 URL 中提取 file_id 时
    """
    if not url or not url.strip():
        raise ValueError("URL 不能为空")

    url = url.strip()

    for pattern in _FILE_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)

    raise ValueError(
        f"无法从 URL 中提取 file_id。支持的格式包括:\n"
        f"  https://docs.google.com/document/d/{{FILE_ID}}/edit\n"
        f"  https://drive.google.com/file/d/{{FILE_ID}}/view\n"
        f"  https://drive.google.com/open?id={{FILE_ID}}\n"
        f"  收到: {url[:80]}"
    )


def resolve_file_id(url: str | None = None, file_id: str | None = None) -> str:
    """统一解析 file_id：从 URL 提取或直接返回 file_id。

    Args:
        url: Google Drive URL（与 file_id 二选一）
        file_id: Drive 文件 ID（与 url 二选一）

    Returns:
        解析后的 file_id

    Raises:
        ValueError: 当两者都传或都没传，或 URL 解析失败时
    """
    if url and file_id:
        raise ValueError("url 和 file_id 只能传一个，不能同时传")
    if not url and not file_id:
        raise ValueError("url 和 file_id 必须传一个")

    if url:
        return extract_file_id_from_url(url)
    return file_id.strip()
