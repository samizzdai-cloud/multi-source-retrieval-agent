"""
渲染层：将中间表示 (IR) 转换为 Markdown 字符串。
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import Block, DocContent, DocOutline, InlineElement, TabContent

logger = logging.getLogger(__name__)


class MarkdownRenderer:
    """将 DocContent 渲染为 Markdown 字符串。"""

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def render(self, doc: DocContent) -> str:
        """渲染完整文档。

        注意：不单独渲染 doc.title 为 # 标题，
        因为 doc.title 是文件名/文档属性，而 tab 内的 TITLE 样式才是内容标题。
        这样避免与 tab 内部的 TITLE（H1）产生冲突。
        """
        lines = []

        for i, tab in enumerate(doc.tabs):
            if i > 0:
                lines.append("---")
                lines.append("")
            lines.extend(self._render_tab(tab))

        return "\n".join(lines).rstrip() + "\n"

    # ------------------------------------------------------------------
    # Tab 渲染
    # ------------------------------------------------------------------

    def _render_tab(self, tab: TabContent) -> list[str]:
        lines = []
        lines.append(f"## Tab: {tab.tab_title}")
        lines.append("")

        for block in tab.blocks:
            rendered = self._render_block(block)
            if rendered is None:
                continue

            if rendered == "":
                # section_break 或空段落：添加空行分隔
                if lines and lines[-1] != "":
                    lines.append("")
                continue

            # 在块之间添加空行（表格/toc 本身是多行的，保持内部结构）
            if block.type in ("table", "toc"):
                if lines and lines[-1] != "":
                    lines.append("")
                lines.extend(rendered.split("\n"))
            else:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(rendered)

        return lines

    # ------------------------------------------------------------------
    # Block 渲染
    # ------------------------------------------------------------------

    def _render_block(self, block: Block, indent: str = "") -> Optional[str]:
        if block.type == "heading":
            prefix = "#" * (block.level or 1)
            text = self._render_inline_elements(block.inline_elements).rstrip("\n")
            # 去除末尾换行符后 strip 首尾空白
            text = text.strip()
            return f"{indent}{prefix} {text}"

        if block.type == "paragraph":
            text = self._render_inline_elements(block.inline_elements).rstrip("\n")
            if block.list_info:
                # 列表项
                marker = self._list_marker(block.list_info)
                # 处理嵌套缩进
                nest_indent = "  " * block.list_info.nesting_level
                return f"{indent}{nest_indent}{marker} {text}"
            return f"{indent}{text}" if text else ""

        if block.type == "table":
            return self._render_table(block, indent)

        if block.type == "toc":
            return self._render_toc(block, indent)

        if block.type == "hr":
            return f"{indent}---"

        if block.type == "section_break":
            return ""  # 空行分隔

        return None

    # ------------------------------------------------------------------
    # Inline 渲染
    # ------------------------------------------------------------------

    def _render_inline_elements(self, elements: list[InlineElement]) -> str:
        parts = []
        for elem in elements:
            rendered = self._render_inline_element(elem)
            if rendered:
                parts.append(rendered)
        return "".join(parts)

    def _render_inline_element(self, elem: InlineElement) -> str:
        text = elem.text.rstrip("\n")

        if elem.type in ("page_break", "column_break"):
            return ""

        if elem.type == "hr":
            return "\n---\n"

        if elem.type in ("equation", "equation_symbol"):
            return f"${text}$" if text else ""

        if elem.type == "footnote_ref":
            return text  # 已经是 [^n] 格式

        if elem.type == "auto_text":
            return text

        if elem.type == "person":
            return text

        if elem.type == "rich_link":
            if elem.link_url:
                return f"{text}({elem.link_url})"
            return text

        if elem.type == "date":
            return text

        # 忽略 bold / italic / underline 格式，只保留纯文本语义
        # 保留上标/下标（有语义差异）和链接（有跳转价值）
        if elem.baseline_offset == "SUPERSCRIPT":
            text = f"<sup>{text}</sup>"
        elif elem.baseline_offset == "SUBSCRIPT":
            text = f"<sub>{text}</sub>"
        if elem.link_url:
            text = f"{text}({elem.link_url})"

        return text

    # ------------------------------------------------------------------
    # 列表标记
    # ------------------------------------------------------------------

    @staticmethod
    def _list_marker(list_info) -> str:
        """根据 glyph_format 判断列表标记类型。"""
        fmt = list_info.glyph_format if list_info else ""
        # 如果包含 %0. %1. 等，判断为有序列表
        if "." in fmt:
            return "1."
        # 否则为无序列表
        return "-"

    # ------------------------------------------------------------------
    # 表格渲染
    # ------------------------------------------------------------------

    def _render_table(self, block: Block, indent: str = "") -> str:
        """
        渲染表格为 Markdown 表格。
        处理嵌套表格：将嵌套表格扁平化为缩进文本块。
        """
        if not block.cells:
            return ""

        rows = block.cells
        if not rows:
            return ""

        # 计算列数（取第一行的 cell 数）
        col_count = max(len(row) for row in rows) if rows else 0
        if col_count == 0:
            return ""

        lines = []

        for row_idx, row in enumerate(rows):
            # 渲染每一行
            cell_texts = []
            for cell_blocks in row:
                cell_text = self._render_cell(cell_blocks, indent)
                cell_texts.append(cell_text)

            # 补齐缺失的列
            while len(cell_texts) < col_count:
                cell_texts.append("")

            # 转义竖线，避免破坏 Markdown 表格
            escaped = [t.replace("|", "\\|") for t in cell_texts]
            lines.append(f"{indent}| " + " | ".join(escaped) + " |")

            # 表头分隔行（第一行后）
            if row_idx == 0:
                sep = f"{indent}|" + "|".join([" --- " for _ in range(col_count)]) + "|"
                lines.append(sep)

        return "\n".join(lines)

    def _render_cell(self, blocks: list[Block], indent: str) -> str:
        """渲染单个 cell 的内容，处理嵌套表格。"""
        parts = []
        for block in blocks:
            if block.type == "table" and block.cells:
                # 嵌套表格：扁平化为缩进文本块
                nested = self._render_nested_table(block, indent + "  ")
                parts.append(nested)
            else:
                rendered = self._render_block(block, indent)
                if rendered is not None:
                    parts.append(rendered)
        # 将 cell 内的多行合并为单行（Markdown 表格 cell 内不能有多行）
        text = " ".join(p.strip() for p in parts if p.strip())
        # 将换行替换为空格
        text = text.replace("\n", " ")
        return text

    def _render_nested_table(self, block: Block, indent: str) -> str:
        """将嵌套表格压扁降级为单行文本，使用类似 [cell:A1{text}] 格式表示子表单元格关系。"""
        parts = []
        if block.cells:
            for r_idx, row in enumerate(block.cells):
                for c_idx, cell_blocks in enumerate(row):
                    cell_text = self._render_cell(cell_blocks, "").strip()
                    # 只有有内容才拼进去
                    if cell_text:
                        # 用 A, B, C 表示列
                        col_letter = chr(ord('A') + c_idx) if c_idx < 26 else str(c_idx + 1)
                        # r_idx + 1 表示行
                        cell_id = f"{col_letter}{r_idx + 1}"
                        parts.append(f"[cell:{cell_id}{{{cell_text}}}]")
        return " ".join(parts)

    # ------------------------------------------------------------------
    # 目录渲染
    # ------------------------------------------------------------------

    def _render_toc(self, block: Block, indent: str = "") -> str:
        """渲染目录为链接列表。"""
        lines = [f"{indent}## 目录", ""]
        for sub_block in getattr(block, "blocks", []):
            text = self._render_inline_elements(sub_block.inline_elements).strip()
            if text:
                # 尝试从 headingId 生成锚点链接
                anchor = ""
                style = getattr(sub_block, "style_type", "")
                if style and style.startswith("HEADING"):
                    anchor = text.lower().replace(" ", "-").replace(".", "")
                    anchor = f"#{anchor}"
                if anchor:
                    lines.append(f"{indent}- [{text}]({anchor})")
                else:
                    lines.append(f"{indent}- {text}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 大纲渲染（仅标题结构）
    # ------------------------------------------------------------------

    def render_outline(self, outline: DocOutline) -> str:
        """将 DocOutline 渲染为 Markdown 大纲。"""
        lines = []
        if outline.title:
            lines.append(f"# {outline.title}")
            lines.append("")

        for i, tab in enumerate(outline.tabs):
            if i > 0:
                lines.append("---")
                lines.append("")
            lines.append(f"## Tab: {tab.tab_title}")
            lines.append("")
            for heading in tab.headings:
                prefix = "#" * heading.level
                lines.append(f"{prefix} {heading.text}")
            if not tab.headings:
                lines.append("*(无标题)*")
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"
