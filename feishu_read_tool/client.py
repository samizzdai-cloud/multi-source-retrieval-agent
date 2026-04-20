"""
feishu_client.py — 飞书 API 客户端

封装对飞书 Docx / Drive / Sheets API 的所有网络调用。
使用 user_access_token 认证（用户身份），token 在初始化时传入，自动附加到所有请求。
外部项目通过实例化 FeishuClient(user_access_token) 后调用各方法。
"""

import logging
import time

import requests
import lark_oapi as lark
from lark_oapi.api.docx.v1 import ListDocumentBlockRequest
from lark_oapi.api.drive.v1 import ListFileRequest
from lark_oapi.api.sheets.v3 import QuerySpreadsheetSheetRequest
from typing import List, Dict
import json

from .rate_limiter import DOC_API_BUCKET, P_BLOCK_API, P_WHITEBOARD, P_COMMENT

logger = logging.getLogger(__name__)

# 99991400 触发后的退避阶梯（秒）。最后一次返回 None 表示放弃重试。
_RATELIMIT_BACKOFF_S = (1.0, 2.0, 4.0)
_RATELIMIT_CODE = 99991400


class APIError(Exception):
    """飞书 API 调用错误"""
    def __init__(self, code: int, msg: str, log_id: str):
        self.code = code
        self.msg = msg
        self.log_id = log_id
        super().__init__(f"API Error {code}: {msg} (log_id: {log_id})")


class FeishuClient:
    """飞书 API 客户端（user_access_token 认证）"""

    def __init__(self, user_access_token: str, log_level=lark.LogLevel.INFO):
        """初始化客户端

        Args:
            user_access_token: 飞书用户访问令牌
            log_level:         日志级别
        """
        self.user_access_token = user_access_token
        self._option = lark.RequestOption.builder() \
            .user_access_token(user_access_token) \
            .build()
        self.client = lark.Client.builder() \
            .enable_set_token(True) \
            .log_level(log_level) \
            .build()

    def get_root_folder_meta(self) -> Dict:
        """获取当前用户「我的空间」根文件夹元数据。

        Returns:
            包含 token、id、user_id 等字段的元数据字典

        Raises:
            APIError: HTTP 请求失败或业务层返回非零 code
        """
        url = "https://open.feishu.cn/open-apis/drive/explorer/v2/root_folder/meta"
        headers = {"Authorization": f"Bearer {self.user_access_token}"}
        response = requests.get(url, headers=headers)

        if response.status_code != 200:
            raise APIError(
                code=response.status_code,
                msg=f"HTTP error: {response.text}",
                log_id=""
            )

        result = response.json()
        if result.get("code") != 0:
            raise APIError(
                code=result.get("code"),
                msg=result.get("msg", "unknown error"),
                log_id=""
            )

        return result.get("data", {})

    def list_document_blocks(self, document_id: str) -> List[Dict]:
        """获取文档的所有 Block

        Args:
            document_id: 文档 ID

        Returns:
            Block 列表

        Raises:
            APIError: API 调用失败
        """
        all_blocks = []
        page_token = None
        page_size = 500

        while True:
            request_builder = ListDocumentBlockRequest.builder() \
                .document_id(document_id) \
                .page_size(page_size) \
                .document_revision_id(-1)  # -1 表示获取最新版本

            if page_token:
                request_builder.page_token(page_token)

            request = request_builder.build()

            # 99991400 共享桶：高优先级（Stage 3 关键路径）+ 本地退避。
            response = None
            for attempt, backoff in enumerate((*_RATELIMIT_BACKOFF_S, None)):
                DOC_API_BUCKET.acquire(P_BLOCK_API)
                response = self.client.docx.v1.document_block.list(request, self._option)
                if response.success():
                    break
                if response.code == _RATELIMIT_CODE and backoff is not None:
                    logger.warning(
                        "list_document_blocks hit 99991400 (doc=%s attempt=%d) → sleep %.1fs",
                        document_id[:12], attempt + 1, backoff,
                    )
                    time.sleep(backoff)
                    continue
                break

            if not response.success():
                raise APIError(
                    code=response.code,
                    msg=response.msg,
                    log_id=response.get_log_id()
                )

            if response.data and response.data.items:
                for block in response.data.items:
                    block_dict = {
                        'block_id': block.block_id,
                        'block_type': block.block_type,
                        'parent_id': block.parent_id,
                        'children': block.children if block.children else [],
                    }

                    rich_text_attrs = [
                        'text', 'heading1', 'heading2', 'heading3', 'heading4',
                        'heading5', 'heading6', 'heading7', 'heading8', 'heading9',
                        'bullet', 'ordered', 'code', 'quote', 'page',
                        'todo',
                    ]
                    for attr in rich_text_attrs:
                        if hasattr(block, attr) and getattr(block, attr):
                            block_dict[attr] = getattr(block, attr)

                    if hasattr(block, 'sheet') and block.sheet:
                        block_dict['sheet'] = block.sheet

                    # type31 Table: extract column_size and row_size from table.property
                    if hasattr(block, 'table') and block.table and block.table.property:
                        p = block.table.property
                        block_dict['table_column_size'] = p.column_size
                        block_dict['table_row_size'] = p.row_size

                    # type43 Whiteboard: extract board.token as whiteboard_id
                    if hasattr(block, 'board') and block.board:
                        board_token = getattr(block.board, 'token', None)
                        if board_token:
                            block_dict['board_token'] = board_token

                    # type18 Bitable embed: extract bitable.token (SDK field)
                    if hasattr(block, 'bitable') and block.bitable:
                        bitable_token = getattr(block.bitable, 'token', None)
                        if bitable_token:
                            block_dict['bitable_token'] = bitable_token

                    # type53 reference_base (真正的 Bitable embed):
                    # REST API raw 字段 reference_base.token = app_token
                    # reference_base.token 格式: {app_token}  (下划线前)
                    # 注意 SDK 可能将此字段归入 undefined，改用 REST raw dict 补取
                    if block.block_type == 53:
                        ref_base = getattr(block, 'reference_base', None)
                        if ref_base:
                            rb_token = getattr(ref_base, 'token', None)
                            rb_view = getattr(ref_base, 'view_id', None)
                            if rb_token:
                                # 保留完整 token（含 _tblXXX），由 _build_bitable_embed_chunk 解析
                                block_dict['bitable_token'] = rb_token
                                block_dict['bitable_view_id'] = rb_view or ''

                    # type40 add_ons: Mermaid flowchart 小组件
                    # add_ons.record JSON 的 data 字段含 mermaid 源码
                    if block.block_type == 40:
                        add_ons = getattr(block, 'add_ons', None)
                        if add_ons:
                            record_str = getattr(add_ons, 'record', None)
                            if record_str:
                                try:
                                    import json as _json
                                    record_obj = _json.loads(record_str)
                                    mermaid_data = record_obj.get('data', '')
                                    if mermaid_data:
                                        block_dict['mermaid_data'] = mermaid_data
                                except Exception:
                                    pass

                    # type15 Code: extract language int from code.style.language
                    if block.block_type == 15:
                        code_obj = getattr(block, 'code', None)
                        if code_obj:
                            style = getattr(code_obj, 'style', None)
                            if style:
                                lang = getattr(style, 'language', None)
                                if lang is not None:
                                    block_dict['code_language'] = int(lang)

                    # type17 Todo: extract done status from todo.style.done
                    if block.block_type == 17:
                        todo_obj = getattr(block, 'todo', None)
                        if todo_obj:
                            style = getattr(todo_obj, 'style', None)
                            if style:
                                done = getattr(style, 'done', None)
                                if done is not None:
                                    block_dict['todo_done'] = bool(done)

                    # comment_ids: block-level comment bindings (used by comment injector)
                    if block.comment_ids:
                        block_dict['comment_ids'] = list(block.comment_ids)

                    all_blocks.append(block_dict)

            if not response.data.has_more:
                break

            page_token = response.data.page_token

        return all_blocks

    def list_folder_files(self, folder_token: str, page_size: int = 200) -> List[Dict]:
        """获取文件夹当前层级的所有文件条目（自动分页）

        Args:
            folder_token: 文件夹 token
            page_size:    每页条目数，默认 200

        Returns:
            所有页合并后的原始文件条目列表（dict）

        Raises:
            APIError: API 返回非零 code
        """
        all_files = []
        page_token = None

        while True:
            request_builder = ListFileRequest.builder() \
                .folder_token(folder_token) \
                .page_size(page_size)

            if page_token:
                request_builder.page_token(page_token)

            request = request_builder.build()
            response = self.client.drive.v1.file.list(request, self._option)

            if not response.success():
                raise APIError(
                    code=response.code,
                    msg=response.msg,
                    log_id=response.get_log_id()
                )

            if response.data and response.data.files:
                for f in response.data.files:
                    entry = {
                        'name': f.name,
                        'token': f.token,
                        'type': f.type,
                        'edited_time': getattr(f, 'edited_time', None),
                    }
                    if hasattr(f, 'shortcut_info') and f.shortcut_info:
                        entry['shortcut_info'] = {
                            'target_token': f.shortcut_info.target_token,
                            'target_type': f.shortcut_info.target_type,
                        }
                    all_files.append(entry)

            if not (response.data and response.data.has_more):
                break

            page_token = response.data.next_page_token

        return all_files

    def query_sheet_metadata(self, spreadsheet_token: str, sheet_id: str) -> Dict:
        """查询电子表格元数据（使用 Sheets V3 SDK）

        Args:
            spreadsheet_token: 表格 token
            sheet_id:          sheet ID

        Returns:
            包含 row_count 和 column_count 的字典
        """
        try:
            request = QuerySpreadsheetSheetRequest.builder() \
                .spreadsheet_token(spreadsheet_token) \
                .build()

            response = self.client.sheets.v3.spreadsheet_sheet.query(request, self._option)

            if not response.success():
                lark.logger.warning(
                    f"Failed to query sheet metadata (V3): code={response.code}, "
                    f"msg={response.msg}, log_id={response.get_log_id()}. "
                    f"Using default values (100 rows, 26 columns)."
                )
                return {'row_count': 100, 'column_count': 26}

            sheets = response.data.sheets if response.data and response.data.sheets else []
            for sheet in sheets:
                if sheet.sheet_id == sheet_id:
                    grid = sheet.grid_properties
                    if grid:
                        return {
                            'row_count': grid.row_count,
                            'column_count': grid.column_count
                        }

            lark.logger.warning(
                f"Sheet {sheet_id} not found in spreadsheet {spreadsheet_token} (V3). "
                f"Using default values (100 rows, 26 columns)."
            )
            return {'row_count': 100, 'column_count': 26}

        except Exception as e:
            lark.logger.warning(
                f"Exception while querying sheet metadata (V3): {str(e)}. "
                f"Using default values (100 rows, 26 columns)."
            )
            return {'row_count': 100, 'column_count': 26}

    def get_wiki_node_info(self, wiki_node_token: str) -> Dict:
        """将 Wiki node token 转换为实际文档信息。

        飞书 Wiki 链接（feishu.cn/wiki/XxxToken）中的 token 是 wiki node token，
        不能直接用于 Docx Block API。需要调用 Wiki v2 API 获取真正的 obj_token 和 obj_type。

        Args:
            wiki_node_token: Wiki 节点 token（从 /wiki/ URL 中提取）

        Returns:
            包含 obj_token 和 obj_type 字段的字典。
            obj_type 可能为 "docx"、"doc"、"sheet"、"bitable" 等。

        Raises:
            APIError: API 调用失败
        """
        url = "https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node"
        headers = {"Authorization": f"Bearer {self.user_access_token}"}
        params = {"token": wiki_node_token}
        resp = requests.get(url, headers=headers, params=params, timeout=15)

        if resp.status_code != 200:
            raise APIError(
                code=resp.status_code,
                msg=f"HTTP error getting wiki node: {resp.text}",
                log_id=""
            )

        data = resp.json()
        if data.get("code") != 0:
            raise APIError(
                code=data.get("code"),
                msg=data.get("msg", "unknown error"),
                log_id=""
            )

        node = data.get("data", {}).get("node", {})
        return {
            "obj_token": node.get("obj_token", ""),
            "obj_type": node.get("obj_type", ""),
            "title": node.get("title", ""),
            "node_token": wiki_node_token,
        }

    def get_whiteboard_nodes(self, whiteboard_id: str) -> List[Dict]:
        """获取画板内所有节点，提取有文字内容的节点。

        Args:
            whiteboard_id: 画板唯一标识（来自 type-43 block 的 board.token）

        Returns:
            [{"node_id": str, "node_type": str, "text": str}, ...]
            仅包含有文字内容的节点。connector 节点不在结果中，但通过
            get_whiteboard_mermaid() 可获取完整的有向图结构。

        Raises:
            APIError: API 调用失败
        """
        nodes = self._fetch_whiteboard_nodes(whiteboard_id)
        results = []
        for node in nodes:
            if node.get("type") == "connector":
                continue
            t = self._extract_whiteboard_node_text(node)
            if t:
                results.append({
                    "node_id": node.get("id", ""),
                    "node_type": node.get("type", ""),
                    "text": t,
                })
        return results

    def get_whiteboard_mermaid(self, whiteboard_id: str) -> str:
        """将画板转换为 Mermaid flowchart 字符串。

        利用 connector 节点的 start/end 关系构建有向图，保留节点间流向语义。
        孤立节点（无连线）也会出现在图中。

        Args:
            whiteboard_id: 画板唯一标识

        Returns:
            Mermaid flowchart LR 源码字符串。
            无节点时返回空字符串。

        Raises:
            APIError: API 调用失败
        """
        nodes = self._fetch_whiteboard_nodes(whiteboard_id)

        # id → 文字（仅非 connector 节点）
        id_to_text: Dict[str, str] = {}
        for node in nodes:
            if node.get("type") == "connector":
                continue
            t = self._extract_whiteboard_node_text(node)
            if t:
                id_to_text[node["id"]] = t

        if not id_to_text:
            return ""

        # 从 connector 节点提取有向边
        edges: List[tuple] = []
        for node in nodes:
            if node.get("type") != "connector":
                continue
            conn = node.get("connector", {})
            src = conn.get("start_object", conn.get("start", {})).get("id", "")
            dst = conn.get("end_object", conn.get("end", {})).get("id", "")
            # 两端都有文字节点才加边（过滤连到空节点/图片的连线）
            if src in id_to_text and dst in id_to_text:
                edges.append((src, dst))

        def safe_id(node_id: str) -> str:
            """Mermaid 节点 ID：去掉 : 和空格"""
            return node_id.replace(":", "_").replace(" ", "_")

        def safe_label(text: str) -> str:
            """Mermaid 标签：替换双引号和花括号"""
            return (text
                    .replace('"', "'")
                    .replace("{", "(")
                    .replace("}", ")")
                    .replace("\n", " "))

        lines = ["flowchart LR"]
        for nid, txt in id_to_text.items():
            lines.append(f'    {safe_id(nid)}["{safe_label(txt)}"]')
        for src, dst in edges:
            lines.append(f"    {safe_id(src)} --> {safe_id(dst)}")

        return "\n".join(lines)

    # ── internal helpers ─────────────────────────────────────────────────────

    def _fetch_whiteboard_nodes(self, whiteboard_id: str) -> List[Dict]:
        """调用 Board API，返回原始节点列表。

        走 99991400 共享桶（P_WHITEBOARD），遇限流自动退避重试。
        """
        url = f"https://open.feishu.cn/open-apis/board/v1/whiteboards/{whiteboard_id}/nodes"
        headers = {"Authorization": f"Bearer {self.user_access_token}"}

        data = None
        for attempt, backoff in enumerate((*_RATELIMIT_BACKOFF_S, None)):
            DOC_API_BUCKET.acquire(P_WHITEBOARD)
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code != 200:
                raise APIError(
                    code=resp.status_code,
                    msg=f"HTTP error getting whiteboard nodes: {resp.text}",
                    log_id=""
                )
            data = resp.json()
            if data.get("code") == 0:
                break
            if data.get("code") == _RATELIMIT_CODE and backoff is not None:
                logger.warning(
                    "_fetch_whiteboard_nodes hit 99991400 (wb=%s attempt=%d) → sleep %.1fs",
                    whiteboard_id[:12], attempt + 1, backoff,
                )
                time.sleep(backoff)
                continue
            break

        if data is None or data.get("code") != 0:
            raise APIError(
                code=(data or {}).get("code", -1),
                msg=(data or {}).get("msg", "unknown error"),
                log_id=""
            )
        return data.get("data", {}).get("nodes", [])

    @staticmethod
    def _extract_whiteboard_node_text(node: Dict) -> str:
        """从画板节点提取纯文本（支持 text 和 rich_text 两种格式）。"""
        parts: List[str] = []
        t_obj = node.get("text")
        if t_obj and isinstance(t_obj, dict):
            t = t_obj.get("text", "").strip()
            if t:
                parts.append(t)
        rt = node.get("rich_text")
        if rt and isinstance(rt, dict):
            for para in rt.get("paragraphs", []):
                for el in para.get("elements", []):
                    tel = el.get("text_element")
                    if tel and isinstance(tel, dict):
                        t = tel.get("text", "").strip()
                        if t:
                            parts.append(t)
                    lel = el.get("link_element")
                    if lel and isinstance(lel, dict):
                        t = lel.get("text", "").strip()
                        if t:
                            parts.append(t)
        return " ".join(" ".join(p.split()) for p in parts if p).strip()
    def get_doc_comments(self, file_token: str, file_type: str = "docx") -> List[Dict]:
        """获取云文档全部评论（自动翻页）。

        只返回**未解决**的评论（is_solved=False），包括局部评论和全局评论。

        Args:
            file_token: 文档 token（docx document_id 或 wiki obj_token）
            file_type:  文档类型，"docx" 或 "doc"

        Returns:
            [
              {
                "is_whole": bool,          # True=全局评论，False=局部划词评论
                "quote": str,              # 局部评论的引用原文（is_whole=False 时有效）
                "replies": [str, ...]      # 所有回复的纯文本列表（按时间顺序）
              },
              ...
            ]
            空列表表示无评论或 API 权限不足（不抛异常）。
        """
        url = f"https://open.feishu.cn/open-apis/drive/v1/files/{file_token}/comments"
        headers = {"Authorization": f"Bearer {self.user_access_token}"}
        results = []
        page_token = None

        while True:
            params: Dict = {"file_type": file_type, "page_size": 100, "is_solved": False}
            if page_token:
                params["page_token"] = page_token

            # 99991400 共享桶，最低优先级（不阻塞主 pipeline）。
            data = None
            try:
                for attempt, backoff in enumerate((*_RATELIMIT_BACKOFF_S, None)):
                    DOC_API_BUCKET.acquire(P_COMMENT)
                    resp = requests.get(url, headers=headers, params=params, timeout=10)
                    if resp.status_code != 200:
                        return results  # 保持原语义：权限/网络错直接返回已收集内容
                    data = resp.json()
                    if data.get("code") == 0:
                        break
                    if data.get("code") == _RATELIMIT_CODE and backoff is not None:
                        logger.warning(
                            "get_doc_comments hit 99991400 (file=%s attempt=%d) → sleep %.1fs",
                            file_token[:12], attempt + 1, backoff,
                        )
                        time.sleep(backoff)
                        continue
                    # 其它业务错（权限、not found 等）→ 返回已收集内容
                    return results
            except Exception:
                return results
            if data is None or data.get("code") != 0:
                return results

            # [S1.2] 飞书 API 对空字段既可能省略也可能显式 null；``.get(k, {})`` 只在 key
            # 缺失时生效，显式 null 会穿透成 None。统一用 ``(x or {}/[])`` 兜底，避免
            # ``for item in None`` 这类 ``'NoneType' object is not iterable``。
            paged_data = data.get("data") or {}
            for item in (paged_data.get("items") or []):
                # 提取所有回复的纯文本
                reply_texts: List[str] = []
                reply_list = (item.get("reply_list") or {})
                for reply in (reply_list.get("replies") or []):
                    parts: List[str] = []
                    content = (reply.get("content") or {})
                    for el in (content.get("elements") or []):
                        if el.get("type") == "text_run":
                            t = (el.get("text_run") or {}).get("text", "").strip()
                            if t:
                                parts.append(t)
                    if parts:
                        reply_texts.append("".join(parts))

                if not reply_texts:
                    continue

                results.append({
                    "comment_id": item.get("comment_id", ""),
                    "is_whole": item.get("is_whole", False),
                    "quote": (item.get("quote") or "").strip(),
                    "replies": reply_texts,
                })

            if not paged_data.get("has_more"):
                break
            page_token = paged_data.get("page_token")

        return results
    def read_sheet_range(self, spreadsheet_token: str, range_notation: str) -> List[List]:
        """读取电子表格数据

        Args:
            spreadsheet_token: 表格 token
            range_notation:    范围表示法，如 "fgvTJC!A1:D10"

        Returns:
            二维数组格式的单元格数据

        Raises:
            APIError: API 调用失败
        """
        # 使用 Sheets API v2，指定 FormattedValue 以保留百分比、货币等显示格式
        # 直接用 requests 发请求，token 来自 self.user_access_token（由环境变量 FEISHU_UAT 传入）
        url = (
            f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
            f"/{spreadsheet_token}/values/{range_notation}"
            f"?valueRenderOption=FormattedValue"
        )
        headers = {"Authorization": f"Bearer {self.user_access_token}"}
        resp = requests.get(url, headers=headers, timeout=25)

        if resp.status_code != 200:
            raise APIError(
                code=resp.status_code,
                msg=f"HTTP error: {resp.text}",
                log_id=""
            )

        raw_data = resp.json()
        if raw_data.get("code") != 0:
            raise APIError(
                code=raw_data["code"],
                msg=raw_data.get("msg", "unknown error"),
                log_id=""
            )

        value_range = raw_data.get("data", {}).get("valueRange", {})
        return value_range.get("values", [])
    def query_all_sheets_metadata(self, spreadsheet_token: str) -> List[Dict]:
        """获取电子表格所有工作表的元数据（含 merges）。

        Returns:
            [{"sheet_id", "title", "index", "hidden", "row_count", "column_count",
              "resource_type", "merges": [{"start_row_index", "end_row_index",
              "start_column_index", "end_column_index"}]}]
        """
        url = (f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets"
               f"/{spreadsheet_token}/sheets/query")
        headers = {"Authorization": f"Bearer {self.user_access_token}"}
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code != 200:
            raise APIError(code=resp.status_code, msg=f"HTTP error: {resp.text}", log_id="")
        data = resp.json()
        if data.get("code") != 0:
            raise APIError(code=data.get("code"), msg=data.get("msg", ""), log_id="")
        results = []
        for s in data.get("data", {}).get("sheets", []):
            grid = s.get("grid_properties", {})
            results.append({
                "sheet_id": s.get("sheet_id", ""),
                "title": s.get("title", ""),
                "index": s.get("index", 0),
                "hidden": s.get("hidden", False),
                "row_count": grid.get("row_count", 200),
                "column_count": grid.get("column_count", 20),
                "resource_type": s.get("resource_type", "sheet"),
                "merges": s.get("merges", []),
            })
        return results

    def find_cells(self, spreadsheet_token: str, sheet_id: str,
                   range_str: str, keyword: str) -> List[str]:
        """在指定范围内查找包含关键词的单元格。

        Args:
            spreadsheet_token: 表格 token
            sheet_id:          工作表 ID
            range_str:         查找范围，如 "sheet_id!A1:V100"
            keyword:           查找的关键词

        Returns:
            匹配的 cell 引用列表，如 ['A12', 'B15', 'F1']
        """
        url = (f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets"
               f"/{spreadsheet_token}/sheets/{sheet_id}/find")
        headers = {
            "Authorization": f"Bearer {self.user_access_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        body = {
            "find_condition": {
                "range": range_str,
                "match_case": False,
                "match_entire_cell": False,
                "search_by_regex": False,
                "include_formulas": False,
            },
            "find": keyword,
        }
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=15)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if data.get("code") != 0:
                return []
            find_result = data.get("data", {}).get("find_result", {})
            matched = find_result.get("matched_cells", [])
            return matched if isinstance(matched, list) else []
        except Exception:
            return []