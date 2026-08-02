"""PDF engine — permissive licences only.

Replaces PyMuPDF/pymupdf4llm (AGPL, Artifex commercial licence otherwise
required) with a stack that can be shipped in a proprietary product:

    pypdfium2   Apache-2.0 / BSD-3   rendering + fast raw text (Google PDFium)
    pdfplumber  MIT                  layout, tables, vector shapes
    pdfminer.six MIT                 (pdfplumber's parser)
    pypdf       BSD-3                structure, fonts, page ops
    pikepdf     MPL-2.0              QPDF bindings (QPDF itself Apache-2.0)
    Pillow      MIT-CMU              image encode/decode, PDF assembly

Division of labour matters for speed. PDFium is fast and handles rendering and
plain text; pdfplumber is markedly slower but is the only one here that gives
character-level geometry, tables and vector paths. So pdfplumber is opened
lazily and only for pages that actually need layout analysis.

Everything is behind this module, so swapping a backend later touches one file.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

BBox = tuple[float, float, float, float]


class EngineError(RuntimeError):
    pass


def _require(mod: str, pip: str):
    try:
        return __import__(mod)
    except ImportError as exc:                       # pragma: no cover
        raise EngineError(f"{mod} is not installed (pip install {pip})") from exc


@dataclass
class Span:
    bbox: BBox
    text: str
    size: float
    bold: bool
    font: str


@dataclass
class PageGeometry:
    """Vector content of a page, in PDF points."""
    rects: list[BBox] = field(default_factory=list)
    lines: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)
    curves: int = 0
    images: list[BBox] = field(default_factory=list)

    @property
    def path_count(self) -> int:
        return len(self.rects) + len(self.lines) + self.curves


class Page:
    def __init__(self, doc: "Document", index: int):
        self.doc = doc
        self.index = index

    # ---------------------------------------------------------------- fast
    @property
    def rect(self) -> BBox:
        pg = self.doc._pdfium[self.index]
        w, h = pg.get_size()
        return (0.0, 0.0, float(w), float(h))

    def text(self) -> str:
        """Raw text via PDFium — the cheap triage signal."""
        try:
            pg = self.doc._pdfium[self.index]
            tp = pg.get_textpage()
            try:
                return tp.get_text_bounded() or ""
            finally:
                tp.close()
        except Exception:
            return ""

    def render_png(self, dpi: int = 200, gray: bool = False) -> bytes:
        pg = self.doc._pdfium[self.index]
        bitmap = pg.render(scale=dpi / 72.0, grayscale=gray, draw_annots=True)
        try:
            image = bitmap.to_pil()
            if gray and image.mode != "L":
                image = image.convert("L")
            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=False)
            return buf.getvalue()
        finally:
            try:
                bitmap.close()
            except Exception:
                pass

    def save_png(self, path: Path, dpi: int = 200, gray: bool = False) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render_png(dpi=dpi, gray=gray))
        return path

    def save_jpeg(self, path: Path, dpi: int = 150, quality: int = 70) -> Path:
        from PIL import Image
        pg = self.doc._pdfium[self.index]
        bitmap = pg.render(scale=dpi / 72.0, draw_annots=True)
        try:
            image = bitmap.to_pil()
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, "JPEG", quality=quality, optimize=True)
            return path
        finally:
            try:
                bitmap.close()
            except Exception:
                pass

    def fonts(self) -> list[str]:
        """BaseFont names from the page resources — cheap, via pypdf."""
        try:
            page = self.doc._pypdf.pages[self.index]
            res = page.get("/Resources")
            if res is None:
                return []
            fonts = res.get_object().get("/Font")
            if fonts is None:
                return []
            out = []
            for ref in fonts.get_object().values():
                try:
                    base = ref.get_object().get("/BaseFont")
                    if base:
                        out.append(str(base).lstrip("/"))
                except Exception:
                    continue
            return out
        except Exception:
            return []

    # ------------------------------------------------------------ detailed
    def _plumber(self):
        return self.doc._plumber_page(self.index)

    def spans(self) -> list[Span]:
        """Text lines with geometry and font metadata (pdfplumber)."""
        pg = self._plumber()
        if pg is None:
            return []
        out: list[Span] = []
        try:
            for line in pg.extract_text_lines(strip=True) or []:
                chars = line.get("chars") or []
                size = max((c.get("size", 0) or 0) for c in chars) if chars else 0.0
                font = str(chars[0].get("fontname", "")) if chars else ""
                bold = any("bold" in str(c.get("fontname", "")).lower() for c in chars)
                out.append(Span((line["x0"], line["top"], line["x1"], line["bottom"]),
                                line.get("text", ""), float(size), bold, font))
        except Exception:
            return []
        return out

    def tables(self) -> list[tuple[BBox, list[list[str]]]]:
        """Tables WITH their bounding box, so body text inside them can be
        suppressed and the table placed in correct reading order."""
        pg = self._plumber()
        if pg is None:
            return []
        out = []
        try:
            for tbl in pg.find_tables() or []:
                rows = tbl.extract()
                if rows and len(rows) >= 2:
                    out.append((tuple(tbl.bbox), rows))
        except Exception:
            return []
        return out

    def geometry(self, max_objects: int = 60000) -> PageGeometry:
        pg = self._plumber()
        geo = PageGeometry()
        if pg is None:
            return geo
        try:
            for r in (pg.rects or [])[:max_objects]:
                geo.rects.append((r["x0"], r["top"], r["x1"], r["bottom"]))
            for ln in (pg.lines or [])[:max_objects]:
                geo.lines.append(((ln["x0"], ln["top"]), (ln["x1"], ln["bottom"])))
            geo.curves = len(pg.curves or [])
            for im in (pg.images or [])[:max_objects]:
                geo.images.append((im["x0"], im["top"], im["x1"], im["bottom"]))
        except Exception:
            pass
        return geo


class Document:
    """Lazily opens each backend: PDFium always, pypdf and pdfplumber on demand."""

    def __init__(self, path: Path):
        self.path = Path(path)
        pdfium = _require("pypdfium2", "pypdfium2")
        self._pdfium = pdfium.PdfDocument(str(self.path))
        self._pypdf_doc = None
        self._plumber_doc = None

    # -- lifecycle
    def __enter__(self) -> "Document":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        for obj in (self._plumber_doc, self._pdfium):
            try:
                if obj is not None:
                    obj.close()
            except Exception:
                pass
        self._plumber_doc = None
        self._pypdf_doc = None

    # -- backends
    @property
    def _pypdf(self):
        if self._pypdf_doc is None:
            pypdf = _require("pypdf", "pypdf")
            self._pypdf_doc = pypdf.PdfReader(str(self.path))
        return self._pypdf_doc

    def _plumber_page(self, index: int):
        try:
            if self._plumber_doc is None:
                pdfplumber = _require("pdfplumber", "pdfplumber")
                self._plumber_doc = pdfplumber.open(str(self.path))
            return self._plumber_doc.pages[index]
        except Exception:
            return None

    def release_layout(self) -> None:
        """Drop pdfplumber's per-page caches; it holds parsed objects."""
        try:
            if self._plumber_doc is not None:
                for pg in self._plumber_doc.pages:
                    try:
                        pg.flush_cache()
                    except Exception:
                        pass
        except Exception:
            pass

    # -- access
    @property
    def page_count(self) -> int:
        return len(self._pdfium)

    def page(self, index: int) -> Page:
        return Page(self, index)

    def pages(self) -> Iterator[Page]:
        for i in range(self.page_count):
            yield self.page(i)


def open_pdf(path: Path) -> Document:
    return Document(path)


def images_to_pdf(images: list[Path], out: Path) -> Path:
    """Assemble images into a PDF with Pillow (MIT) — replaces img2pdf (LGPL)."""
    from PIL import Image
    if not images:
        raise EngineError("no images to assemble")
    first = Image.open(images[0])
    try:
        rest = []
        for p in images[1:]:
            im = Image.open(p)
            rest.append(im.convert("RGB") if im.mode not in ("RGB", "L") else im)
        base = first.convert("RGB") if first.mode not in ("RGB", "L") else first
        base.save(out, "PDF", save_all=True, append_images=rest, resolution=150.0)
    finally:
        try:
            first.close()
        except Exception:
            pass
    return out
