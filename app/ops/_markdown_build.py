"""Markdown assembly from engine primitives.

Replaces pymupdf4llm (AGPL). Headings are inferred from font size relative to
the page's median, tables come from pdfplumber, and text falling inside a table
is suppressed so it is not emitted twice.
"""
from __future__ import annotations

from statistics import median

from ..pdfengine import BBox, Page, Span


def _inside(span: Span, box: BBox) -> bool:
    cx = (span.bbox[0] + span.bbox[2]) / 2
    cy = (span.bbox[1] + span.bbox[3]) / 2
    return box[0] <= cx <= box[2] and box[1] <= cy <= box[3]


def _table_md(rows: list[list[str]]) -> str:
    def cell(v) -> str:
        return " ".join(str(v or "").split()).replace("|", "\\|")
    header = [cell(c) for c in rows[0]]
    width = max(len(r) for r in rows)
    header += [""] * (width - len(header))
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join(["---"] * width) + " |"]
    for row in rows[1:]:
        cells = [cell(c) for c in row] + [""] * (width - len(row))
        out.append("| " + " | ".join(cells[:width]) + " |")
    return "\n".join(out)


def page_markdown(page: Page, want_tables: bool = True,
                  exclude_boxes: list[BBox] | None = None) -> str:
    """`exclude_boxes` lets the caller keep figure-region text out of the flowing
    paragraph text (same idea as table suppression below) -- otherwise a
    caption or in-drawing label appears twice: once garbled here (it is short,
    out-of-reading-order text, so it reads as nonsense) and once properly under
    the figure's own description block."""
    tables = page.tables() if want_tables else []
    spans = page.spans()
    if not spans and not tables:
        return ""

    sizes = [s.size for s in spans if s.size]
    body = median(sizes) if sizes else 11.0

    blocks: list[tuple[float, str]] = []
    for box, rows in tables:
        blocks.append((box[1], _table_md(rows)))

    exclude = exclude_boxes or []
    para: list[str] = []
    para_top = 0.0
    for span in spans:
        if any(_inside(span, box) for box, _ in tables):
            continue
        if any(_inside(span, box) for box in exclude):
            continue
        text = " ".join(span.text.split())
        if not text:
            continue
        if span.size >= body * 1.45:
            if para:
                blocks.append((para_top, " ".join(para))); para = []
            blocks.append((span.bbox[1], f"## {text}"))
        elif span.size >= body * 1.15 or (span.bold and len(text) < 90):
            if para:
                blocks.append((para_top, " ".join(para))); para = []
            blocks.append((span.bbox[1], f"### {text}"))
        else:
            if not para:
                para_top = span.bbox[1]
            para.append(text)
            # a short line ending in a full stop usually ends the paragraph
            if text.endswith((".", ":", ";")) and len(text) < 70:
                blocks.append((para_top, " ".join(para))); para = []
    if para:
        blocks.append((para_top, " ".join(para)))

    blocks.sort(key=lambda b: b[0])
    return "\n\n".join(b[1] for b in blocks if b[1].strip())
