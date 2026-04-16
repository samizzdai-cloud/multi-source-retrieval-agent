"""
md_parser.py — 飞书文档 → Markdown 转换器

将飞书 Docx Block 树递归转换为 Markdown 文本。
支持文本、标题、列表、引用、代码块、Sheet 嵌入表格等 Block 类型。
依赖 FeishuClient 读取嵌入的 Sheet 数据。
"""

from typing import List, Dict
from ..client import FeishuClient, APIError
from .sheet_utils import parse_sheet_token, build_range_notation, parse_grid_to_table
from .utils import FileError
import lark_oapi as lark


class ParseError(Exception):
    """Block 解析错误"""
    def __init__(self, block_id: str, reason: str):
        self.block_id = block_id
        self.reason = reason
        super().__init__(f"Parse Error at block {block_id}: {reason}")


class MarkdownParser:
    """Markdown 解析器"""
    
    def __init__(self, feishu_client: FeishuClient):
        """初始化解析器
        
        Args:
            feishu_client: 飞书客户端实例
        """
        self.client = feishu_client
        # 文档标题：在 parse_document() 执行后由 Page block 自动填充
        self.document_title: str = None
        # 有序列表动态序号计数器（每次 parse_document 时重置为 0）
        self._ordered_list_counter: int = 0
        # 当前文档的 blocks_dict，parse_document 执行后填充，供嵌套列表查父块序号用
        self._blocks_dict: Dict = {}
        # type19 子块 ID 集合，避免子块被二次渲染
        self._callout_child_ids: set = set()
        self.stats = {
            "total_blocks": 0,
            "text_blocks": 0,
            "sheet_blocks": 0,
            "failed_blocks": 0
        }
    
    def _extract_text_from_elements(self, elements: list) -> str:
        """从 elements 列表中提取纯文本，自动过滤带删除线的 text_run 片段。

        兼容飞书 SDK 对象（hasattr）与字典（dict.get）两种数据结构。

        Args:
            elements: 飞书 Block 的 elements 列表

        Returns:
            拼接后的纯文本字符串
        """
        text = ""
        for element in elements:
            # ── SDK 对象形式 ─────────────────────────────────────────
            if hasattr(element, 'text_run') and element.text_run is not None:
                text_run = element.text_run
                # 检查删除线：text_element_style.strikethrough
                style = getattr(text_run, 'text_element_style', None)
                strikethrough = (getattr(style, 'strikethrough', False)
                                 if style is not None else False)
                if not strikethrough and hasattr(text_run, 'content') and text_run.content:
                    text += text_run.content

            elif hasattr(element, 'mention_doc') and element.mention_doc is not None:
                md = element.mention_doc
                title = getattr(md, 'title', '') or ''
                url   = getattr(md, 'url',   '') or ''
                if title or url:
                    text += f"{title}（{url}）"

            # ── 字典形式 ─────────────────────────────────────────────
            elif isinstance(element, dict) and 'text_run' in element:
                text_run = element['text_run']
                if isinstance(text_run, dict):
                    style = text_run.get('text_element_style')
                    strikethrough = (style.get('strikethrough', False)
                                     if isinstance(style, dict) else False)
                    if not strikethrough and 'content' in text_run:
                        text += text_run['content']

            elif isinstance(element, dict) and 'mention_doc' in element:
                md = element['mention_doc']
                title = md.get('title', '') or ''
                url   = md.get('url',   '') or ''
                if title or url:
                    text += f"{title}（{url}）"

        return text

    def build_block_tree(self, blocks: List[Dict]) -> Dict[str, Dict]:
        """构建 Block 索引字典
        
        Args:
            blocks: Block 列表
            
        Returns:
            以 block_id 为键的字典
        """
        return {block['block_id']: block for block in blocks}
    
    def traverse_blocks(self, root_id: str, blocks_dict: Dict) -> List[Dict]:
        """DFS 遍历 Block 树
        
        Args:
            root_id: 根节点 ID（Page Block）
            blocks_dict: Block 索引字典
            
        Returns:
            按阅读顺序排列的 Block 列表
        """
        result = []
        
        def dfs(block_id: str):
            if block_id not in blocks_dict:
                return
            
            block = blocks_dict[block_id]
            result.append(block)
            
            # type31 (Table) 和 type19 (Callout) 的子节点由专用转换器处理，不参与 DFS
            if block.get('block_type') in (19, 31):
                return
            
            # 按 children 数组顺序递归访问子节点
            for child_id in block.get('children', []):
                dfs(child_id)
        
        dfs(root_id)
        return result
    
    def convert_text_block(self, block: Dict) -> str:
        """转换文本类 Block
        
        Args:
            block: Block 数据
            
        Returns:
            Markdown 格式的文本
        """
        block_type = block['block_type']

        # block_type → 对应的飞书 SDK 属性名映射
        type_to_attr = {
            2:  'text',
            3:  'heading1', 4:  'heading2', 5:  'heading3',
            6:  'heading4', 7:  'heading5', 8:  'heading6',
            9:  'heading7', 10: 'heading8', 11: 'heading9',
            12: 'bullet',   13: 'ordered',  15: 'code',
            14: 'quote',    17: 'todo',
        }
        attr_name = type_to_attr.get(block_type, 'text')

        # 优先取对应属性，回退到 'text'
        text_obj = block.get(attr_name) or block.get('text')

        # 提取文本内容（经 _extract_text_from_elements 自动过滤删除线）
        text_content = ""
        if text_obj:
            if hasattr(text_obj, 'elements'):
                elements = text_obj.elements if text_obj.elements else []
            elif isinstance(text_obj, dict) and 'elements' in text_obj:
                elements = text_obj.get('elements', [])
            else:
                elements = []

            text_content = self._extract_text_from_elements(elements)

        # 根据 block_type 转换为 Markdown
        if block_type == 2:
            # 普通文本
            return text_content
        elif 3 <= block_type <= 11:
            # 标题 1-9
            level = block_type - 2
            return '#' * level + ' ' + text_content
        elif block_type == 12:
            # 无序列表
            return '- ' + text_content
        elif block_type == 13:
            # 有序列表：从 ordered.style.sequence 动态还原真实序号
            ordered_obj = block.get('ordered')
            sequence = None
            if ordered_obj:
                # 兼容 SDK 对象与字典两种形式
                style = (getattr(ordered_obj, 'style', None)
                         if not isinstance(ordered_obj, dict)
                         else ordered_obj.get('style'))
                if style is not None:
                    if hasattr(style, 'sequence'):
                        sequence = style.sequence
                    elif isinstance(style, dict):
                        sequence = style.get('sequence')
            # sequence 为具体数字字符串 → 重置计数器；为 "auto"/None → 顺延递增
            if sequence and sequence != 'auto':
                try:
                    self._ordered_list_counter = int(sequence)
                except (ValueError, TypeError):
                    self._ordered_list_counter += 1
            else:
                self._ordered_list_counter += 1

            # 检查父块是否也是有序列表（type13），若是则渲染为 x.m 嵌套格式
            parent_id = block.get('parent_id')
            parent_prefix = ""
            if parent_id and self._blocks_dict:
                parent_block = self._blocks_dict.get(parent_id)
                if parent_block and parent_block.get('block_type') == 13:
                    p_ordered = parent_block.get('ordered')
                    p_seq = None
                    if p_ordered:
                        p_style = (getattr(p_ordered, 'style', None)
                                   if not isinstance(p_ordered, dict)
                                   else p_ordered.get('style'))
                        if p_style is not None:
                            p_seq = (getattr(p_style, 'sequence', None)
                                     if not isinstance(p_style, dict)
                                     else p_style.get('sequence'))
                    if p_seq and p_seq != 'auto':
                        parent_prefix = f"{p_seq}."

            return f"{parent_prefix}{self._ordered_list_counter}. " + text_content
        elif block_type == 14:
            # 引用
            return '> ' + text_content
        elif block_type == 15:
            # 代码块
            return '```\n' + text_content + '\n```'
        elif block_type == 17:
            # Todo Block：读取完成状态渲染为 Markdown 复选框
            todo_obj = block.get('todo')
            done = False
            if todo_obj:
                style = (getattr(todo_obj, 'style', None)
                         if not isinstance(todo_obj, dict)
                         else todo_obj.get('style'))
                if style is not None:
                    done = (getattr(style, 'done', False)
                            if not isinstance(style, dict)
                            else style.get('done', False))
            prefix = '- [x] ' if done else '- [ ] '
            return prefix + text_content
        elif block_type == 22:
            # 分割线
            return '---'
        else:
            return text_content
    
    def escape_table_cell(self, cell) -> str:
        """转义表格单元格内容
        
        支持普通字符串、None，以及飞书富文本结构（list of dicts）。
        
        Args:
            cell: 原始单元格值（str / None / list of dicts）
            
        Returns:
            转义后的文本（处理 | 和换行符）
        """
        if isinstance(cell, list):
            # 飞书富文本：拼接所有 text 片段
            text = ""
            for segment in cell:
                if isinstance(segment, dict) and 'text' in segment:
                    text += str(segment.get('text', ''))
                else:
                    text += str(segment)
        elif cell is None:
            text = ""
        else:
            text = str(cell)

        # 转义管道符
        text = text.replace('|', '\\|')

        # 替换换行符为 HTML 换行标签
        text = text.replace('\n', '<br>')

        # 移除回车符
        text = text.replace('\r', '')

        return text
    
    def normalize_table_data(self, data: List[List]) -> List[List]:
        """标准化表格数据，确保每行列数一致
        
        Args:
            data: 二维数组
            
        Returns:
            标准化后的二维数组
        """
        if not data:
            return []
        
        max_cols = max(len(row) for row in data)
        normalized = []
        for row in data:
            # 补充空单元格
            normalized_row = row + [''] * (max_cols - len(row))
            normalized.append(normalized_row)
        
        return normalized
    
    def format_table_as_markdown(self, data: List[List]) -> str:
        """将二维数组格式化为 Markdown 表格
        
        Args:
            data: 二维数组，第一行为表头
            
        Returns:
            Markdown 表格字符串
        """
        if not data:
            return ""
        
        # 标准化数据
        data = self.normalize_table_data(data)
        
        if not data:
            return ""
        
        # 获取列数
        num_cols = len(data[0])
        
        # 构建表格
        lines = []
        
        # 表头（直接传入原始值，由 escape_table_cell 负责类型处理）
        header_row = data[0]
        header_cells = [self.escape_table_cell(cell) for cell in header_row]
        lines.append('| ' + ' | '.join(header_cells) + ' |')

        # 分隔行
        lines.append('| ' + ' | '.join(['---'] * num_cols) + ' |')

        # 数据行
        for row in data[1:]:
            cells = [self.escape_table_cell(cell) for cell in row]
            lines.append('| ' + ' | '.join(cells) + ' |')
        
        return '\n'.join(lines)
    
    def convert_sheet_block(self, block: Dict) -> str:
        """转换表格 Block
        
        Args:
            block: Block 数据
            
        Returns:
            Markdown 表格字符串
        """
        try:
            # 提取 sheet.token
            if 'sheet' not in block or not block['sheet']:
                raise ParseError(block['block_id'], "Missing sheet data")
            
            sheet_obj = block['sheet']
            if hasattr(sheet_obj, 'token'):
                sheet_token = sheet_obj.token
            elif isinstance(sheet_obj, dict) and 'token' in sheet_obj:
                sheet_token = sheet_obj['token']
            else:
                raise ParseError(block['block_id'], "Missing sheet token")
            
            # 解析 Sheet Token
            spreadsheet_token, sheet_id = parse_sheet_token(sheet_token)
            
            # 查询表格元数据
            metadata = self.client.query_sheet_metadata(spreadsheet_token, sheet_id)
            row_count = metadata['row_count']
            column_count = metadata['column_count']
            
            # 构造范围
            range_notation = build_range_notation(sheet_id, row_count, column_count)
            
            # 读取表格数据
            data = self.client.read_sheet_range(spreadsheet_token, range_notation)
            
            # 转换为 Markdown 表格
            markdown_table = self.format_table_as_markdown(data)
            
            self.stats['sheet_blocks'] += 1
            return markdown_table
            
        except Exception as e:
            lark.logger.error(f"Failed to convert sheet block {block['block_id']}: {str(e)}")
            self.stats['failed_blocks'] += 1
            return f"\n<!-- Failed to convert sheet: {str(e)} -->\n"
    
    def convert_block_to_markdown(self, block: Dict) -> str:
        """将单个 Block 转换为 Markdown
        
        Args:
            block: Block 数据
            
        Returns:
            Markdown 格式的文本
        """
        block_type = block['block_type']
        
        try:
            if block_type == 1:
                # Page Block，跳过不输出
                return ""
            elif block_type == 30:
                # 表格 Block
                return self.convert_sheet_block(block)
            elif block_type == 31:
                # Grid Block（原生表格行），由 parse_document 的 while 循环批量处理，此处不应单独出现
                return ""
            elif block_type == 32:
                # Grid Column Block，内容已在 type31 处理中消耗
                return ""
            elif block_type == 19:
                # Callout（高亮块）：收集所有子块文本，渲染为 blockquote
                lines = []
                for child_id in block.get('children', []):
                    child = self._blocks_dict.get(child_id)
                    if child is None:
                        continue
                    child_text_obj = child.get('text')
                    if child_text_obj is None:
                        continue
                    if hasattr(child_text_obj, 'elements'):
                        elems = child_text_obj.elements or []
                    elif isinstance(child_text_obj, dict):
                        elems = child_text_obj.get('elements', [])
                    else:
                        elems = []
                    t = self._extract_text_from_elements(elems)
                    if t:
                        lines.append('> ' + t)
                return '\n'.join(lines)
            elif block_type in [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17, 22]:
                # 文本类 Block（含 quote=14、todo=17）
                # type19 子块已在 callout 处理中消耗，跳过避免重复
                if block['block_id'] in self._callout_child_ids:
                    return ""
                self.stats['text_blocks'] += 1
                return self.convert_text_block(block)
            else:
                # 未知类型，记录警告
                lark.logger.warning(f"Unsupported block type: {block_type}, skipping block {block['block_id']}")
                return ""
        except Exception as e:
            lark.logger.error(f"Failed to convert block {block['block_id']}: {str(e)}")
            self.stats['failed_blocks'] += 1
            return ""
    
    def parse_document(self, document_id: str) -> str:
        """解析文档并转换为 Markdown
        
        Args:
            document_id: 文档 ID
            
        Returns:
            完整的 Markdown 内容
        """
        # 重置状态，确保每次转换从头开始
        self._ordered_list_counter = 0
        self._blocks_dict = {}
        self._callout_child_ids = set()

        # 获取所有 Block
        blocks = self.client.list_document_blocks(document_id)
        self.stats['total_blocks'] = len(blocks)
        
        # 构建 Block 索引
        blocks_dict = self.build_block_tree(blocks)
        self._blocks_dict = blocks_dict

        # 收集所有 type19 的子块 ID，后续渲染时跳过它们避免重复输出
        for b in blocks:
            if b.get('block_type') == 19:
                for cid in b.get('children', []):
                    self._callout_child_ids.add(cid)

        # 找到 Page Block（根节点）
        root_id = None
        for block in blocks:
            if block['block_type'] == 1:
                root_id = block['block_id']
                break
        
        if not root_id:
            raise ParseError("unknown", "No Page Block found in document")

        # ── 提取文档标题（来自 Page block 的 page 属性）──────────────────────
        root_block = blocks_dict[root_id]
        page_obj = root_block.get('page')
        title = ""
        if page_obj:
            # 兼容 SDK 对象和字典两种结构
            if hasattr(page_obj, 'elements'):
                elements = page_obj.elements or []
            elif isinstance(page_obj, dict):
                elements = page_obj.get('elements', [])
            else:
                elements = []

            title += self._extract_text_from_elements(elements)

        title = title.strip()
        if not title:
            raise ParseError(root_id, "Document title not found. Aborting export.")

        self.document_title = title
        # ─────────────────────────────────────────────────────────────────────

        # 遍历 Block 树
        ordered_blocks = self.traverse_blocks(root_id, blocks_dict)
        
        # 转换每个 Block 并拼接
        markdown_parts = []
        for block in ordered_blocks:
            if block['block_type'] == 31:
                # type31 Grid: 提取列→转置为行→渲染为 Markdown 表格
                data = parse_grid_to_table(block, blocks_dict)
                if data:
                    md_table = self.format_table_as_markdown(data)
                    if md_table:
                        markdown_parts.append(md_table)
                    self.stats['sheet_blocks'] += 1
            else:
                markdown_content = self.convert_block_to_markdown(block)
                if markdown_content:
                    markdown_parts.append(markdown_content)
        
        # 在 Block 之间添加空行
        return '\n\n'.join(markdown_parts)
    
    def get_statistics(self) -> Dict:
        """获取转换统计信息"""
        return self.stats
