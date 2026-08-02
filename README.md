# FileForge

> **Status:** the PDF engine migration (off AGPL PyMuPDF) and the Flet UI have
> never been executed against a real document. See `CLAUDE.md` §6 for exactly
> what is verified and what is not, and the priority list for validating it.

Offline Windows desktop toolkit for PDF / Word / image / Markdown conversion.
Nothing leaves the machine.

## Build the distributable (developer machine, Windows x64)

```powershell
winget install UB-Mannheim.TesseractOCR   # embeds OCR into the build
winget install JRSoftware.InnoSetup       # produces the installer
powershell -ExecutionPolicy Bypass -File build.ps1
```

Produces `dist\FileForge\FileForge.exe` (~180–260 MB one-dir) and
`dist\FileForge-Setup.exe`. The build script installs deps into a throwaway
`.buildenv`, stages Tesseract into `tools\`, freezes with PyInstaller, launches the
exe as a smoke test, and compiles the installer. `-SkipInstaller` stops after the exe.

Ship **`FileForge-Setup.exe` + `CLIENT_GUIDE.md`** to the client. The installer is
per-user (`PrivilegesRequired=lowest`), so it needs no admin rights, and it adds a
Start Menu entry, an optional desktop icon, and an optional right-click
*"Convert to Markdown"* verb on PDFs.

## Run from source (development)

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1   # venv + Start Menu shortcut
```

Or `FileForge.bat` for a console window while debugging.

### Optional native helpers

| Tool | Enables | Install |
|---|---|---|
| Ghostscript | Text-preserving PDF compression. **Not embedded** — it is AGPL, so shipping it inside a proprietary installer creates source-disclosure obligations. Only the compress action needs it | `winget install ArtifexSoftware.GhostScript` |
| Tesseract-OCR | Text extraction from scanned PDFs — **embedded by `build.ps1`**, Apache-2.0 so redistribution is fine | `winget install UB-Mannheim.TesseractOCR` |
| LibreOffice | DOCX→PDF when MS Word is absent | `winget install TheDocumentFoundation.LibreOffice` |

Each is optional — the app detects what is present at launch and degrades gracefully.

## Capabilities

| Input | Action | Engine |
|---|---|---|
| PDF | Compress to ≤ N MB (default 500) | Ghostscript ladder → raster fallback |
| PDF | → Word `.docx` | pdf2docx, batched + docxcompose merge |
| PDF | → Markdown | pymupdf4llm, auto-OCR, figure description, split into parts |
| PDF | → PNG pages | PyMuPDF |
| DOCX | → PDF | MS Word COM → LibreOffice fallback |
| Images | → single PDF | Pillow + img2pdf |

## Launch modes

```
FileForge.exe                              open empty
FileForge.exe a.pdf b.pdf c.pdf            preloaded (drag files onto the icon)
FileForge.exe --op pdf_to_md "a.pdf"       preload and start immediately
```

`--op` takes any `Operation.id` from the registry; the Explorer context-menu verb
uses `--op pdf_to_md`. Dragging a multi-selection onto the shortcut passes every path
to a single process, so a 40-file batch is one run, not 40.

## Tests

```
python tests/test_suite.py       # 38 checks, no PyMuPDF/Tesseract needed
```

`build.ps1` runs this and aborts the build on failure (`-SkipTests` to override).
Each test named "Regression:" in its docstring guards a bug found in adversarial
review and fixed:

| Bug found | Impact | Fix |
|---|---|---|
| `ctx.out()` used check-then-create | **Silent data loss.** 16 concurrent workers produced 2 filenames; outputs overwrote each other in folder batches | Atomic reservation via `O_CREAT\|O_EXCL` / `mkdir`, plus parent-folder provenance so `Case A/claim.pdf` and `Case B/claim.pdf` stay distinct |
| `OpContext.run()` read stdout after exit | **Deadlock.** Any child writing past the ~64 KB pipe buffer blocked forever; LibreOffice and Tesseract both do | Child output redirected to a temp file, never a pipe |
| `purge_temp_root()` wiped all of `temp/` at launch | Opening a second window destroyed the first window's in-flight job | Age-gated: only dirs untouched for 6 h |
| `_ordered()` re-stat'd during sort | A file deleted mid-scan aborted the entire folder walk | Missing files sort last instead of raising |
| Sequential batches shared one scratch dir keyed on `hash(name)` | Two files named `claim.pdf` collided; the second inherited the first's exported figures | UUID-keyed scratch per document |
| `docx_to_pdf` ran in the process pool | Word COM cannot be driven concurrently | `Operation.parallel_safe=False` forces sequential, with a note in the log |
| Worker death propagated | `BrokenProcessPool` from one OOM child aborted the whole batch | Per-future exception capture; that file is marked failed, the rest continue |
| No filename sanitisation | Reserved names (`CON`), `*?<>`, 400-char stems and path separators reached the filesystem | `_safe_name()` sanitises, bounds length, guards device names |
| `figures.analyse()` walked every vector path | A dense CAD sheet could hold 100k+ paths | Structure reconstruction capped at 4000 paths; description still emitted |

## Folder batches and parallelism

`app/batch.py` walks folders (`collect_files`, recursive by default) and runs a
per-file operation across a `ProcessPoolExecutor`. Notes:

* Parallelism is **across files, never within one** — no shared state, no locks,
  and a crash in one worker cannot corrupt another's output.
* The app's own workspace is excluded from discovery, so `result_files` can never
  be fed back in as input.
* Files are queued **smallest first**: results appear early, and a 5 GB outlier
  cannot hold a worker while the rest of the pool idles.
* Default workers = `min(4, cores - 1)`. Raise it for many small documents; lower
  it for multi-GB files, since each worker holds its own document in memory.
* `mp.get_context("spawn")` is explicit (Windows has no fork), and `main.py` calls
  `multiprocessing.freeze_support()` — without it the frozen exe would respawn its
  own GUI once per worker.
* Per-file progress is not reported in parallel mode; completions are.

## Known limitations

Stated rather than hidden — none has a clean fix at this layer:

* **Cancelling a batch does not kill in-flight documents.** The queue drains
  immediately, but a file already converting runs to completion; killing a worker
  mid-write would leave a truncated output. Cancel is prompt, not instant.
* **Windows MAX_PATH.** Deep case folders plus long names can exceed 260
  characters. Enable long paths (Group Policy, or `LongPathsEnabled=1` under
  `HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`) on the client machine.
* **Charts are described, not read.** Axis labels and legends survive; the plotted
  values do not. Reading quantities off vector geometry is unreliable, and in
  evidence a wrong number is worse than an absent one.
* **A scanned exhibit inside a digital PDF is not OCR'd.** Page triage sees the
  surrounding text layer and marks the page "digital", so the exhibit's own text
  is missed. `Force OCR` is the workaround.
* **Script narrowing is per document, not per page**, from a six-page sample. A
  lone Hindi annexure at page 480 of an English file may be missed; untick
  auto-narrow for such files.
* **The build is unsigned**, so SmartScreen warns on first run.

## Performance notes

Measured nowhere — instrument before trusting any of it. The reasoning:

* **OCR dominates scanned work**, so that is where the effort went (`app/ocr.py`):
  - one `tesseract` invocation per batch via a *list file* instead of one process
    per page (pytesseract spawns ~150 ms of startup + model load per page);
  - grayscale, `alpha=False` pixmaps — Tesseract binarises anyway, so RGB meant
    producing, writing and decoding 3x the pixels for nothing;
  - `OMP_THREAD_LIMIT=1` per worker: Tesseract uses OpenMP internally and N of
    them in a pool fight over the same cores;
  - `--oem 1` (LSTM only, skips the slower legacy engine) and `--psm 3`.
* **Language packs** are embedded by `build.ps1 -Languages eng,hin,tel,…`, copied
  from the local Tesseract install or fetched from Google's tessdata repo
  (Apache-2.0). `ocr.list_languages()` reads `--list-langs` so the UI only offers
  installed packs, and `normalise_langs()` silently drops uninstalled ones rather
  than failing the run.
* **Legacy Indic font detection** (`uses_legacy_font`). Pre-Unicode fonts
  (Kruti Dev, Shree Lipi, Anu, Godavari, APS) produce a text layer of Latin
  codepoints mapped to Indic glyphs — extraction yields convincing mojibake, and
  triage would wrongly mark the page "digital". Matching is token-prefix based,
  not substring: `Arial-Caps` contains "aps" and `Manuscript` contains "anu".
  Detected pages are routed to OCR and never fall back to the text layer.
  A manual `force_ocr` option covers fonts not on the list.
* **Page triage before extraction.** A raw `get_text()` is cheap; the ONNX layout
  model is not. Pages with no text layer skip the rich extractor entirely and go
  straight to OCR, instead of paying for layout analysis that returns nothing.
* **Windowed rich extraction.** `pymupdf4llm` repeats document-level setup per
  call, so it gets `P4LLM_WINDOW` (32) pages at a time with `page_chunks=True`.
* **Cores go to one place at a time.** One file => parallel OCR *within* it
  (`ocr_workers`); many files => parallel *across* files with `ocr_workers=1`.
  Nested pools only oversubscribe.
* **GC disabled across the page loop** with explicit per-window `gc.collect()`.
  The cyclic collector finds little here and scanning costs real time at volume.

## Handling multi-GB files

* **Compression** predicts the correct quality rung from `target ÷ current` size, so a
  5 GB → 500 MB job starts at rung 4 rather than burning three full Ghostscript passes.
  Every pass writes to disk; the smallest result wins. If Ghostscript can't reach the
  target, pages are rasterised one at a time to JPEG on disk and reassembled by
  `img2pdf` — constant memory, guaranteed size, at the cost of the text layer.
* **PDF→DOCX** converts in 40-page slices (`config.PDF_CHUNK_PAGES`) and stitches them.
* **PDF→Markdown** streams output page-by-page and flushes every 25 pages; the whole
  document is never held in RAM. Output rolls over to a new part at the size budget,
  always on a page boundary, and parts are staged in temp until the final count is
  known so each can be headed *"Part 3 of 7 — pages 61–94 of 300"*.
* **Figures** (`app/ops/figures.py`) are detected from vector-path counts and image
  rects, then described inline: type, coverage, caption, and every short text span
  found inside the artwork bbox. Box-and-line diagrams are reconstructed as Mermaid
  by `build_mermaid()`, which refuses (returns `None`) unless ≥3 labelled boxes, ≥2
  edges, and every node participates — a wrong diagram in evidence is worse than an
  admitted gap. ASCII art is deliberately not attempted. Pure-geometry helpers take
  plain tuples and are unit-testable without PyMuPDF.
* **Markdown extraction** fetches `config.P4LLM_WINDOW` (32) pages per
  `pymupdf4llm` call with `page_chunks=True`. The earlier one-call-per-page loop
  repeated that library's document-level setup on every single page.
* **Sequential batches** run one file per `ctx.scoped()` slice, so the progress bar is global.
  A file that throws is logged and skipped; the run continues.

## Workspace

```
D:\FileForge\
  temp\job_<uuid>\   scratch — deleted in a finally: block on success, error, or cancel
  result_files\      final outputs, collision-safe names, never auto-deleted
  logs\fileforge.log
```

Stale `temp\` dirs from a crash are purged at launch and on window close.
If `D:` is unavailable the workspace falls back to `%LOCALAPPDATA%\FileForge`.

## Adding a new file type or action

Two steps, no UI edits — the buttons are generated from the registry:

```python
# app/ops/convert_xlsx.py
def xlsx_to_csv(sources: list[Path], ctx: OpContext) -> Path:
    out = ctx.out(sources[0].stem, ".csv")
    ...                     # ctx.tmp()/ctx.tmpdir() for scratch,
    ctx.progress(50, "…")   # ctx.progress() also polls for cancellation
    return out

# app/registry.py -> _build()
register(Operation(id="xlsx_to_csv", label="Excel → CSV",
                   extensions=(".xlsx",), handler=convert_xlsx.xlsx_to_csv,
                   description="Export the first sheet."))
```

Add the extension to `FILE_TYPES` so the picker offers it. Handlers run on a worker
thread; call `ctx.progress()` or `ctx.check_cancel()` inside loops to stay responsive.
Set `batch=True` to have the runner call the handler once per file on a multi-select,
`multi=True` if the handler consumes the whole list itself, `primary=True` to star it.

New third-party imports must be added to `hiddenimports` in `FileForge.spec` —
PyInstaller's static analysis will not find anything imported lazily inside a function,
which is how the ops modules import their engines.
