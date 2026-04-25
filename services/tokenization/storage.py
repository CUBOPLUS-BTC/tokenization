from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from common.config import Settings


_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class StoredDocument(NamedTuple):
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    path: Path


def _storage_root(settings: Settings) -> Path:
    root = Path(settings.tokenization_documents_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[2] / root
    return root.resolve()


def sanitize_filename(filename: str | None) -> str:
    normalized = (filename or "document.pdf").strip() or "document.pdf"
    normalized = normalized.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    cleaned = _SAFE_FILENAME_RE.sub("-", normalized).strip(".-")
    if not cleaned.lower().endswith(".pdf"):
        cleaned = f"{cleaned or 'document'}.pdf"
    return cleaned


def store_pdf_document(
    *,
    settings: Settings,
    asset_id: str,
    filename: str | None,
    payload: bytes,
    content_type: str | None,
) -> StoredDocument:
    safe_filename = sanitize_filename(filename)
    storage_key = f"{asset_id}/{safe_filename}"
    path = _storage_root(settings) / asset_id / safe_filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return StoredDocument(
        storage_key=storage_key,
        filename=safe_filename,
        content_type=(content_type or "application/pdf").strip() or "application/pdf",
        size_bytes=len(payload),
        path=path,
    )


def resolve_document_path(*, settings: Settings, storage_key: str) -> Path:
    root = _storage_root(settings)
    resolved = (root / storage_key).resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError("document_path_outside_storage_root")
    return resolved
