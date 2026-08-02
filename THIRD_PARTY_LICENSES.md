# Third-party components

Every dependency below is permissively licensed and may be redistributed inside
a proprietary, commercially sold application. Reproduce this file in the
installer and link it from any About dialog.

| Component | Licence | Redistributable in closed source | Used for |
|---|---|---|---|
| pypdfium2 / PDFium | Apache-2.0 / BSD-3 | yes | page rendering, raw text |
| pdfplumber | MIT | yes | layout, tables, vector geometry |
| pdfminer.six | MIT | yes | PDF parsing (via pdfplumber) |
| pypdf | BSD-3 | yes | structure, font resources |
| pikepdf | MPL-2.0 | yes (file-level copyleft; unmodified use is fine) | lossless optimise, image recompression |
| QPDF | Apache-2.0 | yes | pikepdf's C++ engine |
| Pillow | MIT-CMU | yes | image encode/decode, PDF assembly |
| python-docx | MIT | yes | DOCX writing |
| pytesseract | Apache-2.0 | yes | Tesseract wrapper |
| Tesseract OCR + tessdata | Apache-2.0 | yes | OCR engine and language packs |
| docx2pdf | MIT | yes | optional; needs Microsoft Word installed |
| Flet | Apache-2.0 | yes | desktop UI |
| PyInstaller | GPL with a distribution exception | yes — the exception explicitly permits shipping frozen proprietary apps | packaging |
| Inno Setup | modified BSD-style | yes | installer |

## Deliberately excluded

| Component | Licence | Why excluded |
|---|---|---|
| PyMuPDF / MuPDF | AGPL-3.0, or paid commercial licence from Artifex | AGPL requires releasing the whole application's source. Replaced by pypdfium2 + pdfplumber + pypdf. |
| pymupdf4llm | AGPL-3.0 (Artifex) | Same. Replaced by `app/ops/_markdown_build.py`. |
| pdf2docx | built on PyMuPDF | Inherits the obligation. Replaced by a python-docx writer. |
| img2pdf | LGPL-3.0 | Relinking obligations are awkward inside a frozen binary. Replaced by Pillow. |
| docxcompose | LGPL | No longer needed — DOCX is written in one pass. |
| Ghostscript | AGPL-3.0 | **Never bundled.** Used only if the operator installed it themselves, which is aggregation, not distribution. |
| LibreOffice | MPL-2.0 | Not bundled; invoked only if already installed. |

## Notes

* MPL-2.0 (pikepdf) is file-level copyleft: modifications to *its* files must be
  published, but linking from proprietary code is permitted. Do not patch pikepdf.
* Apache-2.0 requires the NOTICE file be preserved — keep this document shipped.
* This is a summary, not legal advice. Have counsel review before you sell.
