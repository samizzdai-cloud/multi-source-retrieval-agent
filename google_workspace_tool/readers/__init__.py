"""Google Drive 文件类型专用读取器。

每个模块提供统一的 3 方法接口：
- read_one(file_id, creds, ...) -> dict
- read_batch(file_ids, creds, ...) -> list[dict]
- read_concurrent(file_ids, creds, max_workers, ...) -> list[dict]
"""

from . import doc_reader
from . import word_reader
from . import slide_reader
from . import sheet_reader
from . import text_reader
from . import excel_reader
from . import pptx_reader

__all__ = ["doc_reader", "word_reader", "slide_reader", "sheet_reader", "text_reader", "excel_reader", "pptx_reader"]
