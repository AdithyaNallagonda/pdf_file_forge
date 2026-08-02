"""PDF → Markdown, tuned for feeding documents into an LLM.

Per page: use the native text layer where there is one (headings inferred from
font size, tables from pdfplumber), and fall back to Tesseract OCR when the page
is a scan. Output is streamed page-by-page to disk, so file size is irrelevant
to memory use. All extraction goes through app.pdfengine — permissive licences
only, no PyMuPDF.
"""
from __future__ import annotations

import gc
import io
import uuid
import re
import shutil
from pathlib import Path

from .. import config, ocr, pdfengine
from ..workspace import OpContext
from . import figures
from ._markdown_build import page_markdown


# Legacy pre-Unicode Indic fonts. A PDF using these HAS a text layer, but the
# bytes are Latin codepoints mapped onto Devanagari/Telugu glyphs by the font —
# extraction yields convincing-looking mojibake. These pages must be OCR'd even
# though the triage says they carry text.
LEGACY_FONT_HINTS = (
    # Devanagari (Hindi/Marathi)
    "kruti", "krutidev", "devlys", "shree", "shreelipi", "chanakya", "agra",
    "dvb-", "dv-", "aps", "akruti", "walkman", "amarujala", "jagran", "bhaskar",
    "shivaji", "yogeshweb", "kundli", "millennium", "richa", "sanskrit99",
    # Telugu / Kannada / Tamil
    "anu", "godavari", "priyanka", "hemalatha", "vijaya", "eenadu", "shree-tel",
    "tlot", "tlwg", "bharat", "elango", "amudham", "tab-", "tam-", "tscii",
    "nudi", "baraha", "shree-kan", "shree-tam",
    # generic legacy encodings
    "isfoc", "gist", "susha", "ml-", "mlw", "subak", "sakal",
)


def _legacy_match(font_name: str) -> bool:
    """Token-aware match. Plain substring search is far too loose: 'Arial-Caps'
    contains 'aps' and 'Manuscript' contains 'anu'. Split on non-alphanumerics
    and require a whole-token prefix (exact match for very short codes)."""
    base = font_name.lower().split("+")[-1]
    tokens = [t for t in re.split(r"[^a-z0-9]+", base) if t]
    for tok in tokens:
        for hint in LEGACY_FONT_HINTS:
            h = hint.strip("-")
            if not h:
                continue
            if (tok == h) or (len(h) > 3 and tok.startswith(h)):
                return True
    return False


def uses_legacy_font(page) -> str:
    """Return the offending font name if this page relies on a legacy Indic font."""
    try:
        for name in page.fonts():
            if name and _legacy_match(name):
                return name
    except Exception:
        pass
    return ""


class _RollingWriter:
    """Writes Markdown page-by-page, rolling to a new part at a size budget.

    Splits only on page boundaries, so no sentence is ever cut in half. Parts are
    staged in temp and only assembled into result_files at the end, once the
    final part count is known (needed for the 'part 2 of 7' headers).
    """

    def __init__(self, stage: Path, limit_bytes: int):
        self.stage = stage
        self.limit = limit_bytes
        self.notes: dict[int, str] = {}      # page -> what is on it
        self.assets: Path = stage / "assets"
        self.parts: list[tuple[Path, int, int]] = []   # path, first page, last page
        self._fh = None
        self._written = 0
        self._first = 1
        self._last = 1

    def _open(self, page: int) -> None:
        path = self.stage / f"body_{len(self.parts) + 1:03d}.md"
        self._fh = open(path, "w", encoding="utf-8", newline="\n")
        self.parts.append((path, page, page))
        self._written = 0
        self._first = page

    def write_page(self, page: int, text: str) -> None:
        if self._fh is None:
            self._open(page)
        elif self.limit and self._written >= self.limit:
            self.close_part()
            self._open(page)
        chunk = f"\n<!-- page {page} -->\n\n{text}\n\n---\n"
        self._fh.write(chunk)
        self._written += len(chunk.encode("utf-8"))
        self._last = page
        path, first, _ = self.parts[-1]
        self.parts[-1] = (path, first, page)
        if page % 25 == 0:
            self._fh.flush()

    def note(self, page: int, what: str) -> None:
        if what:
            self.notes[page] = what

    def _manifest(self, first: int, last: int) -> str:
        rows = [f"- p.{pg} — {self.notes[pg]}"
                for pg in range(first, last + 1) if pg in self.notes]
        if not rows:
            return ""
        return ("## Contents of this part\n\n"
                "Pages carrying figures or tables; all other pages are text.\n\n"
                + "\n".join(rows) + "\n")

    def close_part(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def finalize(self, ctx: OpContext, src: Path, total_pages: int) -> Path:
        self.close_part()
        n = len(self.parts)
        if n == 0:
            out = ctx.out(src.stem, ".md", source=src)
            out.write_text(f"# {src.stem}\n\n*No extractable content.*\n", encoding="utf-8")
            return out

        def header(idx: int, first: int, last: int) -> str:
            h = [f"# {src.stem}"]
            if n > 1:
                h.append(f"**Part {idx} of {n} — pages {first}–{last} of {total_pages}.** "
                         f"Other parts of this document are in the same folder.")
            else:
                h.append(f"Source: `{src.name}` — {total_pages} pages.")
            return "\n\n".join(h) + "\n"

        has_assets = self.assets.is_dir() and any(self.assets.iterdir())
        if n == 1 and not has_assets:
            out = ctx.out(src.stem, ".md", source=src)
            body, first, last = self.parts[0]
            with open(out, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(header(1, first, last))
                dst.write(self._manifest(first, last))
                with open(body, "r", encoding="utf-8") as fh:
                    shutil.copyfileobj(fh, dst)
            return out

        folder = ctx.out(f"{src.stem}_markdown", "", source=src)
        folder.mkdir(parents=True, exist_ok=True)
        index = [f"# {src.stem} — {n} parts\n",
                 f"Source: `{src.name}`, {total_pages} pages. "
                 f"Upload the parts to the chat in order.\n"]
        for i, (body, first, last) in enumerate(self.parts, 1):
            out = folder / f"{src.stem}_part{i:02d}.md"
            with open(out, "w", encoding="utf-8", newline="\n") as dst:
                dst.write(header(i, first, last))
                dst.write(self._manifest(first, last))
                with open(body, "r", encoding="utf-8") as fh:
                    shutil.copyfileobj(fh, dst)
            index.append(f"- `{out.name}` — pages {first}–{last}")
        if has_assets:
            dest = folder / "figures"
            dest.mkdir(exist_ok=True)
            for img in sorted(self.assets.iterdir()):
                shutil.move(str(img), str(dest / img.name))
            index.append(f"\nPage images are in `figures\\` — upload the ones you "
                         f"need alongside the Markdown.")
        (folder / "_INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        return folder


def pdf_to_markdown(sources: list[Path], ctx: OpContext) -> Path:
    src = sources[0]
    opts = ctx.options
    split_mb = float(opts.get("split_mb", config.DEFAULT_SPLIT_MB))
    want_figs = bool(opts.get("describe_figures", True))
    want_mermaid = bool(opts.get("mermaid", True))
    want_export = bool(opts.get("export_figures", False))
    ocr_dpi = int(opts.get("ocr_dpi", 300))
    ocr_lang = str(opts.get("ocr_lang", ocr.DEFAULT_LANGS))
    force_ocr = bool(opts.get("force_ocr", False))
    auto_langs = bool(opts.get("auto_langs", True))
    langs_resolved = False
    ocr_workers = int(opts.get("ocr_workers", 1))
    want_tables = bool(opts.get("extract_tables", True))
    tess = config.find_tesseract()
    if tess:
        available = ocr.list_languages(tess)
        chosen = ocr.normalise_langs(ocr_lang, available)
        if chosen != ocr_lang:
            missing = [l for l in ocr_lang.split("+") if l not in chosen.split("+")]
            ctx.log(f"Language pack(s) not installed: {', '.join(missing)} — "
                    f"reading with '{chosen}' instead.")
        ocr_lang = chosen

    # uuid, not hash(name): in a sequential batch every file shares one temp
    # dir, so two documents called claim.pdf collided and the second inherited
    # the first's exported figures.
    writer = _RollingWriter(ctx.tmpdir(f"md_{uuid.uuid4().hex[:10]}"),
                            int(split_mb * config.MB))
    ocr_used = empty = fig_count = mermaid_count = legacy_seen = 0
    ocr_root = ctx.tmpdir("ocr")

    # The page loop is allocation-heavy; the cyclic collector finds almost nothing
    # and costs real time at this volume. Disabled, with explicit sweeps instead.
    gc_was_on = gc.isenabled()
    gc.disable()

    try:
        with pdfengine.open_pdf(src) as doc:
            total = doc.page_count
            window = max(8, int(config.P4LLM_WINDOW))

            for start in range(0, total, window):
                ctx.check_cancel()
                span = list(range(start, min(start + window, total)))
                ctx.progress(3 + 95 * start / total,
                             f"Pages {span[0] + 1}–{span[-1] + 1} of {total}")

                # -- triage: which pages actually have a text layer? ----------
                # A raw get_text() is cheap; running the ONNX layout model over
                # scanned pages that hold no text at all is not.
                raw: dict[int, str] = {}
                forced: dict[int, str] = {}
                for i in span:
                    try:
                        pg = doc.page(i)
                        raw[i] = pg.text().strip()
                        if force_ocr:
                            forced[i] = "forced by setting"
                        elif raw[i]:
                            bad = uses_legacy_font(pg)
                            if bad:
                                forced[i] = bad
                    except Exception:
                        raw[i] = ""
                digital = [i for i in span
                           if len(raw[i]) >= config.OCR_CHAR_THRESHOLD and i not in forced]
                scanned = [i for i in span if i not in digital]
                if forced and legacy_seen == 0:
                    sample = next(iter(forced.values()))
                    ctx.log(f"Legacy non-Unicode font detected ({sample}) — "
                            f"those pages will be read by OCR instead, "
                            f"otherwise the text would extract as garbage.")
                legacy_seen += len(forced)

                # -- figures: analysed once per page, up front ----------------
                # Reused below both to keep figure-region text out of the plain
                # paragraph flow (page_markdown) and to describe the figure —
                # analysing twice would cost the same geometry scan twice for
                # nothing.
                figs: dict[int, figures.Figure | None] = {}
                if want_figs:
                    for i in span:
                        ctx.check_cancel()
                        try:
                            figs[i] = figures.analyse(doc.page(i), want_mermaid=want_mermaid)
                        except Exception as exc:
                            figs[i] = None
                            ctx.log(f"Figure scan failed on page {i + 1} "
                                    f"({type(exc).__name__}) — continuing.")

                # -- layout extraction, digital pages only --------------------
                rich: dict[int, str] = {}
                for i in digital:
                    ctx.check_cancel()
                    try:
                        fig = figs.get(i)
                        # Regression: a caption or in-drawing label used to appear
                        # twice — garbled in the ordinary paragraph flow (short,
                        # out-of-reading-order text) and again, properly, under
                        # the figure's own description block. Exclude the
                        # figure's own footprint from the flowing text.
                        exclude = [fig.bbox] if fig else []
                        rich[i] = page_markdown(doc.page(i), want_tables=want_tables,
                                                exclude_boxes=exclude)
                    except Exception as exc:
                        ctx.log(f"Layout extraction failed on page {i + 1} "
                                f"({type(exc).__name__}) — using raw text.")
                        rich[i] = raw.get(i, "")

                # -- OCR, batched and optionally parallel ---------------------
                # Narrow the language set once, from a sample spread across the
                # whole document — running every selected model on every page is
                # both slower and less accurate than running only what is there.
                if scanned and tess and auto_langs and not langs_resolved:
                    langs_resolved = True
                    try:
                        scripts = ocr.detect_scripts(src, list(range(total)),
                                                     ocr_root, tess)
                        if scripts:
                            narrowed = ocr.narrow_langs(ocr_lang, scripts)
                            ctx.log(f"Scripts found: {', '.join(sorted(scripts))} — "
                                    f"reading with '{narrowed}'"
                                    + (f" (was '{ocr_lang}')" if narrowed != ocr_lang else ""))
                            ocr_lang = narrowed
                    except Exception as exc:
                        ctx.log(f"Script detection skipped ({type(exc).__name__}).")

                ocr_map: dict[int, str] = {}
                if scanned and tess:
                    ctx.progress(3 + 95 * start / total,
                                 f"Reading {len(scanned)} scanned page(s) "
                                 f"near {span[0] + 1}/{total}")
                    ocr_map = ocr.ocr_pages(
                        src, scanned, ocr_root, tess, dpi=ocr_dpi, lang=ocr_lang,
                        workers=ocr_workers, is_cancelled=ctx.check_cancel_flag)

                # -- assemble -------------------------------------------------
                for i in span:
                    ctx.check_cancel()
                    page = doc.page(i)
                    text = rich.get(i, "")
                    if (len(text.strip()) < config.OCR_CHAR_THRESHOLD
                            and raw[i] and i not in forced):
                        text = raw[i]
                    if len(text.strip()) < config.OCR_CHAR_THRESHOLD:
                        got = ocr_map.get(i, "")
                        if got.strip():
                            text = got
                            ocr_used += 1
                    text = text.strip()
                    if not text:
                        empty += 1

                    note = []
                    rows = sum(1 for ln in text.splitlines() if ln.startswith("|"))
                    if rows >= 3:
                        note.append(f"table (~{rows - 2} rows)")

                    if want_figs:
                        fig = figs.get(i)
                        if fig:
                            fig_count += 1
                            if want_export:
                                try:
                                    fig.image_file = "figures/" + figures.export_image(
                                        page, writer.assets, i + 1,
                                        dpi=int(opts.get("image_dpi", 150)))
                                except Exception:
                                    pass
                            if fig.mermaid:
                                mermaid_count += 1
                            text = (text + "\n\n" if text else "") + figures.render(fig, i + 1)
                            note.append(fig.summary() +
                                        (" + structure recovered" if fig.mermaid else ""))

                    writer.note(i + 1, "; ".join(note))
                    writer.write_page(i + 1, text or "*[no extractable content]*")

                raw.clear(); rich.clear(); ocr_map.clear(); forced.clear(); figs.clear()
                doc.release_layout()      # pdfplumber caches parsed page objects
                gc.collect()
    finally:
        if gc_was_on:
            gc.enable()

    final = writer.finalize(ctx, src, total)
    notes = [f"{total} pages"]
    if len(writer.parts) > 1:
        notes.append(f"{len(writer.parts)} parts")
    if fig_count:
        notes.append(f"{fig_count} figures described")
    if mermaid_count:
        notes.append(f"{mermaid_count} diagrams reconstructed")
    if ocr_used:
        notes.append(f"{ocr_used} OCR'd ({ocr_lang})")
    if legacy_seen:
        notes.append(f"{legacy_seen} legacy-font pages re-read")
    if empty:
        notes.append(f"{empty} empty")
    if empty and not tess:
        ctx.log("Tip: install Tesseract-OCR to extract text from scanned pages.")
    ctx.progress(100, "Done — " + ", ".join(notes))
    return final
