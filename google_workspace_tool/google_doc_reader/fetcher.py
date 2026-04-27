"""
批量请求层：负责 Google Docs API 的并发获取、限流控制、错误处理。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


class BatchDocFetcher:
    """
    批量获取 Google Docs 文档 JSON 内容。

    特性：
    - 可控并发（默认 10）
    - fields 参数精简传输
    - 指数退避重试（429 限流）
    - 支持传入已认证的 service 或 credentials
    """

    DEFAULT_MAX_WORKERS = 10
    # 默认请求字段：包含文档标题、tab 结构、主体内容、列表属性
    DEFAULT_FIELDS = (
        "documentId,title,"
        "tabs(tabProperties,childTabs,"
        "documentTab(body(content),lists)"
        ")"
    )

    # 大纲模式字段：仅获取 paragraphStyle（判断 heading）和 textRun content（获取标题文本）
    # 注意：实际减少的数据量取决于文档内容，但可避免传输 table 详情、inlineObjects、footnotes 等
    OUTLINE_FIELDS = (
        "documentId,title,"
        "tabs(tabProperties,childTabs,"
        "documentTab(body(content(paragraph(paragraphStyle,elements(textRun(content,textStyle(strikethrough))))))))"
    )

    def __init__(
        self,
        creds=None,
        docs_service=None,
        max_workers: int = DEFAULT_MAX_WORKERS,
        fields: Optional[str] = None,
        suggestions_view_mode: str = "PREVIEW_WITHOUT_SUGGESTIONS",
        include_tabs_content: bool = True,
    ):
        """
        Args:
            creds: google.oauth2.credentials.Credentials，与 docs_service 二选一
            docs_service: 已构建的 docs(v1) service 对象，优先使用
            max_workers: 并发数
            fields: 请求的 fields 参数，默认精简字段
            suggestions_view_mode: 建议显示模式
            include_tabs_content: 是否包含所有 tabs
        """
        if docs_service:
            self._service = docs_service
        elif creds:
            self._service = build(
                "docs", "v1", credentials=creds, cache_discovery=False
            )
        else:
            raise ValueError("creds 或 docs_service 至少提供一个")

        self._max_workers = max_workers
        self._fields = fields or self.DEFAULT_FIELDS
        self._suggestions_view_mode = suggestions_view_mode
        self._include_tabs_content = include_tabs_content

    def fetch_one(self, doc_id: str) -> dict:
        """获取单个文档，带重试逻辑。"""
        attempt = 0
        max_attempts = 4
        base_delay = 1.0

        while attempt < max_attempts:
            try:
                result = (
                    self._service.documents()
                    .get(
                        documentId=doc_id,
                        includeTabsContent=self._include_tabs_content,
                        suggestionsViewMode=self._suggestions_view_mode,
                        fields=self._fields,
                    )
                    .execute()
                )
                return result
            except HttpError as e:
                status = e.resp.status if hasattr(e, "resp") else 0
                if status == 429:
                    attempt += 1
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Doc %s 触发限流 (429)，第 %d 次重试，等待 %.1f 秒",
                        doc_id,
                        attempt,
                        delay,
                    )
                    time.sleep(delay)
                    continue
                elif status == 403:
                    logger.error("Doc %s 无权限访问 (403): %s", doc_id, e)
                    raise
                elif status == 404:
                    logger.error("Doc %s 不存在 (404): %s", doc_id, e)
                    raise
                else:
                    logger.error("Doc %s API 错误 (%s): %s", doc_id, status, e)
                    raise
            except Exception as e:
                logger.error("Doc %s 未知错误: %s", doc_id, e)
                raise

        raise RuntimeError(f"Doc {doc_id} 超过最大重试次数")

    def fetch_many(
        self,
        doc_ids: list[str],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> dict[str, dict]:
        """
        批量获取多个文档。

        Args:
            doc_ids: 文档 ID 列表
            progress_callback: 可选进度回调，签名为 (current, total, doc_id) -> None

        Returns:
            {doc_id: doc_json} 的字典
        """
        results: dict[str, dict] = {}
        total = len(doc_ids)

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            future_to_id = {
                executor.submit(self.fetch_one, did): did for did in doc_ids
            }
            completed = 0
            for future in as_completed(future_to_id):
                did = future_to_id[future]
                try:
                    doc_json = future.result()
                    results[did] = doc_json
                except Exception as e:
                    logger.error("获取文档 %s 失败: %s", did, e)
                    results[did] = {"_error": str(e), "documentId": did}

                completed += 1
                if progress_callback:
                    progress_callback(completed, total, did)

        return results
