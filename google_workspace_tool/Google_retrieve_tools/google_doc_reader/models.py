"""
Google Docs 中间表示（IR）数据模型。

定义从 JSON 解析后的结构化数据类型，供 parser → renderer 传递使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ListInfo:
    """列表项的元信息。"""

    list_id: str
    nesting_level: int
    glyph_format: str  # 如 "%0.", "%0"，用于判断有序/无序


@dataclass
class InlineElement:
    """段落内的内联元素。"""

    type: str  # 'text', 'equation', 'equation_symbol', 'hr', 'person',
    # 'rich_link', 'date', 'auto_text', 'footnote_ref', 'page_break', 'column_break'
    text: str = ""
    is_strikethrough: bool = False
    is_bold: bool = False
    is_italic: bool = False
    is_underline: bool = False
    is_suggested_deletion: bool = False
    baseline_offset: Optional[str] = None  # 'SUPERSCRIPT' | 'SUBSCRIPT'
    link_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Block:
    """块级元素（段落、表格、目录等）。"""

    type: str  # 'heading', 'paragraph', 'table', 'toc', 'hr', 'section_break'
    level: Optional[int] = None  # 标题层级 1-6
    style_type: Optional[str] = None  # 'HEADING_1', 'NORMAL_TEXT', etc.
    list_info: Optional[ListInfo] = None
    # 表格: rows -> cells -> blocks (每个 cell 内是 Block 列表)
    cells: Optional[list[list[list[Block]]]] = None
    # 目录/其他嵌套内容: 子 Block 列表
    blocks: list[Block] = field(default_factory=list)
    inline_elements: list[InlineElement] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class TabContent:
    """单个 Tab 的内容。"""

    tab_id: str
    tab_title: str
    index: int
    blocks: list[Block] = field(default_factory=list)


@dataclass
class DocContent:
    """整个文档的内容。"""

    doc_id: str
    title: str
    tabs: list[TabContent] = field(default_factory=list)


@dataclass
class OutlineHeading:
    """大纲中的标题条目。"""

    level: int
    text: str
    style_type: str


@dataclass
class OutlineTab:
    """大纲中的 Tab 条目。"""

    tab_id: str
    tab_title: str
    headings: list[OutlineHeading] = field(default_factory=list)


@dataclass
class DocOutline:
    """文档大纲（仅包含标题结构）。"""

    doc_id: str
    title: str
    tabs: list[OutlineTab] = field(default_factory=list)
