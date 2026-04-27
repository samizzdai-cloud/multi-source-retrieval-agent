"""Word (.docx) 专用读取器。

通过 Drive API 下载文件后，用 python-docx 解析为 Markdown。
提供 read_one / read_batch / read_concurrent 三种模式。
支持提取 Word comments（批注）并内联标注。
使用 Word 原生 numPr/numId 检测有序/无序列表。
"""

import io
import zipfile
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from utils.download_utils import download_file

_MIME_WORD = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MIME_WORD_OLD = "application/msword"

_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _extract_comments(raw_bytes: bytes) -> dict[str, tuple[str, str]]:
    """从 docx 文件中提取 comments。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        if "word/comments.xml" not in zf.namelist():
            return {}

        from lxml import etree

        with zf.open("word/comments.xml") as f:
            tree = etree.parse(f)
        root = tree.getroot()

        comments = {}
        for c in root.iter(f"{{{_NS_W}}}comment"):
            cid = c.get(f"{{{_NS_W}}}id", "")
            author = c.get(f"{{{_NS_W}}}author", "")
            text = "".join(t.text or "" for t in c.iter(f"{{{_NS_W}}}t"))
            if cid:
                comments[cid] = (author, text)
        return comments
    except Exception:
        return {}


def _extract_numbering(raw_bytes: bytes) -> dict[str, dict]:
    """从 docx 文件中提取 numbering 定义。

    Returns:
        {numId: {abstractNumId, levels: {ilvl: (numFmt, lvlText)}}}
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        if "word/numbering.xml" not in zf.namelist():
            return {}

        from lxml import etree

        with zf.open("word/numbering.xml") as f:
            tree = etree.parse(f)
        root = tree.getroot()

        # 先解析 abstractNum
        abstract_nums = {}
        for anum in root.iter(f"{{{_NS_W}}}abstractNum"):
            anid = anum.get(f"{{{_NS_W}}}abstractNumId", "")
            levels = {}
            for lvl in anum.iter(f"{{{_NS_W}}}lvl"):
                ilvl = lvl.get(f"{{{_NS_W}}}ilvl", "0")
                num_fmt = lvl.find(f"{{{_NS_W}}}numFmt")
                lvl_text = lvl.find(f"{{{_NS_W}}}lvlText")
                fmt = num_fmt.get(f"{{{_NS_W}}}val") if num_fmt is not None else "decimal"
                text = lvl_text.get(f"{{{_NS_W}}}val") if lvl_text is not None else "%1."
                levels[ilvl] = (fmt, text)
            abstract_nums[anid] = levels

        # 再解析 num -> abstractNumId 映射
        numbering = {}
        for num in root.iter(f"{{{_NS_W}}}num"):
            num_id = num.get(f"{{{_NS_W}}}numId", "")
            for anid_elem in num.iter(f"{{{_NS_W}}}abstractNumId"):
                anid = anid_elem.get(f"{{{_NS_W}}}val", "")
                if anid in abstract_nums:
                    numbering[num_id] = {
                        "abstractNumId": anid,
                        "levels": abstract_nums[anid],
                    }
        return numbering
    except Exception:
        return {}


def _get_list_info(para_element) -> tuple[Optional[str], Optional[str]]:
    """获取段落的列表信息。

    Returns:
        (numId, ilvl) 或 (None, None)
    """
    pPr = para_element.find(f"{{{_NS_W}}}pPr")
    if pPr is None:
        return None, None
    numPr = pPr.find(f"{{{_NS_W}}}numPr")
    if numPr is None:
        return None, None

    numId_elem = numPr.find(f"{{{_NS_W}}}numId")
    ilvl_elem = numPr.find(f"{{{_NS_W}}}ilvl")

    numId = numId_elem.get(f"{{{_NS_W}}}val") if numId_elem is not None else None
    ilvl = ilvl_elem.get(f"{{{_NS_W}}}val") if ilvl_elem is not None else "0"

    return numId, ilvl


def _format_list_prefix(numId: str, ilvl: str, numbering: dict, list_counters: dict) -> str:
    """生成列表项前缀（如 '1.', 'a.', '-'）。

    Args:
        numId: 编号 ID
        ilvl: 缩进级别
        numbering: numbering 定义
        list_counters: 每个 numId+ilvl 的当前计数器

    Returns:
        前缀字符串，如 "1. ", "- ", "a. "
    """
    level_key = f"{numId}:{ilvl}"
    list_counters[level_key] = list_counters.get(level_key, 0) + 1
    idx = list_counters[level_key]

    num_def = numbering.get(numId, {})
    levels = num_def.get("levels", {})
    fmt, tmpl = levels.get(ilvl, ("decimal", "%1."))

    # 生成编号文本
    if fmt == "decimal":
        num_str = str(idx)
    elif fmt == "lowerLetter":
        num_str = chr(ord("a") + (idx - 1) % 26)
    elif fmt == "upperLetter":
        num_str = chr(ord("A") + (idx - 1) % 26)
    elif fmt == "lowerRoman":
        num_str = _to_roman(idx).lower()
    elif fmt == "upperRoman":
        num_str = _to_roman(idx).upper()
    elif fmt == "bullet":
        return "- "
    elif fmt == "chicago":
        num_str = str(idx)
    else:
        num_str = str(idx)

    # 替换模板中的 %N 占位符为编号值
    prefix = tmpl
    for i in range(1, 10):
        prefix = prefix.replace(f"%{i}", num_str)
    return prefix + " "


def _to_roman(n: int) -> str:
    """整数转罗马数字。"""
    val = [1000, 900, 500, 400, 100, 90, 50, 40, 10, 9, 5, 4, 1]
    syb = ["M", "CM", "D", "CD", "C", "XC", "L", "XL", "X", "IX", "V", "IV", "I"]
    roman_num = ""
    for i, v in enumerate(val):
        while n >= v:
            n -= v
            roman_num += syb[i]
    return roman_num


def _reset_list_counters() -> dict:
    """创建新的列表计数器。"""
    return {}


def _is_in_table(element) -> bool:
    """通过父元素链判断元素是否在表格中。"""
    parent = element.getparent()
    while parent is not None:
        tag = parent.tag.split("}")[-1] if "}" in parent.tag else parent.tag
        if tag == "tbl":
            return True
        parent = parent.getparent()
    return False


def _try_extract_word_text(raw_bytes: bytes) -> Optional[str]:
    """用 python-docx 提取 Word 文本并转为 Markdown 格式。"""
    try:
        import docx  # type: ignore
    except ImportError:
        return None
    try:
        import re
        from docx.text.paragraph import Paragraph

        document = docx.Document(io.BytesIO(raw_bytes))
        parts = []

        # 提取 comments 和 numbering
        comments = _extract_comments(raw_bytes)
        numbering = _extract_numbering(raw_bytes)

        # 列表计数器（按 body 遍历顺序维护）
        list_counters = _reset_list_counters()
        last_list_numId = None

        def _looks_like_heading_text(text: str) -> bool:
            if len(text) < 10:
                return False
            if text.isupper() and len(text) < 40:
                return False
            if re.search(r"https?://|www\.|\.[a-z]{2,}$", text, re.IGNORECASE):
                return False
            words = text.split()
            if len(words) < 3:
                return False
            return True

        def _is_heading(para) -> tuple[bool, int]:
            text = para.text.strip()
            style = para.style.name if para.style else "Normal"
            style_lower = style.lower()

            if style_lower.startswith("heading"):
                words = text.split()
                if len(words) >= 2 or len(text) >= 15:
                    level_str = style.split()[-1]
                    if level_str.isdigit():
                        return True, int(level_str)
                    return True, 1
                return False, 0

            if len(text) < 5:
                return False, 0
            if re.search(r"https?://|www\.", text):
                return False, 0

            if len(para.runs) == 1 and len(text) < 80:
                run = para.runs[0]
                if run.bold and not run.italic and _looks_like_heading_text(text):
                    return True, 1

            non_empty = [r for r in para.runs if r.text.strip()]
            if non_empty and all(r.bold for r in non_empty) and len(text) < 100:
                if _looks_like_heading_text(text):
                    return True, 1

            return False, 0

        def _render_paragraph_text(para_element, list_counters, last_numId_ref) -> str:
            """渲染单个段落的文本，包括列表前缀。"""
            # 获取文本（支持标准 runs 和 sdt）
            para = Paragraph(para_element, document.part)
            text = para.text.strip()
            if not text:
                text = "".join(t.text or "" for t in para_element.iter(f"{{{_NS_W}}}t")).strip()
            if not text:
                return ""

            # 检测列表属性
            numId, ilvl = _get_list_info(para_element)

            if numId is not None and numId in numbering:
                # 新列表开始或列表切换时重置计数器
                if numId != last_numId_ref[0]:
                    # 只清除该 numId 的计数器
                    keys_to_remove = [k for k in list_counters if k.startswith(f"{numId}:")]
                    for k in keys_to_remove:
                        del list_counters[k]
                last_numId_ref[0] = numId

                prefix = _format_list_prefix(numId, ilvl, numbering, list_counters)
                indent = "  " * int(ilvl)
                return indent + prefix + text
            else:
                # 非列表段落：如果之前是列表，重置 last_numId
                if last_numId_ref[0] is not None:
                    last_numId_ref[0] = None
                return text

        def _render_cell_text(cell, list_counters, last_numId_ref) -> str:
            """渲染单元格文本，处理列表段落。"""
            cell_lines = []
            for para in cell.paragraphs:
                text = para.text.strip()
                if not text:
                    continue

                # 检测列表属性
                numId, ilvl = _get_list_info(para._element)
                if numId is not None and numId in numbering:
                    if numId != last_numId_ref[0]:
                        keys_to_remove = [k for k in list_counters if k.startswith(f"{numId}:")]
                        for k in keys_to_remove:
                            del list_counters[k]
                    last_numId_ref[0] = numId
                    prefix = _format_list_prefix(numId, ilvl, numbering, list_counters)
                    indent = "  " * int(ilvl)
                    cell_lines.append(indent + prefix + text)
                else:
                    if last_numId_ref[0] is not None:
                        last_numId_ref[0] = None
                    cell_lines.append(text)

            if not cell_lines:
                return ""
            # 多行单元格内容用 ", " 连接（不用 <br>）
            return ", ".join(cell_lines).replace("|", "\\|")

        table_by_element = {id(t._element): t for t in document.tables}
        last_numId_ref = [None]

        for idx, child in enumerate(document.element.body):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                if _is_in_table(child):
                    continue

                line = _render_paragraph_text(child, list_counters, last_numId_ref)
                if line:
                    # Heading 检测（仅对非列表段落）
                    numId, _ = _get_list_info(child)
                    if numId is None:
                        para = Paragraph(child, document.part)
                        if para.runs:
                            is_h, level = _is_heading(para)
                            if is_h:
                                line = "#" * min(level, 6) + " " + line

                    parts.append(line)

            elif tag == "tbl":
                table = table_by_element.get(id(child))
                if table and table.rows:
                    col_count = max(len(row.cells) for row in table.rows)
                    for ri, row in enumerate(table.rows):
                        cells = [_render_cell_text(cell, list_counters, last_numId_ref) for cell in row.cells]
                        while len(cells) < col_count:
                            cells.append("")
                        parts.append("| " + " | ".join(cells) + " |")
                        if ri == 0:
                            parts.append(
                                "|" + "|".join([" --- " for _ in range(col_count)]) + "|"
                            )
                    parts.append("")

            # 在对应 body index 的位置附加 comments（附加到最近非空行）
            # 收集 body index -> comment_ids

        # 预处理 comment 目标位置
        non_empty_indices = []
        comment_map_by_index = {}
        for i, child in enumerate(document.element.body):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            cids = [s.get(f"{{{_NS_W}}}id") for s in child.iter(f"{{{_NS_W}}}commentRangeStart")]
            if cids:
                comment_map_by_index[i] = cids

            is_non_empty = False
            if tag == "p":
                if not _is_in_table(child):
                    para = Paragraph(child, document.part)
                    text = para.text.strip()
                    if not text:
                        text = "".join(t.text or "" for t in child.iter(f"{{{_NS_W}}}t")).strip()
                    if text:
                        is_non_empty = True
            elif tag == "tbl":
                is_non_empty = True
            if is_non_empty:
                non_empty_indices.append(i)

        comment_target = {}
        for idx, cids in comment_map_by_index.items():
            nearest = None
            min_dist = float('inf')
            for ni in non_empty_indices:
                dist = abs(ni - idx)
                if dist < min_dist:
                    min_dist = dist
                    nearest = ni
            if nearest is not None:
                for cid in cids:
                    if cid and cid not in comment_target:
                        comment_target[cid] = nearest

        # 渲染 + 附加 comments（一次遍历）
        parts = []
        list_counters = _reset_list_counters()
        last_numId_ref = [None]

        def _append_comments_for_body_idx(idx):
            """将 target 为 idx 的 comments 附加到 parts 最后一个非空元素。"""
            for cid, target_idx in comment_target.items():
                if target_idx == idx and cid in comments:
                    author, ctext = comments[cid]
                    for j in range(len(parts) - 1, -1, -1):
                        if parts[j].strip():
                            parts[j] += f" [comment: {author}: {ctext}]"
                            break

        for idx, child in enumerate(document.element.body):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag

            if tag == "p":
                if _is_in_table(child):
                    continue

                line = _render_paragraph_text(child, list_counters, last_numId_ref)
                if line:
                    numId, _ = _get_list_info(child)
                    if numId is None:
                        para = Paragraph(child, document.part)
                        if para.runs:
                            is_h, level = _is_heading(para)
                            if is_h:
                                line = "#" * min(level, 6) + " " + line
                    parts.append(line)

            elif tag == "tbl":
                table = table_by_element.get(id(child))
                if table and table.rows:
                    col_count = max(len(row.cells) for row in table.rows)
                    for ri, row in enumerate(table.rows):
                        cells = [_render_cell_text(cell, list_counters, last_numId_ref) for cell in row.cells]
                        while len(cells) < col_count:
                            cells.append("")
                        parts.append("| " + " | ".join(cells) + " |")
                        if ri == 0:
                            parts.append(
                                "|" + "|".join([" --- " for _ in range(col_count)]) + "|"
                            )
                    parts.append("")

            # 在当前 body child 渲染完成后，附加 target 为当前 idx 的 comments
            _append_comments_for_body_idx(idx)

        return "\n".join(parts)
    except Exception:
        return None


def read_one(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
) -> dict:
    """读取单个 Word 文档。"""
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        raw_bytes = download_file(drive_service, file_id, supports_all_drives)
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": _MIME_WORD,
            "title": "",
            "content": None,
            "structured_md": None,
            "content_type": None,
            "raw_bytes": None,
            "error": f"下载失败: {e}",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }

    title = ""
    try:
        meta = drive_service.files().get(
            fileId=file_id, fields="name", supportsAllDrives=supports_all_drives
        ).execute()
        title = meta.get("name", "")
    except HttpError:
        pass

    text = _try_extract_word_text(raw_bytes)
    if text is not None:
        return {
            "file_id": file_id,
            "mime_type": _MIME_WORD,
            "title": title,
            "content": text,
            "structured_md": text,
            "content_type": "text",
            "raw_bytes": raw_bytes,
            "error": None,
            "mode": "full",
            "drive_size": None,
            "skip_reason": None,
        }
    else:
        return {
            "file_id": file_id,
            "mime_type": _MIME_WORD,
            "title": title,
            "content": None,
            "structured_md": None,
            "content_type": "bytes",
            "raw_bytes": raw_bytes,
            "error": "无法解析 Word 内容（python-docx 未安装或解析失败）",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(
    file_ids: list[str],
    creds,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个 Word 文档。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_WORD,
                "error": str(e),
                "content": None,
                "structured_md": None,
            })
    return results


def read_concurrent(
    file_ids: list[str],
    creds,
    max_workers: int = 5,
    supports_all_drives: bool = True,
) -> list[dict]:
    """并发批量读取多个 Word 文档。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_WORD,
                "error": str(e),
                "content": None,
                "structured_md": None,
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))
    return results
