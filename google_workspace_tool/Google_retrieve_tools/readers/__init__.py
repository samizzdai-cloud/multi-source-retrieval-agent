"""Google Drive 文件类型专用读取器。

每个模块提供统一的 3 方法接口：
- read_one(file_id, creds, ...) -> dict
- read_batch(file_ids, creds, ...) -> list[dict]
- read_concurrent(file_ids, creds, max_workers, ...) -> list[dict]
"""

from . import doc_reader
from . import word_reader
from . import slide_reader
from . import pdf_reader
from . import sheet_reader

__all__ = ["doc_reader", "word_reader", "slide_reader", "pdf_reader", "sheet_reader"]
