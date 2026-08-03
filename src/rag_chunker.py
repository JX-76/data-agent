# -*- coding: utf-8 -*-
"""Configurable chunkers for Phase A RAG productionization."""
from __future__ import unicode_literals

from rag_contracts import RagChunkRecord

try:
    text_type = unicode
except NameError:  # pragma: no cover - Python 3
    text_type = str


def _safe_text(value):
    if value is None:
        return u""
    if isinstance(value, text_type):
        return value
    try:
        return value.decode("utf-8")
    except Exception:
        try:
            return value.decode("mbcs")
        except Exception:
            return text_type(value)


def _table_text(lines, headers):
    """Preserve byte-input compatibility on Python 2 without unsafe joins."""
    text = u"\n".join(lines)
    # Historical callers/tests on Python 2 pass UTF-8 byte headers and expect
    # byte text back.  Keep that boundary compatibility after building safely
    # in Unicode; normal Unicode inputs remain Unicode for the RAG pipeline.
    if headers and not isinstance(headers[0], text_type):
        return text.encode("utf-8")
    return text


class RecursiveTextChunker(object):
    def __init__(self, chunk_size=512, overlap=80):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap must be >= 0 and < chunk_size")
        self.chunk_size = int(chunk_size)
        self.overlap = int(overlap)

    def chunk_document(self, document, text):
        text = _safe_text(text)
        if not text:
            return []
        chunks = []
        start = 0
        idx = 1
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            part = text[start:end]
            chunk_id = "%s#c%04d" % (document.doc_id, idx)
            chunks.append(RagChunkRecord(
                chunk_id=chunk_id,
                doc_id=document.doc_id,
                parent_id=document.doc_id,
                text=part,
                chunk_type="text",
                title=document.title,
                source_uri=document.source_uri,
                start=start,
                end=end,
                metadata={"tenant_id": document.tenant_id, "acl": list(document.acl), "chunk_index": idx},
            ))
            if end >= len(text):
                break
            start = max(0, end - self.overlap)
            idx += 1
        return chunks


class TableAwareChunker(object):
    def __init__(self, rows_per_chunk=20):
        self.rows_per_chunk = int(rows_per_chunk or 20)

    def chunk_table(self, document, headers, rows):
        headers = list(headers or [])
        rows = list(rows or [])
        if not rows:
            return []
        out = []
        for start in range(0, len(rows), self.rows_per_chunk):
            part = rows[start:start + self.rows_per_chunk]
            # All joining is deliberately Unicode based: Python 2 otherwise
            # coerces Chinese headers/cells through ASCII and crashes ingestion.
            lines = [u" | ".join([_safe_text(x) for x in headers])]
            for row in part:
                lines.append(u" | ".join([_safe_text(x) for x in row]))
            idx = len(out) + 1
            out.append(RagChunkRecord(
                chunk_id="%s#t%04d" % (document.doc_id, idx),
                doc_id=document.doc_id,
                parent_id=document.doc_id,
                text=_table_text(lines, headers),
                chunk_type="table",
                title=document.title,
                source_uri=document.source_uri,
                start=start,
                end=start + len(part),
                metadata={"tenant_id": document.tenant_id, "acl": list(document.acl), "headers": headers, "row_start": start, "row_count": len(part)},
            ))
        return out


class ParentChildChunker(object):
    def __init__(self, child_chunker=None):
        self.child_chunker = child_chunker or RecursiveTextChunker()

    def chunk_document(self, document, text):
        parent = RagChunkRecord(
            chunk_id=document.doc_id,
            doc_id=document.doc_id,
            parent_id=document.doc_id,
            text=_safe_text(text),
            chunk_type="parent",
            title=document.title,
            source_uri=document.source_uri,
            start=0,
            end=len(_safe_text(text)),
            metadata={"tenant_id": document.tenant_id, "acl": list(document.acl), "is_parent": True},
        )
        children = self.child_chunker.chunk_document(document, text)
        return [parent] + children
