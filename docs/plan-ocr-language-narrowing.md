# Plan — never silently lose a language during OCR (+ restore the Flet UI)

Status: **APPROVED by the user, 2 Aug 2026.** Cleared for execution.

Two parts:
- **Part A** — OCR language narrowing silently destroys Hindi content. The main fix.
- **Part B** — the Flet UI cannot start; the app has been silently falling back to Tkinter.

---

## 1. Goal

**A.** A document containing Hindi, Telugu or any other selected language must
render that language correctly in the output Markdown. It must never be OCR'd
with a language model that cannot represent its script, silently producing
convincing Latin gibberish. Where the app cannot read something confidently, it
says so rather than inventing plausible text.

**B.** The primary Flet UI must actually launch. If it ever does fall back to
Tkinter, the user must be told, not left to assume they are seeing the real UI.

---

## 2. Evidence — the confirmed defects

### Part A

Source: `RCD SOR 2021 Volume I.pdf` (734 pages, 724 MiB, Bihar Road Construction
Department schedule of rates), output line ~708.

| Stage | Result |
|---|---|
| Legacy font detection | **correct** — `uses_legacy_font()` returns `IRUAHW+KrutiDev010,Bold`, forcing OCR |
| Raw text layer | `iFk fuekZ.k foHkkx` (KrutiDev Latin-byte mojibake — correctly rejected) |
| `detect_scripts()` over 6 sampled pages of 734 | `{'Latin', 'Cyrillic'}` — **misses Devanagari**, and hallucinates Cyrillic |
| `narrow_langs('eng+hin+tel', {...})` | `'eng'` — **drops `hin` and `tel`** |
| OCR with `-l eng` | `qa fate faart` ← the reported gibberish |
| OCR with `-l eng+hin` | `पथ निर्माण विभाग` ← correct |

Root cause: **document-wide language narrowing based on a 0.8% page sample
overrode deterministic per-page evidence that Devanagari was present.**

Installed packs are not the issue — the bundled Tesseract at
`D:\FileForge\tools\tesseract\` carries `ben eng guj hin kan mal mar ori osd
pan san tam tel urd`.

### Part B

```
flet 0.86.5 installed;  flet-desktop  NOT installed
→ ft.app() attempts a runtime auto-install of flet-desktop, which fails
→ crashes inside flet's own pip helper: NameError: name 'exit' is not defined
→ main.py catches it and falls back to Tkinter *silently*
```

`requirements.txt` pins `flet>=0.24`, which now resolves to 0.86.5, where
desktop support lives in a separate `flet-desktop` package. Exactly the risk
CLAUDE.md §6 flagged as unverified.

---

## 3. Constraints and invariants (must not break)

- `CLAUDE.md` §2.1 — permissive licences only. `flet-desktop` is Apache-2.0
  (same project as Flet); **no other new dependency**. Never reintroduce
  pymupdf/fitz/pymupdf4llm/pdf2docx/img2pdf/docxcompose/PyQt.
- `CLAUDE.md` §2.2 — never fabricate document content. Part A *serves* this rule.
- `CLAUDE.md` §4 — `narrow_langs()` must continue to **only narrow, never add** a
  language the user did not select. This is non-negotiable and has a test.
- `CLAUDE.md` §4 — legacy-font matching stays **token-prefix, never substring**
  (`Arial-Caps` contains "aps"; `Manuscript` contains "anu").
- The performance rationale for narrowing is real and must be preserved where it
  is safe: running every model on every page is slower *and* less accurate.
- All OCR work stays behind `app/ocr.py`; all PDF access behind `app/pdfengine.py`.
- One bad file must never kill a batch: catch, log, mark failed, continue.
- Long loops must call `ctx.progress()` / `ctx.check_cancel()`.
- `app/ui.py` (Tkinter) must remain functional as a fallback.
- No network calls at runtime, ever.

---

## 4. Design decision (confirmed with the user)

Narrowing is **kept, with hard rails**. It may no longer act on weak evidence,
and it may never override strong evidence.

Two grades of evidence:

- **Strong / deterministic** — the font resources on the page. `KrutiDev010`
  *is* a Devanagari font; that is a structural fact of the PDF, readable without
  rendering anything.
- **Weak / statistical** — Tesseract OSD over a handful of sampled page images.
  Demonstrably noisy (it reported Cyrillic in a Bihar rate schedule).

Rule: **weak evidence may never remove a language that strong evidence supports,
and weak evidence drawn from too thin a sample may not narrow at all.**

Confirmed choices:
- Narrowing kept with rails (not disabled, not removed) — preserves the speed
  benefit on genuinely single-language batches.
- **Font-scan refinement: IN.** Font names are read across *all* pages up front,
  cheaply and without rendering, and become the primary narrowing signal. OSD
  sampling is demoted to a secondary signal for pages that have no text layer at
  all (true scans), where fonts cannot help.
- Forced-OCR pages whose expected script is missing get an **inline
  `> [!WARNING]` block in the Markdown**, not just a log line, so the caveat
  travels with the file into the chat.

---

## PART A — steps

### Step A1 — map legacy fonts to their scripts (`app/ops/convert_markdown.py`)
`LEGACY_FONT_HINTS` is currently a flat tuple with the script recorded only in
comments. Restructure into a mapping of hint → script name
(`"kruti"` → `"Devanagari"`, `"godavari"` → `"Telugu"`, `"tscii"` → `"Tamil"`, …),
preserving **every existing hint** and the token-prefix matching semantics
exactly. Add `legacy_font_script(font_name) -> str` returning the script or `""`.
`_legacy_match()` must keep working identically for existing callers and tests.

### Step A2 — upfront font scan across the whole document (`app/ops/convert_markdown.py`)
Before the first narrowing decision, walk every page's font resources via the
existing `Page.fonts()` (pypdf, no rendering) and build
`font_scripts: set[str]`. This is the font-scan refinement.
- Must call `ctx.progress()` and `ctx.check_cancel()` — 734 pages is not instant.
- Must be resilient: a page whose resources cannot be parsed is skipped, not fatal.
- Bound the work: if the document exceeds ~3000 pages, sample evenly rather than
  walking every page.

### Step A3 — feed strong evidence into narrowing (`app/ops/convert_markdown.py`)
Union `font_scripts` into the script set before calling `narrow_langs()`, so a
Devanagari legacy font guarantees `hin`/`mar`/`san`/`nep` survive. Because the
font scan covers *all* pages, absence of any Telugu font across 734 pages is now
positive evidence that `tel` may safely be dropped — this is what makes the fix
both correct **and** faster than the status quo on this document.

### Step A4 — require an adequate sample before OSD may narrow (`app/ocr.py`)
Add a coverage guard: OSD-derived scripts may only narrow when sampled pages are
at least `max(12, 2% of OCR-bound pages)`. Below that, OSD contributes nothing.
Raise the default `samples` from 6 to 12.

### Step A5 — drop OSD's known-spurious scripts (`app/ocr.py`)
Ignore a detected script that maps to no selected language **and** appears in
only one sampled page, so single-page noise (the phantom Cyrillic) cannot
influence the outcome.

### Step A6 — re-check narrowing as new evidence arrives (`app/ops/convert_markdown.py`)
`langs_resolved` currently latches `True` for the whole run. Re-evaluate when new
strong evidence appears mid-document and widen back toward the user's selection
if so. **Never widen beyond the user's own selection.**

### Step A7 — post-OCR script sanity check (`app/ops/convert_markdown.py`)
If a page was forced to OCR *because* of a legacy Indic font, but its OCR output
contains no character in that script's Unicode range, emit:
- a visible line in the run log, and
- an inline block on that page in the Markdown:

```
> [!WARNING]
> This page uses a legacy Devanagari font but no Devanagari text was
> recovered. Verify against the original before relying on this page.
```

This is the "admitted gap beats a wrong answer" guarantee from CLAUDE.md §1.

### Step A8 — make the narrowing decision loud (`app/ops/convert_markdown.py`)
Log which languages were used, which were dropped, on what evidence, and how
many pages were sampled/scanned. Include the language set in the end-of-run
summary so the user can spot this class of problem themselves.

---

## PART B — steps

### Step B1 — add the missing dependency (`requirements.txt`)
Add `flet-desktop` (Apache-2.0, same project as Flet) alongside `flet>=0.24`.
Consider pinning a floor that matches the Flet major in use (0.86.x).

### Step B2 — ship it in the frozen build (`FileForge.spec`)
Add `flet_desktop` to `collect_all(...)` / `hiddenimports` so PyInstaller
actually bundles it. **Do not** add any banned package while editing this file.

### Step B3 — never fall back silently (`main.py`)
The Tkinter fallback is correct behaviour and must stay, but the user must be
told it happened — a clear console/log message naming the reason, and, once the
Tkinter window is up, a visible note that this is the fallback interface.

### Step B4 — verify the Flet UI actually renders
Launch it for real via `Run-Dev.bat`. Confirm the window opens, the file picker
opens, Advanced settings expand, and the language chooser lists the installed
packs. This code has never been executed (CLAUDE.md §6) — expect API drift and
fix what breaks, keeping the existing compat shims.

---

## 5. Unit tests to add (`tests/test_suite.py`)

| Test | Pins down |
|---|---|
| `test_legacy_font_script_mapping` | `KrutiDev010`→Devanagari, `Godavari`→Telugu, `Shree-Tel-0800`→Telugu, `TSCII`→Tamil; modern fonts → `""` |
| `test_legacy_match_unchanged` | every existing legacy/modern font case from `test_legacy_fonts` still classifies identically (no regression from the restructure) |
| `test_strong_evidence_survives_narrowing` | `narrow_langs('eng+hin+tel', {'Latin','Devanagari'})` keeps `hin` |
| `test_font_scan_absence_allows_narrowing` | no Telugu font anywhere ⇒ `tel` may be dropped |
| `test_narrowing_refuses_thin_sample` | 6 sampled pages of 734 ⇒ OSD contributes no narrowing |
| `test_narrowing_still_works_on_dense_sample` | genuinely all-Latin doc, adequate sample ⇒ still narrows to `eng` (perf preserved) |
| `test_single_page_spurious_script_ignored` | a one-off `Cyrillic` cannot influence the result |
| `test_narrow_langs_never_adds` | unchanged invariant — detection can never introduce an unselected language |
| `test_script_mismatch_warns` | forced-OCR page whose output lacks the expected script produces a warning, not silence |
| `test_warning_block_wellformed` | the emitted `> [!WARNING]` block is valid Markdown and does not break the page split |

Tests must keep running **without** Tesseract installed (the suite gates builds
on any machine) — stub or skip the engine-dependent parts accordingly.

---

## 6. Edge cases to handle explicitly

- Document with **no** legacy fonts and no OCR pages — narrowing path untouched.
- Legacy font present but user selected only `eng` — must **not** add `hin`.
- Tesseract absent entirely — no crash; existing "install Tesseract" tip stands.
- Requested pack not installed (`hin` selected, only `eng` present) — warn
  clearly that the language cannot be read; never silently emit gibberish.
- Document mixing three scripts (English + Hindi + Telugu on one page).
- Single-page document (sample size ≥ page count).
- `detect_scripts()` raising or timing out — fall back to the full selection.
- Font scan on an encrypted/damaged page — skip that page, continue.
- Cancellation mid-scan and mid-detection.
- Non-ASCII path to the PDF (logging already fixed in `78e6dab`).
- **724 MiB / 734-page document** — the added font scan must not measurably slow
  the run; it reads font dictionaries only, never renders.

---

## 7. Acceptance criteria

1. `python tests/test_suite.py` → **all pass** (currently 62/62; expect ~72).
2. Converting page 1 of `RCD SOR 2021 Volume I.pdf` yields `पथ निर्माण विभाग`
   and `बिहार सरकार`, **not** `qa fate faart`.
3. On that same document the chosen language set is `eng+hin` (Telugu correctly
   dropped on whole-document font evidence) — correct *and* faster than the
   pre-fix full set.
4. The run log states which languages were used and, if any were dropped, why.
5. A control document that is genuinely all-English still narrows to `eng`.
6. `Run-Dev.bat` launches the **Flet** UI (not the Tkinter fallback) and the
   window renders.
7. No banned dependency appears in `requirements.txt` or `FileForge.spec`.

---

## 8. Out of scope

- Re-converting the user's existing 734-page output — a separate run once merged.
- General OCR tuning (DPI, PSM).
- Transcoding KrutiDev→Unicode directly instead of OCR. **Deliberately excluded:**
  a mis-mapped transcode produces silent, confident errors in legal evidence,
  which is exactly what this plan exists to prevent.
- **Telugu is not verifiable against a real document** — the user has none to
  hand. Telugu logic is covered by unit tests only, and the final report must
  say so plainly rather than implying real-document coverage.
