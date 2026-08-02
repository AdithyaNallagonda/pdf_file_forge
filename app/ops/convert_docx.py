"""PDF ⇄ DOCX conversion — permissive licences only.

pdf2docx is gone: it is built on PyMuPDF and inherits the AGPL obligation. In
its place the document is rebuilt from engine primitives with python-docx (MIT):
headings by relative font size, tables from pdfplumber, paragraphs otherwise.

Be honest about the trade-off. pdf2docx reproduced columns, floats and absolute
positioning; this does not. What you get is a clean, editable, correctly ordered
document with its tables intact — which is what a document destined for markup
actually needs — not a pixel-faithful clone of the original layout.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from statistics import median

from .. import config, pdfengine
from ..workspace import OpContext


def _add_table(document, rows: list[list[str]]) -> None:
    width = max(len(r) for r in rows)
    table = document.add_table(rows=0, cols=width)
    try:
        table.style = "Table Grid"
    except Exception:
        pass
    for row in rows:
        cells = table.add_row().cells
        for idx in range(width):
            value = row[idx] if idx < len(row) else ""
            cells[idx].text = " ".join(str(value or "").split())


def pdf_to_docx(sources: list[Path], ctx: OpContext) -> Path:
    try:
        from docx import Document
        from docx.shared import Pt
    except ImportError as e:
        raise RuntimeError("python-docx is not installed (pip install python-docx)") from e

    src = sources[0]
    final = ctx.out(src.stem, ".docx", source=src)
    document = Document()
    keep_breaks = bool(ctx.options.get("page_breaks", True))

    with pdfengine.open_pdf(src) as doc:
        total = doc.page_count
        for i in range(total):
            ctx.check_cancel()
            if i % 5 == 0 or total < 50:
                ctx.progress(3 + 94 * i / total, f"Page {i + 1}/{total}")
            page = doc.page(i)
            tables = page.tables()
            spans = page.spans()
            sizes = [s.size for s in spans if s.size]
            body = median(sizes) if sizes else 11.0

            blocks: list[tuple[float, str, object]] = []
            for box, rows in tables:
                blocks.append((box[1], "table", rows))
            for span in spans:
                cx = (span.bbox[0] + span.bbox[2]) / 2
                cy = (span.bbox[1] + span.bbox[3]) / 2
                if any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b, _ in tables):
                    continue
                text = " ".join(span.text.split())
                if not text:
                    continue
                if span.size >= body * 1.45:
                    kind = "h1"
                elif span.size >= body * 1.15 or (span.bold and len(text) < 90):
                    kind = "h2"
                else:
                    kind = "p"
                blocks.append((span.bbox[1], kind, text))

            blocks.sort(key=lambda b: b[0])
            for _, kind, payload in blocks:
                if kind == "table":
                    _add_table(document, payload)
                elif kind == "h1":
                    document.add_heading(str(payload), level=1)
                elif kind == "h2":
                    document.add_heading(str(payload), level=2)
                else:
                    para = document.add_paragraph(str(payload))
                    if para.runs:
                        para.runs[0].font.size = Pt(11)

            if not blocks:
                document.add_paragraph(f"[page {i + 1}: no extractable text]")
            if keep_breaks and i < total - 1:
                document.add_page_break()
            if i % 50 == 0:
                doc.release_layout()

    tmp = ctx.tmp("out.docx")
    document.save(str(tmp))
    ctx.finish(tmp, final)
    ctx.progress(100, f"Done — {total} pages")
    return final


def docx_to_pdf(sources: list[Path], ctx: OpContext) -> Path:
    """Word via COM if present, else LibreOffice (MPL-2.0). Neither is bundled."""
    src = sources[0]
    final = ctx.out(src.stem, ".pdf", source=src)
    stage = ctx.tmpdir("docx2pdf")

    try:
        from docx2pdf import convert as word_convert
        ctx.progress(20, "Converting via Microsoft Word …")
        tmp_out = stage / f"{src.stem}.pdf"
        word_convert(str(src), str(tmp_out))
        if tmp_out.exists() and tmp_out.stat().st_size:
            ctx.finish(tmp_out, final)
            ctx.progress(100, "Done")
            return final
    except Exception as e:
        ctx.log(f"Word unavailable ({type(e).__name__}); trying LibreOffice.")

    soffice = config.find_soffice()
    if not soffice:
        raise RuntimeError("Neither Microsoft Word nor LibreOffice is available "
                           "for DOCX → PDF conversion.")
    ctx.progress(45, "Converting via LibreOffice …")
    res = ctx.run([soffice, "--headless", "--norestore", "--convert-to", "pdf",
                   "--outdir", str(stage), str(src)], timeout=3600)
    produced = stage / f"{src.stem}.pdf"
    if res.returncode != 0 or not produced.exists():
        raise RuntimeError(f"LibreOffice failed: {res.stdout.strip()[:300]}")
    ctx.finish(produced, final)
    ctx.progress(100, "Done")
    return final
