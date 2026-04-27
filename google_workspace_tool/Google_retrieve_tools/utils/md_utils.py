"""Markdown 处理工具。"""

import re


def strip_md_to_plain(md: str) -> str:
    """简单去除 Markdown 标记，得到纯文本。"""
    text = md
    # 去除标题标记
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 去除粗体/斜体/下划线/上标/下标
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"<u>(.+?)</u>", r"\1", text)
    text = re.sub(r"<sup>(.+?)</sup>", r"\1", text)
    text = re.sub(r"<sub>(.+?)</sub>", r"\1", text)
    # 去除链接标记
    text = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", text)
    # 去除表格分隔线
    text = re.sub(r"^\|\s*---+\s*\|.*$", "", text, flags=re.MULTILINE)
    # 去除表格竖线
    text = re.sub(r"\|", " ", text)
    # 去除水平线
    text = re.sub(r"^---\s*$", "", text, flags=re.MULTILINE)
    # 去除 Tab 标记
    text = re.sub(r"^## Tab: .+$", "", text, flags=re.MULTILINE)
    # 去除目录标记
    text = re.sub(r"^## 目录$", "", text, flags=re.MULTILINE)
    # 去除脚注标记格式
    text = re.sub(r"\[\^(\d+)\]", r"[\1]", text)
    # 去除 equation 标记
    text = re.sub(r"\[equation\]", "", text)
    # 去除嵌套表格标记
    text = re.sub(r"\[嵌套表格\]", "", text)
    text = re.sub(r"\[/嵌套表格\]", "", text)
    # 合并多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"
