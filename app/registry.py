"""Operation registry.

THE extension point. To support a new file type or action:
  1. write ``def my_op(sources: list[Path], ctx: OpContext) -> Path`` in app/ops/
  2. append one Operation(...) entry to OPERATIONS below.
The GUI rebuilds its action buttons from this table automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .workspace import OpContext

Handler = Callable[[list[Path], OpContext], Path]


@dataclass(frozen=True)
class Operation:
    id: str
    label: str                     # button text
    extensions: tuple[str, ...]    # lowercase, with dot
    handler: Handler
    description: str = ""
    multi: bool = False            # handler itself consumes several files at once
    batch: bool = False            # run the handler once per file when many selected
    parallel_safe: bool = True     # False => must run sequentially (e.g. COM/Word)
    needs_target_size: bool = False
    primary: bool = False          # highlighted as the recommended action


# Input types the file picker advertises. Extend freely.
FILE_TYPES: list[tuple[str, str]] = [
    ("PDF documents", "*.pdf"),
    ("Word documents", "*.docx"),
    ("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
    ("All supported", "*.pdf *.docx *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp"),
]

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")

OPERATIONS: list[Operation] = []


def register(op: Operation) -> Operation:
    OPERATIONS.append(op)
    return op


def group_key(ext: str) -> str:
    """Canonical bucket a file extension belongs to for selection purposes.

    All image extensions collapse to one bucket: "Images -> PDF" is meant to
    combine a .jpg and a .png in one job, so a selection is only "mixed" (and
    thus rejected) when it spans genuinely different operations, e.g. PDFs
    mixed with DOCX. Previously every extension was its own bucket, so picking
    two different image formats together silently lost the feature.
    """
    return "image" if ext in IMAGE_EXTS else ext


def operations_for(paths: list[Path]) -> list[Operation]:
    """Actions valid for the current selection."""
    if not paths:
        return []
    exts = {p.suffix.lower() for p in paths}
    if len({group_key(e) for e in exts}) != 1:
        return []
    multi = len(paths) > 1
    ops = [op for op in OPERATIONS
           if (exts & set(op.extensions)) and (op.multi or op.batch or not multi)]
    return sorted(ops, key=lambda o: (not o.primary,))


def _build() -> None:
    from .ops import compress, convert_docx, convert_markdown, convert_image

    register(Operation(
        id="pdf_compress", label="Compress PDF",
        extensions=(".pdf",), handler=compress.compress_pdf,
        description="Shrink to the target size, keeping text searchable when possible.",
        batch=True, needs_target_size=True))

    register(Operation(
        id="pdf_to_docx", label="PDF → Word (.docx)",
        extensions=(".pdf",), handler=convert_docx.pdf_to_docx,
        description="Layout-preserving conversion, processed in page batches.",
        batch=True))

    register(Operation(
        id="pdf_to_md", label="PDF → Markdown",
        extensions=(".pdf",), handler=convert_markdown.pdf_to_markdown,
        description="LLM-ready Markdown, split into chat-sized parts. "
                    "OCRs scanned pages automatically.",
        batch=True, primary=True))

    register(Operation(
        id="pdf_to_images", label="PDF → Images",
        extensions=(".pdf",), handler=convert_image.pdf_to_images,
        description="Render every page to PNG in a folder.", batch=True))

    register(Operation(
        id="docx_to_pdf", label="Word → PDF",
        extensions=(".docx",), handler=convert_docx.docx_to_pdf,
        description="Via Microsoft Word if installed, else LibreOffice.",
        batch=True, parallel_safe=False))   # Word COM breaks under concurrency

    register(Operation(
        id="img_to_pdf", label="Images → PDF",
        extensions=IMAGE_EXTS, handler=convert_image.images_to_pdf,
        description="Combine the selected images into one PDF.", multi=True))


_build()
