"""PPTX (.pptx) 专用读取器。

通过 Drive API 或 Gmail 下载二进制内容后，用 python-pptx 等工具解析为 Markdown 文本。
"""

import io
import zipfile
import xml.etree.ElementTree as etree
import re
from typing import List, Optional, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.utils.download_utils import download_file

_MIME_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

_NS_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _extract_comments(raw_bytes: bytes) -> dict:
    """提取 PPTX 文件中的批注。
    
    返回 dict 格式：{ "slide1.xml": ["Author: comment", ...], ... }
    """
    comments_by_slide = {}
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw_bytes))
        namelist = zf.namelist()
        
        # 1. 提取作者信息
        authors = {}
        if "ppt/commentAuthors.xml" in namelist:
            with zf.open("ppt/commentAuthors.xml") as f:
                tree = etree.parse(f)
                root = tree.getroot()
                for author in root.findall(f".//{{{_NS_P}}}cmAuthor"):
                    a_id = author.get("id")
                    name = author.get("name", "Unknown")
                    if a_id:
                        authors[a_id] = name
                        
        # 2. 遍历 slides/_rels/ 找 comment 的对应关系
        slide_comment_map = {} # comment1.xml -> slide1.xml
        for name in namelist:
            if name.startswith("ppt/slides/_rels/") and name.endswith(".xml.rels"):
                slide_filename = name.split("/")[-1].replace(".rels", "") # slide1.xml
                with zf.open(name) as f:
                    tree = etree.parse(f)
                    root = tree.getroot()
                    for rel in root.findall(f".//{{{_NS_RELS}}}Relationship"):
                        target = rel.get("Target", "")
                        if "comments/comment" in target:
                            comment_filename = target.split("/")[-1] # comment1.xml
                            slide_comment_map[comment_filename] = slide_filename
                            
        # 3. 解析 comment*.xml
        for name in namelist:
            if name.startswith("ppt/comments/comment") and name.endswith(".xml"):
                comment_filename = name.split("/")[-1]
                slide_name = slide_comment_map.get(comment_filename)
                if not slide_name:
                    continue
                    
                with zf.open(name) as f:
                    tree = etree.parse(f)
                    root = tree.getroot()
                    slide_comments = []
                    for cm in root.findall(f".//{{{_NS_P}}}cm"):
                        a_id = cm.get("authorId")
                        author_name = authors.get(a_id, "Unknown")
                        text_elem = cm.find(f".//{{{_NS_P}}}text")
                        if text_elem is not None and text_elem.text:
                            slide_comments.append(f"{author_name}: {text_elem.text.strip()}")
                            
                    if slide_comments:
                        comments_by_slide[slide_name] = slide_comments
                        
        return comments_by_slide
    except Exception:
        return {}


def _build_pseudo_table(elements_group):
    """
    将同一 Y-Band 内的散落文本框转置合并为 Markdown 表格。
    引入 X 轴相交聚类算法：将水平方向上有重叠的文本框编为同一列，垂直连接内容，然后再转置。
    """
    if not elements_group or len(elements_group) <= 1:
        return None
    
    # 1. 按 X 坐标聚类，生成列 (Column Grouping)
    # 使用中心点距离而不是严格的相交，或者去除很大的 width 带来的干扰。
    # 比如很多文本框的 width 非常大，但实际文字只在左边。为了准确，我们只用 x0 来聚类（比如相差不到 500000 EMU 的算同一列）
    elements_group.sort(key=lambda e: e['x0'])
    cols_groups = [] # [{'min_x': min_x, 'max_x': max_x, 'elems': [...]}]
    
    # 设定列宽容差 (约 0.5 英寸)
    X_TOLERANCE = 500000 
    
    for elem in elements_group:
        x0 = elem['x0']
        # 为了防止很宽的空文本框导致所有列合并，只用 x0 和一个预设容差来判断列归属
        x1 = x0 + X_TOLERANCE
        
        merged = False
        for cg in cols_groups:
            # 判断当前元素的 x0 是否落在已有列的范围内容差内
            if not (x1 < cg['min_x'] or x0 > cg['max_x']):
                cg['elems'].append(elem)
                cg['min_x'] = min(cg['min_x'], x0)
                cg['max_x'] = max(cg['max_x'], x1)
                merged = True
                break
        if not merged:
            cols_groups.append({'min_x': x0, 'max_x': x1, 'elems': [elem]})
            
    if not cols_groups or len(cols_groups) <= 1:
        return None
        
    # 2. 组装每列的行数据
    columns = []
    for cg in cols_groups:
        cg['elems'].sort(key=lambda e: e['y0']) # 每一列内从上到下排序
        col_texts = []
        for elem in cg['elems']:
            lines = [line.replace('|', '\\|').strip() for line in elem['content'].strip().split('\n')]
            # 移除空行
            lines = [l for l in lines if l]
            if lines:
                col_texts.extend(lines)
        if col_texts:
            # 属于同一个单元格的内容，用 ; 连接
            columns.append([" ; ".join(col_texts)])
        else:
            columns.append([" "])
            
    if not columns or len(columns) <= 1:
        return None
        
    # 因为所有列都合并为 1 行，直接提取表头即可
    headers = [col[0] for col in columns]
    
    md_lines = []
    col_count = len(columns)
    
    header_str = "| " + " | ".join(headers) + " |"
    sep_str = "|" + "|".join(["---"] * col_count) + "|"
    
    md_lines.append(header_str)
    md_lines.append(sep_str)
    
    return "\n".join(md_lines)


def _parse_slide_concurrent(slide_idx, slide, comments_dict) -> str:
    """内部并行处理单张幻灯片"""
    elements = []
    
    def _extract_shape_text(shape, abs_x=0, abs_y=0):
        x0 = abs_x + (getattr(shape, "left", 0) or 0)
        y0 = abs_y + (getattr(shape, "top", 0) or 0)
        x1 = x0 + (getattr(shape, "width", 0) or 0)
        y1 = y0 + (getattr(shape, "height", 0) or 0)
        
        if hasattr(shape, "shape_type") and shape.shape_type == 6: # GROUP
            for child in shape.shapes:
                _extract_shape_text(child, abs_x=x0, abs_y=y0)
            return

        if hasattr(shape, "has_table") and shape.has_table:
            table = shape.table
            table_lines = []
            for row_idx, row in enumerate(table.rows):
                row_data = []
                for cell in row.cells:
                    cell_text = cell.text_frame.text.strip().replace('\n', ' ').replace('|', '\\|') if cell.text_frame else ""
                    row_data.append(cell_text)
                table_lines.append("| " + " | ".join(row_data) + " |")
                if row_idx == 0:
                    table_lines.append("|" + "|".join(["---"] * len(row_data)) + "|")
            if table_lines:
                elements.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'content': "\n".join(table_lines), 'type': 'TABLE'})
                
        elif hasattr(shape, "has_text_frame") and shape.has_text_frame:
            lines = []
            for p in shape.text_frame.paragraphs:
                p_text = p.text.strip()
                if p_text:
                    lines.append(p_text)
            
            if lines:
                if len(lines) == 1:
                    elements.append({'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1, 'content': lines[0], 'type': 'TEXT', 'shape': shape})
                else:
                    line_height = (y1 - y0) / len(lines)
                    for i, line in enumerate(lines):
                        l_y0 = y0 + i * line_height
                        l_y1 = l_y0 + line_height
                        elements.append({'x0': x0, 'y0': l_y0, 'x1': x1, 'y1': l_y1, 'content': line, 'type': 'TEXT', 'shape': shape})
                
    for shape in slide.shapes:
        _extract_shape_text(shape)
        
    # Title 提取逻辑对齐：优先官方 title，否则取左上角最大块
    title_text = ""
    if slide.shapes.title and slide.shapes.title.text.strip():
        title_text = slide.shapes.title.text.strip()
        # 将官方 title 从 elements 中剔除以防重复
        elements = [e for e in elements if e.get('shape') != slide.shapes.title]
    elif elements:
        elements.sort(key=lambda e: (e['y0'], e['x0']))
        for e in elements:
            if e['type'] == 'TEXT':
                lines = e['content'].split('\n')
                title_text = lines[0][:50]
                if len(lines) == 1:
                    elements.remove(e)
                else:
                    e['content'] = '\n'.join(lines[1:]).strip()
                break
                
    md_lines = []
    if title_text:
        md_lines.append(f"## {title_text}")
    else:
        md_lines.append(f"## Slide {slide_idx}")

    # 分组构建表格 (二维边界碰撞/Y-Overlap 连通图算法)
    # 将 y 轴方向有重叠的文本框划为同一组 (Row Band)
    y_groups = []  # 列表中每个元素为一个包含 dict 的 list: [{'min_y': min_y, 'max_y': max_y, 'elems': []}]
    
    # 提前分离原生表格（原生表格自成一组，不参与合并）
    native_tables = [e for e in elements if e['type'] == 'TABLE']
    text_elements = [e for e in elements if e['type'] != 'TABLE']
    
    # 按 Y 坐标从上到下排序
    text_elements.sort(key=lambda e: e['y0'])
    
    for elem in text_elements:
        y0, y1 = elem['y0'], elem.get('y1', elem['y0'])
        
        # 寻找是否有相交的组
        merged = False
        for g in y_groups:
            # 判断相交条件：重叠部分至少要达到较小者高度的 20%
            overlap = max(0, min(y1, g['max_y']) - max(y0, g['min_y']))
            min_height = min(y1 - y0, g['max_y'] - g['min_y'])
            if overlap > min_height * 0.2:
                g['elems'].append(elem)
                g['min_y'] = min(g['min_y'], y0)
                g['max_y'] = max(g['max_y'], y1)
                merged = True
                break
        if not merged:
            y_groups.append({'min_y': y0, 'max_y': y1, 'elems': [elem]})
            
    # 将原生表格也放入组中参与最终排序
    for tbl in native_tables:
        y0, y1 = tbl['y0'], tbl.get('y1', tbl['y0'])
        y_groups.append({'min_y': y0, 'max_y': y1, 'elems': [tbl]})
        
    # 按整组的平均/最小 Y 坐标对所有组进行排序
    y_groups.sort(key=lambda g: g['min_y'])
    
    parsed_parts = []
    
    for g in y_groups:
        group_elems = g['elems']
        if len(group_elems) == 1:
            elem = group_elems[0]
            parsed_parts.append({'type': 'text', 'content': elem['content']})
            if elem['type'] == 'TABLE':
                has_table = True
            continue
            
        table_row = _build_pseudo_table(group_elems)
        if table_row:
            parsed_parts.append({'type': 'table', 'content': table_row})
            has_table = True
        else:
            # 降级：如果不能构成表格，就按 X 排序从左到右输出
            group_elems.sort(key=lambda e: e.get('x0', 0))
            for elem in group_elems:
                parsed_parts.append({'type': 'text', 'content': elem['content']})

    # 同构表格粘合器 (Table Merger)
    merged_parts = []
    for part in parsed_parts:
        if part['type'] == 'table' and merged_parts and merged_parts[-1]['type'] == 'table':
            prev_table = merged_parts[-1]['content']
            curr_table = part['content']
            
            prev_lines = prev_table.strip().split('\n')
            curr_lines = curr_table.strip().split('\n')
            
            # 至少包含 header 和 separator
            if len(prev_lines) >= 2 and len(curr_lines) >= 2:
                prev_cols = prev_lines[1].count('|') - 1
                curr_cols = curr_lines[1].count('|') - 1
                
                if prev_cols > 0 and curr_cols > 0:
                    max_cols = max(prev_cols, curr_cols)
                    
                    # 补全 prev_lines
                    if prev_cols < max_cols:
                        diff = max_cols - prev_cols
                        for i in range(len(prev_lines)):
                            if i != 1:
                                prev_lines[i] = prev_lines[i] + "   |" * diff
                            else:
                                prev_lines[i] = prev_lines[i] + "---|" * diff
                                
                    # 补全 curr_lines
                    if curr_cols < max_cols:
                        diff = max_cols - curr_cols
                        for i in range(len(curr_lines)):
                            if i != 1:
                                curr_lines[i] = curr_lines[i] + "   |" * diff
                            else:
                                curr_lines[i] = curr_lines[i] + "---|" * diff
                                
                    # 合并：保留 curr_lines[0] 作为数据行追加，并追加剩余数据行(如果有)
                    merged_table = "\n".join(prev_lines + [curr_lines[0]] + curr_lines[2:])
                    merged_parts[-1]['content'] = merged_table
                    continue
        merged_parts.append(part)

    for part in merged_parts:
        md_lines.append(part['content'])
        md_lines.append("")

    # 提取备注，并过滤占位符
    if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes and notes != "Click to add text" and notes != "Click here to add text":
            md_lines.append(f"**Speaker Notes**: {notes}")
            md_lines.append("")

    # 附加批注
    slide_xml_name = f"slide{slide_idx}.xml"
    slide_comments = comments_dict.get(slide_xml_name, [])
    for c in slide_comments:
        md_lines.append(f"[comment: {c}]")
        
    return "\n".join(md_lines) + "\n"


def _try_extract_pptx_text(raw_bytes: bytes) -> Optional[str]:
    """尝试用 python-pptx 提取 PPTX 文本并转为 Markdown 格式。"""
    try:
        import pptx # type: ignore
    except ImportError:
        return None
        
    try:
        # 提取批注字典
        comments_dict = _extract_comments(raw_bytes)
        
        prs = pptx.Presentation(io.BytesIO(raw_bytes))
        
        # 按照用户要求加入多线程并发提取（针对页数较多的幻灯片提升效率）
        # 因 PPTX 对象存在潜在的 COM 内部耦合，我们采用最大 worker 限制，以兼顾安全和速度。
        results = {}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_parse_slide_concurrent, i, slide, comments_dict): i 
                for i, slide in enumerate(prs.slides, 1)
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    results[idx] = f"## Slide {idx}\n[解析错误: {e}]\n"
                    
        # 按幻灯片原本顺序重组
        final_md = []
        for i in range(1, len(prs.slides) + 1):
            if i in results:
                final_md.append(results[i])
                
        return "\n".join(final_md)
    except Exception as e:
        import traceback
        import sys
        traceback.print_exc(file=sys.stderr)
        return None


def read_one(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
    download_timeout: int = 15,
) -> dict:
    """读取单个 PPTX 文件。"""
    drive_service = build("drive", "v3", credentials=creds, cache_discovery=False)

    try:
        raw_bytes = download_file(drive_service, file_id, supports_all_drives, timeout=download_timeout)
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": _MIME_PPTX,
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

    text = _try_extract_pptx_text(raw_bytes)
    if text is not None:
        return {
            "file_id": file_id,
            "mime_type": _MIME_PPTX,
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
            "mime_type": _MIME_PPTX,
            "title": title,
            "content": None,
            "structured_md": None,
            "content_type": "bytes",
            "raw_bytes": raw_bytes,
            "error": "无法解析 PPTX 内容（可能由于缺失 python-pptx 库，或文件损坏）",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(
    file_ids: list[str],
    creds,
    supports_all_drives: bool = True,
) -> list[dict]:
    """串行批量读取多个 PPTX 文档。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_PPTX,
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
    """并发批量读取多个 PPTX 文档。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_PPTX,
                "error": str(e),
                "content": None,
                "structured_md": None,
            }

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(_read, file_ids))
    return results


def read_chunks(
    file_id: str,
    creds,
    supports_all_drives: bool = True,
    max_chunk_tokens: int = 800,
    download_timeout: int = 15,
) -> list:
    """读取 PPTX 文档并切分为 chunks。"""
    from chunkers import markdown_to_chunks, GoogleChunk
    result = read_one(file_id=file_id, creds=creds, supports_all_drives=supports_all_drives, download_timeout=download_timeout)
    if result.get("error") or not result.get("structured_md"):
        return []
    title = result.get("title", "")
    file_url = f"https://drive.google.com/file/d/{file_id}/view"
    
    md = result["structured_md"]
    if title and not md.startswith("#"):
        md = f"# {title}\n\n{md}"
        
    return markdown_to_chunks(
        markdown=md,
        file_id=file_id,
        file_title=title,
        file_url=file_url,
        source_tag="google:pptx",
        max_chunk_tokens=max_chunk_tokens,
        metadata={"mime_type": _MIME_PPTX, "drive_size": result.get("drive_size")},
    )
