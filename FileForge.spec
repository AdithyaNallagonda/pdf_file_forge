# PyInstaller spec — build with:  pyinstaller FileForge.spec --noconfirm
# Produces dist/FileForge/  (one-dir: fast startup, and tools/ can sit beside the exe)
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(os.getcwd())

datas = []
binaries = []
hiddenimports = []

# Some of these packages carry data files (tessdata-adjacent resources, CJK
# encoding tables, etc.) that static analysis cannot see, so collect_all() is
# required. Keep this list in sync with requirements.txt / THIRD_PARTY_LICENSES.md
# -- every entry here must be permissively licensed (see CLAUDE.md 2.1).
for pkg in ("pypdfium2", "pypdfium2_raw", "pdfplumber", "pdfminer", "pypdf",
            "pikepdf", "PIL", "flet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception as exc:
        print(f"[spec] skipping {pkg}: {exc}")

# python-docx ships a default.docx template it loads at runtime
datas += collect_data_files("docx")
# pdfminer (pdfplumber's parser) carries CJK/encoding resource tables
datas += collect_data_files("pdfminer")

# Optional helper binaries staged by build.ps1 into tools/ — shipped verbatim.
if (ROOT / "tools").is_dir():
    for f in (ROOT / "tools").rglob("*"):
        if f.is_file():
            datas.append((str(f), str(Path("tools") / f.parent.relative_to(ROOT / "tools"))))

# NEVER add pymupdf / fitz / pymupdf4llm / pdf2docx / img2pdf / docxcompose /
# PyQt here: all are AGPL/LGPL/GPL and banned from this closed-source build
# (CLAUDE.md 2.1). This project already paid for one AGPL removal refactor.
hiddenimports += [
    "pypdfium2", "pdfplumber", "pdfminer", "pdfminer.high_level", "pypdf", "pikepdf",
    "PIL.Image", "PIL.ImageFile", "pytesseract",
    "docx",
    # docx2pdf drives Word over COM
    "win32com", "win32com.client", "pythoncom", "pywintypes",
]
hiddenimports += ["app.ops.figures", "app.ops._markdown_build", "app.pdfengine",
                  "app.batch", "app.ocr", "app.registry", "app.settings",
                  "app.progress", "app.flet_ui", "app.ui",
                  "app.ops.compress", "app.ops.convert_docx",
                  "app.ops.convert_image", "app.ops.convert_markdown",
                  "multiprocessing.pool", "concurrent.futures.process"]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Trim the biggest dead weight; none of these are used at runtime.
    excludes=["matplotlib", "scipy", "pandas", "notebook", "IPython",
              "tkinter.test", "test", "pytest", "setuptools._distutils"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FileForge",
    console=False,              # no console window for the end user
    disable_windowed_traceback=False,
    icon="assets/fileforge.ico" if (ROOT / "assets/fileforge.ico").exists() else None,
    version="version_info.txt" if (ROOT / "version_info.txt").exists() else None,
)

coll = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False,     # UPX trips Windows Defender heuristics — leave it off
    name="FileForge",
)
