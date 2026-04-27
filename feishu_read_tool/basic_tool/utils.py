"""
utils.py — 通用工具函数库

提供与业务无关的基础辅助函数，不依赖飞书 SDK。
当前包含文件名处理相关工具，供内部模块使用。
"""

import re


class FileError(Exception):
    """文件操作错误"""
    pass


def extract_text_field(field_value) -> str:
    """从飞书 Bitable 字段值提取纯文本字符串。

    支持：
    - 富文本列表 [{"text": "...", "type": "text"}, ...] → 拼接 text
    - 普通字符串 → 直接返回
    - None/空值 → 返回空字符串
    """
    if isinstance(field_value, list):
        return "".join(
            i.get("text", "") for i in field_value if isinstance(i, dict)
        )
    return str(field_value) if field_value else ""


def sanitize_filename(title: str) -> str:
    """将文档标题转换为合法文件名，移除 /\\:*?"<>|\\n\\r，strip 空白。

    Raises:
        FileError: 清理后字符串为空
    """
    sanitized = re.sub(r'[/\\:*?"<>|\n\r]', '', title).strip()
    if not sanitized:
        raise FileError(
            f"Document title '{title}' is empty after sanitization. "
            "Cannot determine output filename."
        )
    return sanitized
