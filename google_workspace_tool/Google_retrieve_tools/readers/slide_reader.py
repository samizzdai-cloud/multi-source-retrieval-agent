"""Google Slides 专用读取器。

通过 Slides API 读取幻灯片文本内容。
支持并发读取、矩阵计算空间排序、备注提取、以及删除线文本过滤。
"""

import time
from concurrent.futures import ThreadPoolExecutor
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

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
                parsed_elements.append({
                    'x0': x0, 'y0': y0,
                    'area': (x1-x0)*(y1-y0),
                    'content': text_content,
                    'type': ph_type or 'SHAPE'
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
            'area': (x1-x0)*(y1-y0),
            'content': f"[SheetsChart: {alt_text}]" if alt_text else "[SheetsChart]",
            'type': 'CHART'
        })

    return parsed_elements


def _build_pseudo_table(elements_group):
    """
    尝试将按 Y 对齐但具有多个离散 X 位置的元素合并为真正的多行 Markdown 表格。
    通过拆分各列内元素的换行符，将原先同一列但跨越多行的内容对应到表格的多行中。
    如果只有一列或者所有列都只有极少内容，则不作为表格返回，让上层按普通元素处理。
    """
    if not elements_group or len(elements_group) <= 1:
        return None
    
    # 将这些文本块按 X 排序，确保列顺序正确 (从左到右)
    elements_group.sort(key=lambda e: e['x0'])
    
    # 将每个列的内容按照换行符拆分成独立的列表
    columns = []
    for elem in elements_group:
        # 移除前导和后缀的无关空白，然后按换行符切分
        lines = [line.replace('|', '\\|').strip() for line in elem['content'].strip().split('\n')]
        # 过滤掉完全空白的行，但尽量保持原始节奏。为简化，这里直接保留空行以便对齐。
        # 如果末尾有连续空行，去掉。
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            columns.append(lines)
            
    if not columns:
        return None
        
    # 如果实际上并没有形成真正的表格行/列关系（例如单列多行），这里避免强行转表格
    if len(columns) <= 1:
        return None
        
    # 提取表头：将各列的第一行作为表头提取，并从各列的数据中移除第一行
    headers = []
    for col in columns:
        headers.append(col[0] if col else " ")
        if col:
            col.pop(0)
            
    # 计算除去表头后需要多少行：找出最长的列
    max_rows = max(len(col) for col in columns) if columns else 0
    
    # 补齐所有列到同样的长度
    for col in columns:
        while len(col) < max_rows:
            col.append("")
            
    # 转置：将列数据转置为行数据
    # rows[i] 包含了第 i 行的所有列内容
    rows = []
    for i in range(max_rows):
        row_data = [col[i] for col in columns]
        rows.append(row_data)

    # 构建表头和表格行
    md_lines = []
    col_count = len(columns)
    
    header_str = "| " + " | ".join(headers) + " |"
    sep_str = "|" + "|".join(["---"] * col_count) + "|"
    
    md_lines.append(header_str)
    md_lines.append(sep_str)
    
    for row in rows:
        row_str = "| " + " | ".join(row) + " |"
        md_lines.append(row_str)
    
    return "\n".join(md_lines)

def _read_one_slide(service, file_id: str) -> tuple[str, str]:
    """内部：通过 Slides API 读取单个演示文稿，实现 Vibe Coding Pipeline。"""
    # 环节一：一次性拉取整个 JSON
    presentation = service.presentations().get(presentationId=file_id).execute()
    title = presentation.get("title", "")
    pres_id = presentation.get("presentationId", "")
    
    # 预处理所有 Notes Pages，建立 map: {objectId: page_data}
    notes_pages = {}
    for note_master in presentation.get('notesMaster', []):  # NotesMaster 如果需要
        pass
    
    # TODO: 目前 presentations.get 默认会返回 slides。
    # 真正的备注页(notesPage)实际关联在各个 slide 内部。
    
    chunks = []
    
    for i, slide in enumerate(presentation.get("slides", []), start=1):
        slide_id = slide.get('objectId', '')
        
        # 环节二：获取备注
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
        
        # 环节三 & 四：提取当前页的排版元素
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
            # 回退：如果没有 TITLE，优先寻找左上角面积较大/位置最高的文本作为标题
            elements.sort(key=lambda e: (e['y0'], e['x0']))
            # 取最上面的非空文本块
            for elem in elements:
                if elem['content'] and not elem['content'].startswith('['):
                    slide_title = elem['content'].split('\n')[0][:50] + "..." # 取第一行缩略
                    break

        # 环节三：按 Y 近似分组并排序 (容差 150000 EMU，约 15 像素)
        # 对于本身就是 TABLE 的元素，不参与 Y 轴近似分组伪表格拼接，独立成组
        Y_TOLERANCE = 150000 
        y_groups = {}
        table_counter = 0  # 避免同 Y 坐标的多个原始 TABLE 相互覆盖
        for elem in elements:
            if elem['type'] in ('TITLE', 'CENTERED_TITLE') and elem == title_elem:
                continue # 跳过主标题
            if not elem['content']:
                continue
                
            if elem['type'] == 'TABLE':
                # 为确保独立的 TABLE 不被分入同个 Y 分组拼在一起，我们分配特殊的组别键
                group_key = f"table_{table_counter}_{elem['y0']}"
                table_counter += 1
            else:
                # 正常的文本块
                group_key = elem['y0'] // Y_TOLERANCE
                
            if group_key not in y_groups:
                y_groups[group_key] = []
            y_groups[group_key].append(elem)
            
        # 根据 Y 键排序
        # 为确保原本空间坐标不被打乱，按 Y 的坐标（数字部分）进行排序
        def get_y_val(key):
            if isinstance(key, str) and key.startswith("table_"):
                return float(key.split('_')[-1]) // Y_TOLERANCE
            return float(key)
            
        sorted_y_keys = sorted(y_groups.keys(), key=get_y_val)

        # 环节五：组装最终 Markdown RAG Chunk 模板
        file_url = f"https://docs.google.com/presentation/d/{pres_id}/edit#slide=id.{slide_id}"
        chunk_id = f"{pres_id}_slide_{i}"

        chunk_parts = []
        chunk_parts.append("---")
        chunk_parts.append("chunk_metadata:")
        chunk_parts.append(f"  chunk_id: \"{chunk_id}\"")
        chunk_parts.append(f"  file_url: \"{file_url}\"")
        chunk_parts.append(f"  title: \"{title}\"")
        chunk_parts.append("---\n")
        
        chunk_parts.append(f"# {slide_title}\n")
        
        if notes_text:
            chunk_parts.append("## 🎤 核心摘要与演讲者备注 (Speaker Notes)")
            chunk_parts.append(notes_text)
            chunk_parts.append("\n---\n")
            
        chunk_parts.append("## 🖼️ Elements Position")
        
        # 遍历 Y 分组处理内容
        for y_key in sorted_y_keys:
            group_elems = y_groups[y_key]
            
            # 单一元素组，或者本身是 TABLE (被单独分配了特殊组别的)
            if len(group_elems) == 1:
                elem = group_elems[0]
                x_pos = int(elem['x0'] / 10000)
                y_pos = int(elem['y0'] / 10000)
                chunk_parts.append(f"**[position: x:{x_pos}, y:{y_pos}]**")
                chunk_parts.append(elem['content'])
                chunk_parts.append("")
                continue
                
            # 若同一 Y 轴近似区域有多个 X 分布的普通文本块，尝试组装为伪表格
            table_row = _build_pseudo_table(group_elems)
            if table_row:
                y_pos = int(group_elems[0]['y0'] / 10000)
                chunk_parts.append(f"**[position: y:{y_pos}]**")
                chunk_parts.append(table_row)
                chunk_parts.append("")
            else:
                # 如果没有成功构建表格，则回退分别输出每个元素块
                for elem in group_elems:
                    x_pos = int(elem['x0'] / 10000)
                    y_pos = int(elem['y0'] / 10000)
                    chunk_parts.append(f"**[position: x:{x_pos}, y:{y_pos}]**")
                    chunk_parts.append(elem['content'])
                    chunk_parts.append("")
            
        chunks.append("\n".join(chunk_parts))

    return "\n\n=====\n\n".join(chunks), title


def read_one(file_id: str, creds, supports_all_drives: bool = True) -> dict:
    """读取单个 Google Slides 演示文稿。"""
    slides_service = build("slides", "v1", credentials=creds, cache_discovery=False)
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
