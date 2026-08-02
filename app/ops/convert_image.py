"""Image → PDF and PDF → images."""
from __future__ import annotations

from pathlib import Path

from .. import pdfengine
from ..workspace import OpContext


def images_to_pdf(sources: list[Path], ctx: OpContext) -> Path:
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("Pillow is not installed (pip install pillow)") from e

    ordered = sorted(sources, key=lambda p: p.name.lower())
    stem = ordered[0].stem if len(ordered) == 1 else f"{ordered[0].stem}_and_{len(ordered) - 1}_more"
    final = ctx.out(stem, ".pdf", source=ordered[0])

    prepared: list[Path] = []
    stage = ctx.tmpdir("img")
    for i, p in enumerate(ordered):
        ctx.progress(5 + 70 * i / len(ordered), f"Preparing {p.name}")
        try:
            with Image.open(p) as im:
                # img2pdf rejects alpha and non-RGB modes; normalise those only.
                if im.mode in ("RGBA", "LA", "P", "CMYK", "1"):
                    conv = stage / f"{i:05d}.jpg"
                    im.convert("RGB").save(conv, "JPEG", quality=92, optimize=True)
                    prepared.append(conv)
                    continue
        except Exception as e:
            raise RuntimeError(f"Cannot read image {p.name}: {e}") from e
        prepared.append(p)

    ctx.progress(85, "Assembling PDF …")
    pdfengine.images_to_pdf(prepared, final)
    ctx.progress(100, f"Done — {len(prepared)} page(s)")
    return final


def pdf_to_images(sources: list[Path], ctx: OpContext) -> Path:
    src = sources[0]
    dpi = int(ctx.options.get("image_dpi", 200))
    outdir = ctx.out(f"{src.stem}_pages", "", source=src)
    outdir.mkdir(parents=True, exist_ok=True)

    with pdfengine.open_pdf(src) as doc:
        total = doc.page_count
        width = max(4, len(str(total)))
        for i in range(total):
            ctx.check_cancel()
            doc.page(i).save_png(outdir / f"page_{i + 1:0{width}d}.png", dpi=dpi)
            if i % 5 == 0 or total < 50:
                ctx.progress(2 + 97 * (i + 1) / total, f"Page {i + 1}/{total}")
    ctx.progress(100, f"Done — {total} PNG(s) at {dpi} DPI")
    return outdir
