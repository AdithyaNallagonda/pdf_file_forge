"""Global configuration and external-tool discovery."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "FileForge"
FROZEN = getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Folder holding the installed app (exe dir when frozen, repo root otherwise)."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled(*parts: str) -> Path | None:
    """Look for a helper binary shipped alongside the app."""
    for root in (app_dir() / "tools", Path(getattr(sys, "_MEIPASS", app_dir())) / "tools"):
        cand = root.joinpath(*parts)
        if cand.exists():
            return cand
    return None

# ---- Workspace roots -------------------------------------------------------
def _base_dir() -> Path:
    r"""Workspace root, per platform.

    Windows: D:\FileForge if that drive exists (the original brief), else
    %LOCALAPPDATA%. macOS: ~/Library/Application Support. Linux: XDG data dir.
    """
    if sys.platform == "win32":
        try:
            if Path("D:/").exists():
                d = Path("D:/") / APP_NAME
                d.mkdir(parents=True, exist_ok=True)
                return d
        except OSError:
            pass
        root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    fallback = root / APP_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


BASE_DIR = _base_dir()
TEMP_ROOT = BASE_DIR / "temp"          # all intermediates, wiped after each run
RESULT_DIR = BASE_DIR / "result_files"  # final deliverables, never auto-deleted
LOG_DIR = BASE_DIR / "logs"

for _p in (TEMP_ROOT, RESULT_DIR, LOG_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---- Defaults --------------------------------------------------------------
MB = 1024 * 1024
DEFAULT_TARGET_MB = 500
OCR_CHAR_THRESHOLD = 60   # page text shorter than this => treat page as scanned
PDF_CHUNK_PAGES = 40      # page batch size for memory-bounded conversions
DEFAULT_SPLIT_MB = 5      # split Markdown into parts of this size (0 disables)
P4LLM_WINDOW = 32         # pages processed per extraction window


# ---- External binaries -----------------------------------------------------
def _which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def find_ghostscript() -> str | None:
    b = bundled("gs", "bin", "gswin64c.exe")
    if b:
        return str(b)
    exe = _which("gswin64c", "gswin32c", "gs")
    if exe:
        return exe
    for root in (r"C:\Program Files\gs", r"C:\Program Files (x86)\gs"):
        base = Path(root)
        if base.is_dir():
            cands = sorted(base.glob("*/bin/gswin*c.exe"), reverse=True)
            if cands:
                return str(cands[0])
    return None


def find_tesseract() -> str | None:
    b = bundled("tesseract", "tesseract.exe") or bundled("tesseract", "tesseract")
    if b:
        os.environ.setdefault("TESSDATA_PREFIX", str(b.parent / "tessdata"))
        return str(b)
    exe = _which("tesseract")
    if exe:
        return exe
    for cand in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                 "/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract",
                 "/usr/bin/tesseract"):
        if Path(cand).exists():
            return cand
    return None


def find_soffice() -> str | None:
    exe = _which("soffice", "libreoffice")
    if exe:
        return exe
    for root in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                 "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                 "/usr/bin/soffice", "/usr/bin/libreoffice"):
        if Path(root).exists():
            return root
    return None
