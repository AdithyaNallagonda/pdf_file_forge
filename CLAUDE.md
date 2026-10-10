# FileForge — project context

Read this first. It carries the decisions, constraints and known-unknowns from
the design conversation that produced this code. Several of the rules below were
arrived at the hard way; changing them without reading the reasoning will
reintroduce bugs that have already been found and fixed once.

---

## 1. What this is, and who it is for

A local Windows/macOS/Linux desktop app that converts documents without sending
anything over a network. Primary user: a **retired lawyer in Hyderabad running
arbitration cases**, elderly, non-technical, working through *hundreds of PDFs
per case*. His workflow is:

> select a pile of PDFs → convert to Markdown → drag into a Claude chat → ask
> questions about the documents.

Everything else the app does (compress, PDF→Word, PDF→images, images→PDF) is
secondary to that one path.

Consequences that shape the whole design:

* Documents are legal evidence. **A wrong answer is worse than an admitted gap.**
  The app never fabricates content it cannot read — it says so instead.
* Documents are frequently **scanned** (old exhibits) and frequently in
  **Indian languages** (Hindi, Telugu, Tamil…), often mixed with English on the
  same page.
* Batches are large, so throughput and unattended reliability matter more than
  per-file polish.
* The UI must not intimidate. One obvious action; everything else demoted.

---

## 2. HARD CONSTRAINTS — do not violate

### 2.1 Licensing (commercial publication is planned)

The app is intended to be **sold as closed source**. Every dependency must be
permissively licensed. Licences are noted per dependency in `requirements.txt`.

**Never reintroduce these**, however convenient:

| Banned | Why |
|---|---|
| `pymupdf` / `fitz` / `pymupdf4llm` | AGPL-3.0 (Artifex). Requires releasing the whole app's source, or a paid commercial licence. This was already removed once — a large refactor. |
| `pdf2docx` | Built on PyMuPDF; inherits the obligation. |
| `img2pdf` | LGPL-3.0; relinking obligations are awkward in a frozen binary. |
| `docxcompose` | LGPL. |
| PyQt | GPL or paid commercial. |
| Ghostscript **bundled** | AGPL. It may be *invoked* if the operator installed it themselves (aggregation), but must never be shipped. |

Approved stack: pypdfium2 (Apache/BSD), pdfplumber + pdfminer.six (MIT), pypdf
(BSD), pikepdf (MPL, over Apache QPDF), Pillow (MIT), python-docx (MIT),
Tesseract + tessdata (Apache), Flet (Apache).

**All PDF access goes through `app/pdfengine.py`.** Do not import a PDF library
directly in an op. That module is the single seam for swapping backends.

### 2.2 Never fabricate document content

* Charts and drawings are **described** (type, coverage, caption, every text
  label found inside), never invented. **ASCII-art diagrams were explicitly
  considered and rejected** — an 80×40 grid cannot carry a plan sheet, and a
  plausible-but-wrong diagram in evidence is dangerous.
* Flowchart reconstruction (`figures.build_mermaid`) is deliberately
  conservative: ≥3 labelled boxes, ≥2 edges, every node connected, else return
  `None`. **Do not loosen these thresholds to increase coverage.**
* Any reconstructed diagram carries a "verify against the original" note.

### 2.3 Output integrity

* Reserve output paths **only** via `ctx.out(stem, suffix, source=…)` — atomic
  (`O_CREAT|O_EXCL` / `mkdir`). A previous check-then-create version silently
  lost 14 of 16 concurrent outputs.
* Move finished artefacts **only** via `ctx.finish(tmp, final)` (`os.replace`).
  `shutil.move` onto the reserved placeholder falls back to a full byte copy —
  ruinous for multi-GB files.
* Temp lives in a `session_temp()` context manager; cleanup is in a `finally`
  so it survives success, exception and cancellation alike.

---

## 3. Architecture

```
main.py                 entry point; Flet UI, falls back to Tkinter
app/
  config.py             paths (per-platform), external tool discovery
  settings.py           persisted user prefs, validated on load
  progress.py           byte-based ETA estimator
  workspace.py          OpContext (progress/cancel/temp/output), session_temp
  registry.py           Operation table — THE extension point
  batch.py              folder discovery + ProcessPoolExecutor across files
  ocr.py                Tesseract: batched, parallel, script detection
  pdfengine.py          the only place a PDF library is imported
  flet_ui.py            primary UI
  ui.py                 fallback Tkinter UI (keep working)
  ops/
    compress.py         pikepdf image recompression → raster fallback
    convert_markdown.py the main path: triage → extract/OCR → split
    _markdown_build.py  markdown assembly from engine primitives
    convert_docx.py     PDF→DOCX via python-docx; DOCX→PDF via Word/LibreOffice
    convert_image.py    PDF→PNG, images→PDF
    figures.py          figure detection, labels, Mermaid reconstruction
tests/test_suite.py     48 checks, runs without PyMuPDF/Tesseract
```

### Adding an operation
Write `handler(sources: list[Path], ctx: OpContext) -> Path` in `app/ops/`, then
add one `register(Operation(...))` in `registry._build()`. The UI generates its
buttons from that table. Flags: `batch=True` (run per file on a multi-select),
`multi=True` (handler consumes the whole list), `parallel_safe=False` (must run
sequentially — Word COM), `primary=True` (starred).

**Anything imported lazily inside a function must be added to `hiddenimports`
in `FileForge.spec`** or PyInstaller will not ship it.

### Key pipeline: PDF → Markdown
1. Window of 32 pages.
2. **Triage** per page: cheap `page.text()`. Has text → layout extraction.
   No text → OCR. Legacy Indic font detected → force OCR (see §4).
3. Layout extraction via `_markdown_build.page_markdown()` (headings by relative
   font size, tables from pdfplumber, text inside tables suppressed).
4. OCR batched through `ocr.ocr_pages()`.
5. Figures described inline; manifest of what is on each page.
6. `_RollingWriter` streams to disk and splits on **page boundaries** at a size
   budget; parts staged in temp until the final count is known, so headers can
   read "Part 3 of 7 — pages 61–94 of 300".

### Concurrency rule
Cores go to **one** place at a time. One file → parallel OCR *inside* it
(`ocr_workers`). Many files → parallel *across* files with `ocr_workers=1`.
Nested pools only oversubscribe. `mp.get_context("spawn")` is explicit;
`main.py` calls `multiprocessing.freeze_support()`.

---

## 4. Non-obvious domain knowledge

**Legacy Indic fonts.** Older Indian court documents use pre-Unicode fonts
(Kruti Dev, Shree Lipi, Anu, Godavari, APS). They *have* a text layer, but it is
Latin codepoints mapped to Indic glyphs — extraction yields convincing mojibake
and triage would wrongly call the page "digital". `uses_legacy_font()` detects
these and routes the page to OCR, never falling back to the text layer.
Matching is **token-prefix, not substring**: `Arial-Caps` contains "aps" and
`Manuscript` contains "anu". Do not simplify this to `in`.

**Language narrowing.** Tesseract handles mixed scripts in one pass
(`-l eng+hin+tel`). Running unnecessary models is slower *and* less accurate, so
`ocr.detect_scripts()` samples six pages and `narrow_langs()` drops selected
languages whose script is absent. It **only narrows, never adds**, and falls
back to the full selection if detection fails.

**OCR performance.** Three things dominated and were fixed: pytesseract spawning
a process per page (now one invocation per batch via a list file); RGB rendering
(now grayscale — Tesseract binarises anyway); OpenMP oversubscription (now
`OMP_THREAD_LIMIT=1` per worker). Plus `--oem 1`.

**Markdown splitting.** The default is 5 MB but the real constraint is the
context window, not the 30 MB upload limit — ~0.5 MB per part fits a
conversation better. Deliberately left as a user setting.

---

## 5. Bugs already found and fixed — do not reintroduce

Each has a regression test whose docstring says "Regression:".

1. `ctx.out()` check-then-create → 16 concurrent writers produced 2 filenames.
2. `OpContext.run()` read stdout after exit → any child writing past the ~64 KB
   pipe buffer deadlocked. Output now goes to a file, never a pipe.
3. `purge_temp_root()` wiped all of `temp/` at launch → a second window
   destroyed the first's in-flight job. Now age-gated at 6 h.
4. `_ordered()` re-stat'd during sort → a file deleted mid-scan aborted the walk.
5. Sequential batches shared one scratch dir keyed on `hash(name)` → two files
   named `claim.pdf` collided. Now UUID-keyed.
6. `docx_to_pdf` in the process pool → Word COM cannot be driven concurrently.
7. `BrokenProcessPool` from one dead worker aborted a whole batch.
8. No filename sanitisation → `CON`, `*?<>`, 400-char stems reached the FS.
9. `figures.analyse` walked every vector path on dense CAD sheets.

---

## 6. CURRENT STATE — what is verified and what is not

**This matters more than anything else in this file.**

Verified: `tests/test_suite.py` — 48 checks covering concurrency, filesystem
behaviour, text assembly, settings, ETA, language logic, Mermaid refusal.

**NOT verified — never executed against a real document:**

* **`app/pdfengine.py`** — written against pypdfium2 / pdfplumber / pypdf
  documentation, never run. Highest-risk file in the project.
* **`app/ops/_markdown_build.py`**, and the migrated `compress.py`,
  `convert_docx.py`, `convert_image.py`, `figures.py`, `ocr.py`.
* **`app/flet_ui.py`** — never run. Flet's API has moved between releases
  (`ft.icons`→`ft.Icons`, `page.window_width`→`page.window.width`); compat shims
  are in place but untested.
* Packaging: Flet ships its own Flutter runtime and PyInstaller is **not** its
  supported route (`flet pack` is). The spec's `collect_all("flet")` may fail or
  balloon the build.

### Priority tasks for this session

1. `pip install -r requirements.txt` in a clean venv; `python tests/test_suite.py`.
2. **Verify `pdfengine` against real PDFs.** Check every call actually exists and
   behaves: `PdfDocument`/`get_textpage().get_text_bounded()`, `render(scale=…,
   grayscale=…)`, `bitmap.to_pil()`, `pdfplumber` `extract_text_lines`,
   `find_tables().bbox`, `.rects/.lines/.curves/.images`, pypdf `/Resources`
   font walk. **Coordinate systems differ** — pdfplumber uses top-down `top`,
   PDFium/pypdf use bottom-up. `figures.py` and `_markdown_build.py` assume
   pdfplumber's convention throughout; confirm and fix if mixed.
3. Run each of the five operations end to end on: a digital text PDF, a scanned
   PDF, an Indian-language PDF, a PDF with drawings/tables, a multi-page batch.
4. `python main.py` for the Flet UI; `--classic-ui` for the Tkinter fallback.
5. Only then `Build-FileForge.bat`.

Generating test PDFs locally is fine (reportlab, or Pillow → PDF). Real
arbitration documents are better if any are available.

---

## 7. Conventions

* Comments explain **why**, not what. If a line encodes a hard-won decision,
  say so — several comments here are the only record of a bug that was fixed.
* Be honest in user-facing strings. "target NOT met — best effort" beats silence.
* One bad file must never kill a batch: catch, log, mark failed, continue.
* Long loops call `ctx.progress()` / `ctx.check_cancel()` so the UI stays live.
* No network calls at runtime, ever. It is a core promise of the product.
* Keep `app/ui.py` (Tkinter) functional as a fallback.
* Add a regression test for every bug fixed.

## 8. Build and run

```
python tests/test_suite.py            # must pass before building
python main.py                        # Flet UI from source
python main.py --classic-ui           # Tkinter fallback
Build-FileForge.bat                   # tests → PyInstaller → sign → installer
.\build.ps1 -SignThumbprint <sha1>    # with code signing
```

Workspace at runtime: `D:\FileForge` on Windows (or `~/Library/Application
Support/FileForge`, or XDG on Linux) → `temp/`, `result_files/`, `logs/`,
`settings.json`. **Note:** if the source tree lives at `D:\FileForge` too, the
app's runtime folders appear inside the repo. `.gitignore` covers them; consider
moving the source elsewhere to keep the two apart.
