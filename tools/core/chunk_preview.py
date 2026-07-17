"""Source-neutral preview builders used by evaluation LLMs.

The complete chunk remains available for the answer.  Preview is deliberately
small and carries document context so a model can judge relevance without
re-reading the entire document/table on every loop.
"""
from __future__ import annotations

from typing import Any, Mapping


def build_chunk_preview(content: str, metadata: Mapping[str, Any], *, limit: int = 700) -> str:
    """Build a deterministic, source-neutral preview.

    Table readers may provide ``table_preview`` with header/sample/hit rows;
    it wins over the generic body truncation.
    """
    title = str(metadata.get("source_name") or metadata.get("file_name") or "").strip()
    heading = str(metadata.get("heading_path") or "").strip()
    doc_type = str(metadata.get("doc_type") or metadata.get("content_type") or "").strip()
    modified = str(metadata.get("modified_time") or metadata.get("modifiedTime") or "").strip()
    table_preview = metadata.get("table_preview")
    # Document chunks are already heading/block bounded.  Preserve their source
    # text for Flash evaluation; only tables use an intentionally compact view.
    body = str(table_preview if table_preview is not None else content or "").strip()
    if table_preview is not None and len(body) > limit:
        body = body[:limit].rstrip() + "..."
    labels = []
    if title:
        labels.append(f"Title: {title}")
    if doc_type:
        labels.append(f"Type: {doc_type}")
    if heading:
        labels.append(f"Heading: {heading}")
    if modified:
        labels.append(f"Modified: {modified[:10]}")
    if body:
        labels.append(f"Content: {body}")
    return "\n".join(labels)
