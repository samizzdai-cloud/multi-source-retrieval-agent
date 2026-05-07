"""Google Slides 专用读取器。

通过 Slides API 读取幻灯片文本内容。
支持并发读取、矩阵计算空间排序、备注提取、以及删除线文本过滤。
新增 read_chunks() 返回标准 GoogleChunk 列表。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from typing import List
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.chunkers import GoogleChunk, ChunkContentType, estimate_tokens

_MIME_SLIDES = "application/vnd.google-apps.presentation"

# ---------------------------------------------------------------------------
# 内部辅助函数：矩阵与坐标计算
# ---------------------------------------------------------------------------

def _apply_transform(transform, x, y):
    """
    应用 AffineTransform 到点 (x,y)。
    transform 格式: {scaleX, scaleY, shearX, shearY, translateX, translateY}
    """
    if not transform:
        return x, y
    sx = transform.get('scaleX', 1)
    sy = transform.get('scaleY', 1)
    shx = transform.get('shearX', 0)
    shy = transform.get('shearY', 0)
    tx = transform.get('translateX', 0)
    ty = transform.get('translateY', 0)
    
    nx = sx * x + shx * y + tx
    ny = sy * y + shy * x + ty
    return nx, ny

def _calculate_bounding_box(size, transform):
    """
    计算 Bounding Box: (x0, y0, x1, y1)
    """
    # 默认 size 0
    w = 0
    h = 0
    if size:
        w = size.get('width', {}).get('magnitude', 0)
        h = size.get('height', {}).get('magnitude', 0)
    
    # 左上角 (0, 0)
    x0, y0 = _apply_transform(transform, 0, 0)
    # 右下角 (w, h)
    x1, y1 = _apply_transform(transform, w, h)
    
    # 考虑 scale 可能是负数，确保 x0 < x1, y0 < y1
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

# ---------------------------------------------------------------------------
# 解析函数
# ---------------------------------------------------------------------------

def _parse_text_elements(text_elements, nesting_level_indent="  "):
    """
    解析文本元素数组，过滤删除线，处理列表缩进。
    返回解析后的纯文本字符串。
    """
    parts = []
    for te in text_elements:
        # 处理段落缩进 (Bullet)
        paragraph_marker = te.get('paragraphMarker')
        if paragraph_marker:
            bullet = paragraph_marker.get('bullet')
            if bullet:
                level = bullet.get('nestingLevel', 0)
                # 根据层级添加缩进
                parts.append(f"\n{nesting_level_indent * level}- ")
            continue

        text_run = te.get('textRun')
        if text_run:
            style = text_run.get('style', {})
            # 过滤删除线
            if style.get('strikethrough') is True:
                continue
            
            content = text_run.get('content', '')
            
            # 简单的加粗还原
            # if style.get('bold') is True:
            #     content = f"**{content.strip()}** "

            parts.append(content)
            
    # 清理多余空行
    return "".join(parts).strip()


def _parse_element(element, parent_transform=None):
    """
    解析单个 PageElement，返回扁平化的包含绝对坐标和内容的字典列表。
    [{ 'x0': x0, 'y0': y0, 'content': str, 'type': str }]
    """
    parsed_elements = []
    
    # 获取自身的 transform 和 size
    transform = element.get('transform', {})
    
    # 如果有 parent_transform，这里需要做矩阵乘法。为简化实现，暂简单相加 translate
    # 注意：更严谨的实现需要完整的 3x3 矩阵乘法
    # 这里我们只做简单的一阶累加（假设组没有旋转）
    if parent_transform:
        transform['translateX'] = transform.get('translateX', 0) + parent_transform.get('translateX', 0)
        transform['translateY'] = transform.get('translateY', 0) + parent_transform.get('translateY', 0)
        transform['scaleX'] = transform.get('scaleX', 1) * parent_transform.get('scaleX', 1)
        transform['scaleY'] = transform.get('scaleY', 1) * parent_transform.get('scaleY', 1)

    size = element.get('size', {})
    x0, y0, x1, y1 = _calculate_bounding_box(size, transform)
    
    # 1. 形状与文本
    shape = element.get('shape')
    if shape:
        placeholder = shape.get('placeholder', {})
        ph_type = placeholder.get('type', '')
        
        text = shape.get('text', {})
        if text:
            text_content = _parse_text_elements(text.get('textElements', []))
            if text_content:
                lines = [l.strip() for l in text_content.strip().split('\n') if l.strip()]
                if len(lines) <= 1:
                    parsed_elements.append({
                        'x0': x0, 'y0': y0,
                        'x1': x1, 'y1': y1,
                        'area': (x1-x0)*(y1-y0),
                        'content': text_content,
                        'type': ph_type or 'SHAPE',
                        'shape_id': element.get('objectId')
                    })
                else:
                    line_height = (y1 - y0) / len(lines)
                    for i, line in enumerate(lines):
                        l_y0 = y0 + i * line_height
                        l_y1 = l_y0 + line_height
                        parsed_elements.append({
                            'x0': x0, 'y0': l_y0,
                            'x1': x1, 'y1': l_y1,
                            'area': (x1-x0)*(l_y1-l_y0),
                            'content': line,
                            'type': ph_type or 'SHAPE',
                            'shape_id': element.get('objectId')
                        })
        return parsed_elements

    # 2. 组 (递归)
    group = element.get('elementGroup')
    if group:
        for child in group.get('children', []):
            parsed_elements.extend(_parse_element(child, parent_transform=transform))
        return parsed_elements

    # 3. 表格
    table = element.get('table')
    if table:
        table_md = []
        rows = table.get('tableRows', [])
        for i, row in enumerate(rows):
            row_texts = []
            for cell in row.get('tableCells', []):
                cell_text = _parse_text_elements(cell.get('text', {}).get('textElements', []))
                # 转义管道符和换行
                cell_text = cell_text.replace('\n', '<br>').replace('|', '\\|')
                row_texts.append(cell_text)
            
            table_md.append("| " + " | ".join(row_texts) + " |")
            # 补分隔符
            if i == 0:
                table_md.append("|" + "|".join(["---"] * len(row_texts)) + "|")
        
        if table_md:
            parsed_elements.append({
                'x0': x0, 'y0': y0,
                'x1': x1, 'y1': y1,
                'area': (x1-x0)*(y1-y0),
                'content': "\n".join(table_md),
                'type': 'TABLE'
            })
        return parsed_elements
    
    # 4. 图表 / 图片 (降级处理)
    # 反馈要求：一般我们图片内容忽略
    # 如果是 SheetsChart 还是可以视情况保留（此处统一只保留 Chart，忽略纯图片）
    title = element.get('title', '')
    desc = element.get('description', '')
    alt_text = f"{title} - {desc}".strip(" -")
    
    if element.get('sheetsChart'):
        parsed_elements.append({
            'x0': x0, 'y0': y0,
            'x1': x1, 'y1': y1,
            'area': (x1-x0)*(y1-y0),
            'content': f"[SheetsChart: {alt_text}]" if alt_text else "[SheetsChart]",
            'type': 'CHART'
        })

    return parsed_elements


def _build_pseudo_table(elements_group):
    """
    将同一 Y-Band 内的散落文本框转置合并为 Markdown 表格。
    引入 X 轴相交聚类算法：将水平方向上有重叠的文本框编为同一列，垂直连接内容，然后再转置。
    """
    if not elements_group or len(elements_group) <= 1:
        return None
    
    # 1. 按 X 坐标聚类，生成列 (Column Grouping)
    elements_group.sort(key=lambda e: e['x0'])
    cols_groups = [] # [{'min_x': min_x, 'max_x': max_x, 'elems': [...]}]
    X_TOLERANCE = 500000 
    for elem in elements_group:
        x0 = elem['x0']
        x1 = x0 + X_TOLERANCE
        merged = False
        for cg in cols_groups:
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

def _read_one_slide_chunks(service, file_id: str) -> List[GoogleChunk]:
    """内部：通过 Slides API 读取单个演示文稿，返回 GoogleChunk 列表。"""
    import time, logging
    _logger = logging.getLogger(__name__)
    _t0 = time.perf_counter()
    presentation = service.presentations().get(presentationId=file_id).execute()
    _api_ms = (time.perf_counter() - _t0) * 1000
    title = presentation.get("title", "")
    pres_id = presentation.get("presentationId", "")

    _t1 = time.perf_counter()
    chunks: List[GoogleChunk] = []

    for i, slide in enumerate(presentation.get("slides", []), start=1):
        slide_id = slide.get('objectId', '')

        # 获取备注
        notes_text = ""
        slide_props = slide.get('slideProperties', {})
        notes_page = slide_props.get('notesPage', {})
        if notes_page:
            notes_obj_id = notes_page.get('notesProperties', {}).get('speakerNotesObjectId')
            for elem in notes_page.get('pageElements', []):
                if elem.get('objectId') == notes_obj_id:
                    shape_text = elem.get('shape', {}).get('text', {}).get('textElements', [])
                    notes_text = _parse_text_elements(shape_text)
                    break

        # 提取当前页的排版元素
        elements = []
        for element in slide.get('pageElements', []):
            elements.extend(_parse_element(element))

        # 提取标题
        slide_title = "无标题页面"
        title_elem = None
        for elem in elements:
            if elem['type'] in ('TITLE', 'CENTERED_TITLE'):
                title_elem = elem
                break

        if title_elem:
            slide_title = title_elem['content'].replace('\n', ' ')
        elif elements:
            elements.sort(key=lambda e: (e['y0'], e['x0']))
            for elem in elements:
                if elem['content'] and not elem['content'].startswith('['):
                    lines = elem['content'].split('\n')
                    slide_title = lines[0][:50] + "..."
                    if len(lines) == 1:
                        elements.remove(elem)
                    else:
                        elem['content'] = '\n'.join(lines[1:]).strip()
                    break

        # 组装 Markdown content（简洁版，metadata 进字段）
        file_url = f"https://docs.google.com/presentation/d/{pres_id}/edit#slide=id.{slide_id}"
        chunk_parts = []
        if slide_title:
            chunk_parts.append(f"## {slide_title}")
        else:
            chunk_parts.append(f"## Slide {i}")

        has_table = False
        
        # 移除 title 元素，避免重复输出
        work_elems = [e for e in elements if e != title_elem and e['content']]

        # 分组构建表格 (二维边界碰撞/Y-Overlap 连通图算法)
        # 将 y 轴方向有重叠的文本框划为同一组 (Row Band)
        y_groups = []  # [{'min_y': min_y, 'max_y': max_y, 'elems': []}]
        
        native_tables = [e for e in work_elems if e['type'] == 'TABLE']
        text_elements = [e for e in work_elems if e['type'] != 'TABLE']
        
        # 按 Y 坐标从上到下排序
        text_elements.sort(key=lambda e: e['y0'])
        
        for elem in text_elements:
            y0 = elem.get('y0', 0)
            # slide API 有时只有 y0, y1 没有提取的话，估算一个高度
            area = elem.get('area', 0)
            w = elem.get('x1', elem.get('x0', 0)) - elem.get('x0', 0)
            h = area / w if w > 0 and area > 0 else 200000
            y1 = elem.get('y1', y0 + h)
            
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
                
        for tbl in native_tables:
            y0 = tbl.get('y0', 0)
            y1 = tbl.get('y1', y0)
            y_groups.append({'min_y': y0, 'max_y': y1, 'elems': [tbl]})
            
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
            chunk_parts.append(part['content'])
            chunk_parts.append("")

        if notes_text and notes_text != "Click to add text" and notes_text != "Click here to add text":
            chunk_parts.append(f"\n**Speaker Notes**: {notes_text}")
            chunk_parts.append("")

        content = "\n".join(chunk_parts)
        chunks.append(GoogleChunk(
            chunk_id=f"google:slide:{file_id}:{i}",
            file_id=file_id,
            file_title=title,
            file_url=file_url,
            heading_path=slide_title,
            content_type=ChunkContentType.TABLE_MD if has_table else ChunkContentType.TEXT,
            content=content,
            token_count=estimate_tokens(content),
            has_table=has_table,
            metadata={
                "mime_type": _MIME_SLIDES,
                "slide_id": slide_id,
                "slide_index": i,
            },
        ))

    return chunks


def _read_one_slide(service, file_id: str) -> tuple[str, str]:
    """内部：通过 Slides API 读取单个演示文稿，返回拼接 Markdown + title。
    向后兼容：底层调用 _read_one_slide_chunks。
    """
    chunks = _read_one_slide_chunks(service, file_id)
    if not chunks:
        return "", ""
    title = chunks[0].file_title
    md_parts = []
    for ch in chunks:
        md_parts.append("---")
        md_parts.append("chunk_metadata:")
        md_parts.append(f'  chunk_id: "{ch.chunk_id}"')
        md_parts.append(f'  file_url: "{ch.file_url}"')
        md_parts.append(f'  title: "{title}"')
        md_parts.append("---\n")
        md_parts.append(ch.content)
    return "\n\n=====\n\n".join(md_parts), title


def read_one(file_id: str, creds, supports_all_drives: bool = True, native_api_timeout: int = 10) -> dict:
    """读取单个 Google Slides 演示文稿。"""
    import httplib2
    http_client = httplib2.Http(timeout=native_api_timeout)
    if hasattr(creds, "authorize"):
        authed_http = creds.authorize(http_client)
    else:
        import google_auth_httplib2
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http_client)
    slides_service = build("slides", "v1", http=authed_http, cache_discovery=False)
    try:
        text, title = _read_one_slide(slides_service, file_id)
        return {
            "file_id": file_id,
            "mime_type": _MIME_SLIDES,
            "title": title,
            "content": text,
            "structured_md": text,
            "content_type": "text",
            "raw_bytes": None,
            "error": None,
            "mode": "full",
            "drive_size": None,
            "skip_reason": None,
        }
    except HttpError as e:
        return {
            "file_id": file_id,
            "mime_type": _MIME_SLIDES,
            "title": "",
            "content": None,
            "structured_md": None,
            "content_type": None,
            "raw_bytes": None,
            "error": f"API 调用失败: {e}",
            "mode": None,
            "drive_size": None,
            "skip_reason": None,
        }


def read_batch(file_ids: list[str], creds, supports_all_drives: bool = True) -> list[dict]:
    """串行批量读取多个 Google Slides。"""
    results = []
    for fid in file_ids:
        try:
            results.append(read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives))
        except Exception as e:
            results.append({
                "file_id": fid,
                "mime_type": _MIME_SLIDES,
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
    """并发批量读取多个 Google Slides。"""
    def _read(fid):
        try:
            return read_one(file_id=fid, creds=creds, supports_all_drives=supports_all_drives)
        except Exception as e:
            return {
                "file_id": fid,
                "mime_type": _MIME_SLIDES,
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
    native_api_timeout: int = 10,
) -> List[GoogleChunk]:
    """读取 Google Slides 并切分为 chunks（每页一个 chunk）。"""
    import httplib2
    http_client = httplib2.Http(timeout=native_api_timeout)
    if hasattr(creds, "authorize"):
        authed_http = creds.authorize(http_client)
    else:
        import google_auth_httplib2
        authed_http = google_auth_httplib2.AuthorizedHttp(creds, http=http_client)
    slides_service = build("slides", "v1", http=authed_http, cache_discovery=False)
    try:
        return _read_one_slide_chunks(slides_service, file_id)
    except HttpError:
        return []
