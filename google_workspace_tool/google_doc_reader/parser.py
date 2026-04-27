"""
解析层：将 Google Docs API 返回的 JSON 转换为中间表示 (IR)。

核心能力：
- 递归遍历 tabs（含 childTabs）
- 解析 paragraphs、tables、tableOfContents
- 过滤删除线（strikethrough）和建议删除/插入的内容
- 提取列表信息（有序/无序）
- 支持上标/下标、脚注引用、日期元素、自动文本
"""

from __future__ import annotations

import logging
from typing import Optional

from .models import Block, DocContent, DocOutline, InlineElement, ListInfo, OutlineHeading, OutlineTab, TabContent

logger = logging.getLogger(__name__)

# namedStyleType → heading level 映射
_HEADING_LEVEL_MAP = {
    "HEADING_1": 1,
    "HEADING_2": 2,
    "HEADING_3": 3,
    "HEADING_4": 4,
    "HEADING_5": 5,
    "HEADING_6": 6,
    "TITLE": 1,  # TITLE 映射为 H1
    "SUBTITLE": 2,  # SUBTITLE 映射为 H2
}

# 块级类型映射
_BLOCK_TYPE_MAP = {
    "HEADING_1": "heading",
    "HEADING_2": "heading",
    "HEADING_3": "heading",
    "HEADING_4": "heading",
    "HEADING_5": "heading",
    "HEADING_6": "heading",
    "TITLE": "heading",
    "SUBTITLE": "heading",
    "NORMAL_TEXT": "paragraph",
}


# AutoText 类型 → 占位符文本
_AUTO_TEXT_LABELS = {
    "PAGE_NUMBER": "[页码]",
    "PAGE_COUNT": "[总页数]",
}


def _has_suggested_deletion(element: dict) -> bool:
    """检查元素是否被建议删除。"""
    return bool(element.get("suggestedDeletionIds"))


def _has_suggested_insertion_only(element: dict) -> bool:
    """检查元素是否仅为建议插入（正式内容中不应出现）。

    当使用 PREVIEW_WITHOUT_SUGGESTIONS 模式时，建议插入的内容
    不应出现在最终输出中。
    """
    return bool(element.get("suggestedInsertionIds")) and not _has_suggested_deletion(
        element
    )


class DocParser:
    """Google Docs JSON 解析器。"""

    def parse(self, doc_json: dict, max_tabs: int | None = None) -> DocContent:
        """
        将 documents.get 返回的 JSON 解析为 DocContent。

        Args:
            doc_json: documents.get 返回的 JSON
            max_tabs: 如果指定，只保留最后 N 个 tabs（按 index 排序最大的 N 个）
        """
        doc_id = doc_json.get("documentId", "")
        title = doc_json.get("title", "")

        # 扁平化所有 tabs
        tabs_json = self._flatten_tabs(doc_json)

        # 如果指定了 max_tabs，只保留最后 N 个（按 index 排序）
        if max_tabs is not None and len(tabs_json) > max_tabs:
            # 按 index 排序后取最后 N 个
            tabs_json = sorted(tabs_json, key=lambda t: t.get("tabProperties", {}).get("index", 0))
            tabs_json = tabs_json[-max_tabs:]

        tabs = [self._parse_tab(tab_json, idx) for idx, tab_json in enumerate(tabs_json)]

        return DocContent(doc_id=doc_id, title=title, tabs=tabs)

    # ------------------------------------------------------------------
    # Tabs 遍历
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten_tabs(doc_json: dict) -> list[dict]:
        """递归扁平化所有 tabs（含 childTabs），按 UI 顺序排列。"""
        all_tabs = []

        def _recurse(tab: dict):
            all_tabs.append(tab)
            for child in tab.get("childTabs", []):
                _recurse(child)

        for tab in doc_json.get("tabs", []):
            _recurse(tab)
        return all_tabs

    # ------------------------------------------------------------------
    # Tab 解析
    # ------------------------------------------------------------------

    def _parse_tab(self, tab_json: dict, index: int) -> TabContent:
        props = tab_json.get("tabProperties", {})
        tab_id = props.get("tabId", f"tab_{index}")
        tab_title = props.get("title", f"Tab {index + 1}")

        document_tab = tab_json.get("documentTab", {})
        body = document_tab.get("body", {})
        content = body.get("content", [])

        # lists 字典用于解析列表项的有序/无序类型
        lists = document_tab.get("lists", {})

        blocks = self._parse_structural_elements(content, lists)
        return TabContent(tab_id=tab_id, tab_title=tab_title, index=index, blocks=blocks)

    # ------------------------------------------------------------------
    # StructuralElement 解析
    # ------------------------------------------------------------------

    def _parse_structural_elements(
        self, elements: list[dict], lists: dict
    ) -> list[Block]:
        """解析 StructuralElement 列表。"""
        blocks = []
        for elem in elements:
            if _has_suggested_deletion(elem):
                continue
            if _has_suggested_insertion_only(elem):
                continue

            if "paragraph" in elem:
                block = self._parse_paragraph(elem["paragraph"], lists)
                if block:
                    blocks.append(block)
            elif "table" in elem:
                block = self._parse_table(elem["table"], lists)
                if block:
                    blocks.append(block)
            elif "tableOfContents" in elem:
                block = self._parse_toc(elem["tableOfContents"], lists)
                if block:
                    blocks.append(block)
            elif "sectionBreak" in elem:
                # 分节符：标记为空块或忽略
                blocks.append(Block(type="section_break"))
            # 其他未知类型忽略
        return blocks

    # ------------------------------------------------------------------
    # Paragraph 解析
    # ------------------------------------------------------------------

    def _parse_paragraph(self, paragraph: dict, lists: dict) -> Optional[Block]:
        style = paragraph.get("paragraphStyle", {})
        named_style = style.get("namedStyleType", "NORMAL_TEXT")

        # 判断是否为水平线：只有一个 horizontalRule 元素且无名文内容
        elements = paragraph.get("elements", [])
        if len(elements) == 1 and "horizontalRule" in elements[0]:
            return Block(type="hr")

        # 解析 inline elements
        inline_elements = []
        for elem in elements:
            inline = self._parse_paragraph_element(elem)
            if inline:
                inline_elements.append(inline)

        # 过滤后为空段落/空标题的处理
        raw_text = "".join(ie.text for ie in inline_elements).rstrip("\n")

        block_type = _BLOCK_TYPE_MAP.get(named_style, "paragraph")
        level = _HEADING_LEVEL_MAP.get(named_style)

        # 列表信息
        bullet = paragraph.get("bullet")
        list_info = None
        if bullet:
            list_id = bullet.get("listId", "")
            nesting_level = bullet.get("nestingLevel", 0)
            glyph_format = ""
            list_props = lists.get(list_id, {}).get("listProperties", {})
            nesting_levels = list_props.get("nestingLevels", [])
            if nesting_level < len(nesting_levels):
                glyph_format = nesting_levels[nesting_level].get("glyphFormat", "")
            list_info = ListInfo(
                list_id=list_id, nesting_level=nesting_level, glyph_format=glyph_format
            )
            block_type = "paragraph"  # 列表项本质是 paragraph，由 list_info 标记

        # 空内容过滤：
        # 1. 空 heading（无可见文本）→ 跳过
        if block_type == "heading" and not raw_text.strip():
            return None
        # 2. 空段落（无 inline_elements、无 list_info、无可见文本）→ 跳过
        if block_type == "paragraph" and not list_info and not raw_text.strip():
            return None

        return Block(
            type=block_type,
            level=level,
            style_type=named_style,
            list_info=list_info,
            inline_elements=inline_elements,
            raw_text=raw_text,
        )

    # ------------------------------------------------------------------
    # ParagraphElement 解析
    # ------------------------------------------------------------------

    def _parse_paragraph_element(self, elem: dict) -> Optional[InlineElement]:
        """解析单个 ParagraphElement。"""
        if _has_suggested_deletion(elem):
            return None
        if _has_suggested_insertion_only(elem):
            return None

        # textRun
        if "textRun" in elem:
            return self._parse_text_run(elem["textRun"], elem)

        # autoText
        if "autoText" in elem:
            at = elem["autoText"]
            auto_type = at.get("type", "")
            # autoText 的 content 可能为空（动态内容），使用类型作为占位符
            text = at.get("content", "") or _AUTO_TEXT_LABELS.get(auto_type, f"[auto:{auto_type}]")
            return InlineElement(
                type="auto_text",
                text=text,
            )

        # pageBreak
        if "pageBreak" in elem:
            return InlineElement(type="page_break", text="")

        # columnBreak
        if "columnBreak" in elem:
            return InlineElement(type="column_break", text="")

        # footnoteReference
        if "footnoteReference" in elem:
            fr = elem["footnoteReference"]
            return InlineElement(
                type="footnote_ref",
                text=f"[^{fr.get('footnoteNumber', '')}]",
                metadata={"footnoteId": fr.get("footnoteId", "")},
            )

        # horizontalRule（在 paragraph 级别已处理，此处作为兜底）
        if "horizontalRule" in elem:
            return InlineElement(type="hr", text="")

        # equation
        if "equation" in elem:
            eq = elem["equation"]
            # equation 的内容通常不直接有 content，但保留防御
            return InlineElement(type="equation", text=eq.get("content", ""))

        # equationSymbol
        if "equationSymbol" in elem:
            es = elem["equationSymbol"]
            return InlineElement(type="equation_symbol", text=es.get("content", ""))

        # inlineObjectElement（图片等）→ 完全跳过
        if "inlineObjectElement" in elem:
            return None

        # person
        if "person" in elem:
            p = elem["person"]
            name = p.get("personProperties", {}).get("name", "")
            email = p.get("personProperties", {}).get("email", "")
            text = name or email or ""
            return InlineElement(
                type="person", text=text, metadata={"email": email, "name": name}
            )

        # richLink
        if "richLink" in elem:
            rl = elem["richLink"]
            rp = rl.get("richLinkProperties", {})
            title = rp.get("title", "")
            url = rp.get("uri", "")
            return InlineElement(
                type="rich_link",
                text=title or url,
                link_url=url,
                metadata={"title": title, "uri": url},
            )

        # dateElement
        if "dateElement" in elem:
            de = elem["dateElement"]
            # 优先使用 displayText（API 输出字段），否则尝试 text.content
            props = de.get("dateElementProperties", {})
            text = props.get("displayText", "")
            if not text and "text" in de:
                text = de["text"].get("content", "")
            return InlineElement(type="date", text=text)

        # 未知类型
        return None

    def _parse_text_run(self, text_run: dict, parent_elem: dict) -> Optional[InlineElement]:
        """解析 textRun。"""
        style = text_run.get("textStyle", {})

        # 过滤删除线
        if style.get("strikethrough"):
            return None

        # 过滤建议删除（防御性检查）
        if _has_suggested_deletion(parent_elem):
            return None

        # 过滤纯建议插入（在 PREVIEW_WITHOUT_SUGGESTIONS 模式下不应出现）
        if _has_suggested_insertion_only(parent_elem):
            return None

        content = text_run.get("content", "")
        if not content:
            return None

        # 链接
        link_url = None
        link = style.get("link")
        if link:
            link_url = link.get("url", "")

        # 上标/下标
        baseline_offset = style.get("baselineOffset")

        return InlineElement(
            type="text",
            text=content,
            is_bold=style.get("bold", False),
            is_italic=style.get("italic", False),
            is_underline=style.get("underline", False),
            is_strikethrough=style.get("strikethrough", False),
            is_suggested_deletion=_has_suggested_deletion(parent_elem),
            baseline_offset=baseline_offset if baseline_offset not in (None, "NONE", "BASELINE_OFFSET_UNSPECIFIED") else None,
            link_url=link_url,
        )

    # ------------------------------------------------------------------
    # Table 解析
    # ------------------------------------------------------------------

    def _parse_table(self, table: dict, lists: dict) -> Optional[Block]:
        """解析表格。"""
        # 过滤整个表格的建议删除/插入
        if _has_suggested_deletion(table) or _has_suggested_insertion_only(table):
            return None

        rows_data = []
        for row in table.get("tableRows", []):
            # 过滤行的建议删除/插入
            if _has_suggested_deletion(row) or _has_suggested_insertion_only(row):
                continue

            cells_data = []
            for cell in row.get("tableCells", []):
                # 过滤单元格的建议删除/插入
                if _has_suggested_deletion(cell) or _has_suggested_insertion_only(cell):
                    continue

                cell_blocks = self._parse_structural_elements(
                    cell.get("content", []), lists
                )
                cells_data.append(cell_blocks)

            if cells_data:
                rows_data.append(cells_data)

        if not rows_data:
            return None

        return Block(type="table", cells=rows_data)

    # ------------------------------------------------------------------
    # TableOfContents 解析
    # ------------------------------------------------------------------

    def _parse_toc(self, toc: dict, lists: dict) -> Block:
        """解析目录。"""
        blocks = self._parse_structural_elements(toc.get("content", []), lists)
        return Block(type="toc", blocks=blocks)


# =============================================================================
# Outline 提取器：快速提取文档标题结构（不展开全部 textRuns）
# =============================================================================

class OutlineExtractor:
    """
    从 Google Docs JSON 中快速提取大纲结构。

    与 DocParser 不同，OutlineExtractor 只关注：
    - Tab 树结构（含 childTabs）
    - Heading 段落（提取标题文本，忽略其他内容）

    这适用于：
    - 大型文档的快速预览
    - 用户选择感兴趣的 section 后再做完整读取
    """

    _HEADING_STYLES = frozenset(
        {"HEADING_1", "HEADING_2", "HEADING_3", "HEADING_4", "HEADING_5", "HEADING_6", "TITLE", "SUBTITLE"}
    )

    _HEADING_LEVEL_MAP = {
        "HEADING_1": 1,
        "HEADING_2": 2,
        "HEADING_3": 3,
        "HEADING_4": 4,
        "HEADING_5": 5,
        "HEADING_6": 6,
        "TITLE": 1,
        "SUBTITLE": 2,
    }

    def extract(self, doc_json: dict) -> DocOutline:
        """从 documents.get JSON 中提取大纲。"""
        doc_id = doc_json.get("documentId", "")
        title = doc_json.get("title", "")

        tabs_json = self._flatten_tabs(doc_json)
        tabs = [self._extract_tab(tab_json, idx) for idx, tab_json in enumerate(tabs_json)]

        return DocOutline(doc_id=doc_id, title=title, tabs=tabs)

    @staticmethod
    def _flatten_tabs(doc_json: dict) -> list[dict]:
        """递归扁平化所有 tabs（含 childTabs），按 UI 顺序排列。"""
        all_tabs = []

        def _recurse(tab: dict):
            all_tabs.append(tab)
            for child in tab.get("childTabs", []):
                _recurse(child)

        for tab in doc_json.get("tabs", []):
            _recurse(tab)
        return all_tabs

    def _extract_tab(self, tab_json: dict, index: int) -> OutlineTab:
        props = tab_json.get("tabProperties", {})
        tab_id = props.get("tabId", f"tab_{index}")
        tab_title = props.get("title", f"Tab {index + 1}")

        document_tab = tab_json.get("documentTab", {})
        body = document_tab.get("body", {})
        content = body.get("content", [])

        headings = self._extract_headings(content)
        return OutlineTab(tab_id=tab_id, tab_title=tab_title, headings=headings)

    def _extract_headings(self, elements: list[dict]) -> list[OutlineHeading]:
        """从 StructuralElement 列表中提取 heading。"""
        headings = []
        for elem in elements:
            if _has_suggested_deletion(elem) or _has_suggested_insertion_only(elem):
                continue

            if "paragraph" in elem:
                heading = self._extract_heading_from_paragraph(elem["paragraph"])
                if heading:
                    headings.append(heading)
            elif "table" in elem:
                # 表格中可能包含 heading，递归扫描（仅到 cell 的 content 一层）
                for row in elem["table"].get("tableRows", []):
                    for cell in row.get("tableCells", []):
                        cell_headings = self._extract_headings(cell.get("content", []))
                        headings.extend(cell_headings)
            elif "tableOfContents" in elem:
                # TOC 本身不提取 heading，跳过以减少噪声
                pass
        return headings

    def _extract_heading_from_paragraph(self, paragraph: dict) -> Optional[OutlineHeading]:
        """如果段落是 heading，提取其文本和级别。"""
        style = paragraph.get("paragraphStyle", {})
        named_style = style.get("namedStyleType", "NORMAL_TEXT")

        if named_style not in self._HEADING_STYLES:
            return None

        # 快速提取文本：只合并 textRun 的 content，不做完整 inline 解析
        text_parts = []
        for elem in paragraph.get("elements", []):
            if _has_suggested_deletion(elem) or _has_suggested_insertion_only(elem):
                continue
            if "textRun" in elem:
                tr = elem["textRun"]
                # 跳过删除线
                if tr.get("textStyle", {}).get("strikethrough"):
                    continue
                text_parts.append(tr.get("content", ""))

        text = "".join(text_parts).strip().replace("\n", " ")
        if not text:
            return None

        level = self._HEADING_LEVEL_MAP.get(named_style, 1)
        return OutlineHeading(level=level, text=text, style_type=named_style)
