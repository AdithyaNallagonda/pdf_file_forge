"""OCR engine.

Three things dominate OCR wall-clock, and all three are avoidable:

1. Process spawn. pytesseract launches a fresh tesseract.exe per image — roughly
   150 ms of startup and model load each time. Tesseract accepts a *list file*
   and will process an entire batch in one invocation, emitting pages separated
   by form feeds. On a 700-page volume that alone reclaims minutes.
2. Colour depth. Tesseract binarises immediately, so rendering RGB means
   producing, writing and decoding 3x the pixels for no gain. Grayscale, no alpha.
3. Thread oversubscription. Tesseract uses OpenMP internally; N of them inside a
   process pool fight for the same cores. OMP_THREAD_LIMIT=1 per worker is
   consistently faster, often even for a single document.

Rendering and recognition are also split across processes for a *single* large
document, which is the case cross-file parallelism cannot help with.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import shutil
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

PAGES_PER_TASK = 8          # render+OCR unit; keeps peak disk/RAM per worker low

# Tesseract codes -> display names. Indian languages first: this app is used on
# arbitration files from across India, where bilingual documents are the norm.
LANG_NAMES: dict[str, str] = {
    "eng": "English",
    "hin": "Hindi (हिन्दी)",
    "tel": "Telugu (తెలుగు)",
    "tam": "Tamil (தமிழ்)",
    "kan": "Kannada (ಕನ್ನಡ)",
    "mal": "Malayalam (മലയാളം)",
    "mar": "Marathi (मराठी)",
    "guj": "Gujarati (ગુજરાતી)",
    "ben": "Bengali (বাংলা)",
    "pan": "Punjabi (ਪੰਜਾਬੀ)",
    "ori": "Odia (ଓଡ଼ିଆ)",
    "asm": "Assamese (অসমীয়া)",
    "urd": "Urdu (اردو)",
    "san": "Sanskrit (संस्कृतम्)",
    "nep": "Nepali (नेपाली)",
    "sin": "Sinhala",
    "ara": "Arabic",
    "osd": "(orientation detection)",
}
# Sensible default for Hyderabad-based practice: English + Hindi + Telugu.
DEFAULT_LANGS = "eng+hin+tel"


def list_languages(tess: str | None) -> list[str]:
    """Language packs actually installed, so the UI never offers a broken option."""
    if not tess:
        return []
    try:
        res = subprocess.run([tess, "--list-langs"], capture_output=True, text=True,
                             env=_env(), creationflags=_flags(), timeout=60,
                             errors="replace")
        out = (res.stdout or "") + (res.stderr or "")
        langs = [ln.strip() for ln in out.splitlines()
                 if ln.strip() and " " not in ln.strip() and ":" not in ln]
        return sorted(set(langs))
    except Exception:
        return []


def normalise_langs(requested: str, available: list[str]) -> str:
    """Drop anything not installed; never return an empty language string."""
    if not available:
        return requested or "eng"
    want = [w for w in (requested or "").split("+") if w]
    keep = [w for w in want if w in available]
    if not keep:
        keep = ["eng"] if "eng" in available else available[:1]
    return "+".join(dict.fromkeys(keep))


def _env() -> dict:
    env = dict(os.environ)
    env["OMP_THREAD_LIMIT"] = "1"        # see module docstring, point 3
    return env


def _flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # type: ignore[attr-defined]


def ocr_images(tess: str, images: list[Path], lang: str = "eng",
               timeout: int = 1800) -> list[str]:
    """One tesseract invocation for the whole list. Falls back to per-image."""
    if not images:
        return []
    work = images[0].parent
    listing = work / "_pages.txt"
    listing.write_text("\n".join(str(p) for p in images), encoding="utf-8")
    outbase = work / "_out"
    cmd = [tess, str(listing), str(outbase), "-l", lang,
           "--oem", "1",      # LSTM only; the legacy engine is slower and no better here
           "--psm", "3"]      # full auto page segmentation, no orientation detection
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, env=_env(),
                             creationflags=_flags(), timeout=timeout, errors="replace")
        txt = outbase.with_suffix(".txt")
        if res.returncode == 0 and txt.exists():
            # tesseract separates pages of a batch with form feeds
            pages = txt.read_text(encoding="utf-8", errors="replace").split("\f")
            pages = [p for p in pages]
            if len(pages) >= len(images):
                return pages[:len(images)]
    except Exception:
        pass

    out: list[str] = []                   # fallback: one at a time
    for img in images:
        try:
            r = subprocess.run([tess, str(img), "stdout", "-l", lang, "--oem", "1",
                                "--psm", "3"], capture_output=True, text=True,
                               env=_env(), creationflags=_flags(), timeout=300,
                               errors="replace")
            out.append(r.stdout if r.returncode == 0 else "")
        except Exception:
            out.append("")
    return out


# Tesseract's OSD reports a *script*, not a language. One script can carry several
# languages, so a Devanagari page might be Hindi, Marathi, Sanskrit or Nepali.
SCRIPT_LANGS: dict[str, list[str]] = {
    "Latin": ["eng"],
    "Devanagari": ["hin", "mar", "san", "nep"],
    "Telugu": ["tel"],
    "Tamil": ["tam"],
    "Kannada": ["kan"],
    "Malayalam": ["mal"],
    "Gujarati": ["guj"],
    "Bengali": ["ben", "asm"],
    "Gurmukhi": ["pan"],
    "Oriya": ["ori"],
    "Arabic": ["urd", "ara"],
    "Han": [], "Japanese": [], "Korean": [], "Cyrillic": [], "Greek": [],
}


def detect_scripts(pdf: Path, pages: list[int], workroot: Path, tess: str,
                   samples: int = 6, dpi: int = 150) -> set[str]:
    """Which scripts actually appear, from a sample of pages.

    Sampling rather than per-page detection is deliberate: an OSD pass costs a
    process spawn, and running one per page would give back the very overhead
    the batching removed. Six evenly spaced pages is enough to tell a Telugu
    volume from an English one; a document that genuinely mixes scripts will
    show both in the sample and keep the full language set.
    """
    if not pages:
        return set()
    from . import pdfengine

    step = max(1, len(pages) // samples)
    picks = pages[::step][:samples]
    work = workroot / "osd"
    work.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    try:
        with pdfengine.open_pdf(Path(pdf)) as doc:
            for i in picks:
                img = work / f"osd_{i:06d}.png"
                try:
                    doc.page(i).save_png(img, dpi=dpi, gray=True)
                    res = subprocess.run([tess, str(img), "stdout", "--psm", "0",
                                          "-l", "osd"], capture_output=True, text=True,
                                         env=_env(), creationflags=_flags(),
                                         timeout=120, errors="replace")
                    for line in (res.stdout or "").splitlines():
                        if line.lower().startswith("script:"):
                            found.add(line.split(":", 1)[1].strip())
                except Exception:
                    continue
    except Exception:
        return set()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return found


def narrow_langs(selected: str, scripts: set[str]) -> str:
    """Keep only the selected languages whose script was actually seen.

    Never returns empty, and never *adds* a language the user did not choose —
    detection narrows the set, it does not override the person."""
    want = [w for w in (selected or "").split("+") if w]
    if not scripts or not want:
        return selected
    allowed: set[str] = set()
    for sc in scripts:
        allowed.update(SCRIPT_LANGS.get(sc, []))
    if not allowed:
        return selected
    keep = [w for w in want if w in allowed]
    return "+".join(keep) if keep else selected


def _task(payload: tuple[str, list[int], int, str, str, str]) -> list[tuple[int, str]]:
    """Render a slice of pages to grayscale PNG, then OCR them in one shot."""
    pdf, pages, dpi, lang, tess, workroot = payload
    from . import pdfengine

    work = Path(workroot) / f"ocr_{pages[0]:06d}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        images: list[Path] = []
        with pdfengine.open_pdf(Path(pdf)) as doc:
            for i in pages:
                # grayscale: Tesseract binarises anyway, so colour is wasted work
                images.append(doc.page(i).save_png(work / f"p{i:06d}.png",
                                                   dpi=dpi, gray=True))
        texts = ocr_images(tess, images, lang)
        return list(zip(pages, texts))
    except Exception:
        return [(i, "") for i in pages]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def ocr_pages(pdf: Path, pages: list[int], workroot: Path, tess: str,
              dpi: int = 300, lang: str = "eng", workers: int = 1,
              on_progress=None, is_cancelled=None) -> dict[int, str]:
    """OCR the given page indices, optionally across processes."""
    if not pages:
        return {}
    chunks = [pages[i:i + PAGES_PER_TASK] for i in range(0, len(pages), PAGES_PER_TASK)]
    payloads = [(str(pdf), c, dpi, lang, tess, str(workroot)) for c in chunks]
    result: dict[int, str] = {}
    done = 0

    if workers <= 1 or len(chunks) == 1:
        for pl in payloads:
            if is_cancelled and is_cancelled():
                break
            for idx, txt in _task(pl):
                result[idx] = txt
            done += len(pl[1])
            if on_progress:
                on_progress(done, len(pages))
        return result

    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=mp.get_context("spawn")) as pool:
        for batch in pool.map(_task, payloads):
            if is_cancelled and is_cancelled():
                break
            for idx, txt in batch:
                result[idx] = txt
            done += len(batch)
            if on_progress:
                on_progress(done, len(pages))
    return result
