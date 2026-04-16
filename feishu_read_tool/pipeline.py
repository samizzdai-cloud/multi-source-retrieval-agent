"""
pipeline.py — 兼容层（向后兼容，新代码请使用 docx_pipeline.py）

此文件保留供现有测试和外部引用，内容已迁移到 docx_pipeline.py。
feishu_retrieve.py 已升级为 Dispatcher 模式，直接引用 docx_pipeline。
"""
# 全部 re-export 自 docx_pipeline，保持向后兼容
from .docx_pipeline import (  # noqa: F401
    run_rag_pipeline,
    allocate_budget,
    _fetch_all_docs_async,
    _fetch_single_doc,
    _truncate_to_budget,
    TABLE_TYPES,
)
