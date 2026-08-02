"""PDF compression targeting an explicit output size.

Permissive licences only. Ghostscript (AGPL) is NOT required and never shipped;
if the user has installed it themselves it is used as an optional best-quality
path, which is aggregation rather than distribution.

Ladder, cheapest effective step first:
  1. Already under target -> lossless structural optimise via pikepdf/QPDF.
  2. Recompress the embedded images in place with pikepdf: downsample and
     re-encode each image XObject. The text layer survives intact, which is what
     matters for a searchable legal document.
  3. Ghostscript, only if the user happens to have it.
  4. Rasterise page-by-page to JPEG and rebuild. Constant memory, guarantees the
     target, loses the text layer.
"""
from __future__ import annotations

import io
import os
import shutil
from pathlib import Path

from .. import config, pdfengine
from ..workspace import OpContext, human

# (image DPI, JPEG quality) — decreasing fidelity
IMAGE_STEPS: list[tuple[int, int]] = [
    (250, 88), (200, 80), (150, 72), (120, 62), (96, 52), (72, 42),
]


def _predict_step(ratio: float) -> int:
    for idx, threshold in enumerate((0.85, 0.55, 0.35, 0.18, 0.09)):
        if ratio >= threshold:
            return idx
    return len(IMAGE_STEPS) - 1


def _lossless(src: Path, dst: Path) -> bool:
    try:
        import pikepdf
        with pikepdf.open(src) as pdf:
            pdf.save(dst, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate,
                     recompress_flate=True)
        return True
    except Exception:
        return False


def _recompress_images(src: Path, dst: Path, dpi: int, quality: int,
                       ctx: OpContext) -> bool:
    """Downsample and re-encode every embedded image, keeping the text layer."""
    try:
        import pikepdf
        from PIL import Image
    except ImportError:
        return False

    try:
        with pikepdf.open(src) as pdf:
            total = len(pdf.pages)
            touched = 0
            for pno, page in enumerate(pdf.pages):
                ctx.check_cancel()
                if pno % 20 == 0:
                    ctx.progress(15 + 45 * pno / max(1, total),
                                 f"Recompressing images, page {pno + 1}/{total}")
                try:
                    resources = page.get("/Resources")
                    xobjects = resources.get("/XObject") if resources is not None else None
                except Exception:
                    continue
                if xobjects is None:
                    continue
                for name, obj in list(xobjects.items()):
                    try:
                        if obj.get("/Subtype") != "/Image":
                            continue
                        pim = pikepdf.PdfImage(obj)
                        img = pim.as_pil_image()
                        w, h = img.size
                        if w * h < 40000:            # thumbnails/logos: leave alone
                            continue
                        # target pixel size for the page box at the chosen DPI
                        scale = min(1.0, (dpi * 8.5) / max(w, 1))
                        if scale < 1.0:
                            img = img.resize((max(1, int(w * scale)),
                                              max(1, int(h * scale))),
                                             Image.LANCZOS)
                        if img.mode not in ("RGB", "L"):
                            img = img.convert("RGB")
                        buf = io.BytesIO()
                        img.save(buf, "JPEG", quality=quality, optimize=True)
                        data = buf.getvalue()
                        if len(data) >= len(bytes(obj.read_raw_bytes())):
                            continue                 # no gain; keep the original
                        newobj = pikepdf.Stream(pdf, data)
                        newobj.Type = pikepdf.Name("/XObject")
                        newobj.Subtype = pikepdf.Name("/Image")
                        newobj.Width, newobj.Height = img.size
                        newobj.ColorSpace = pikepdf.Name(
                            "/DeviceGray" if img.mode == "L" else "/DeviceRGB")
                        newobj.BitsPerComponent = 8
                        newobj.Filter = pikepdf.Name("/DCTDecode")
                        xobjects[name] = newobj
                        touched += 1
                    except Exception:
                        continue
            pdf.save(dst, compress_streams=True,
                     object_stream_mode=pikepdf.ObjectStreamMode.generate)
        return touched > 0 or dst.exists()
    except Exception as exc:
        ctx.log(f"Image recompression failed ({type(exc).__name__}).")
        return False


def _ghostscript(src: Path, dst: Path, dpi: int, quality: int,
                 ctx: OpContext) -> bool:
    """Optional: only if the operator installed Ghostscript themselves."""
    gs = config.find_ghostscript()
    if not gs:
        return False
    cmd = [gs, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dNOPAUSE",
           "-dBATCH", "-dQUIET", "-dSAFER", "-dAutoRotatePages=/None",
           f"-dNumRenderingThreads={min(4, os.cpu_count() or 1)}",
           "-dDetectDuplicateImages=true", "-dCompressFonts=true",
           "-dDownsampleColorImages=true", f"-dColorImageResolution={dpi}",
           "-dDownsampleGrayImages=true", f"-dGrayImageResolution={dpi}",
           "-dAutoFilterColorImages=false", "-dColorImageFilter=/DCTEncode",
           f"-dJPEGQ={quality}", f"-sOutputFile={dst}", str(src)]
    res = ctx.run(cmd)
    return res.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def _rasterise(src: Path, dst: Path, ctx: OpContext, target: int) -> bool:
    """Last resort. Constant memory; the text layer does not survive."""
    for dpi, q in ((110, 65), (85, 55), (72, 45), (60, 35), (50, 28)):
        ctx.check_cancel()
        stage = ctx.tmpdir(f"raster_{dpi}")
        ctx.log(f"Rasterising at {dpi} DPI / q{q} …")
        pages: list[Path] = []
        running = 0
        try:
            with pdfengine.open_pdf(src) as doc:
                total = doc.page_count
                for i in range(total):
                    ctx.check_cancel()
                    jpg = doc.page(i).save_jpeg(stage / f"p{i:06d}.jpg",
                                                dpi=dpi, quality=q)
                    running += jpg.stat().st_size
                    pages.append(jpg)
                    if i % 10 == 0:
                        ctx.progress(70 + 25 * (i + 1) / total,
                                     f"Rasterising page {i + 1}/{total}")
            if running > target * 1.06:
                continue
            pdfengine.images_to_pdf(pages, dst)
            if dst.exists() and dst.stat().st_size <= target:
                return True
        except Exception as exc:
            ctx.log(f"Raster pass failed ({type(exc).__name__}).")
        finally:
            shutil.rmtree(stage, ignore_errors=True)
    return dst.exists() and dst.stat().st_size > 0


def compress_pdf(sources: list[Path], ctx: OpContext) -> Path:
    src = sources[0]
    target_mb = int(ctx.options.get("target_mb", config.DEFAULT_TARGET_MB))
    target = target_mb * config.MB
    original = src.stat().st_size
    ctx.log(f"Input: {human(original)}  |  target ≤ {target_mb} MB")

    final = ctx.out(f"{src.stem}_compressed", ".pdf", source=src)

    if original <= target:
        ctx.progress(30, "Already within target — optimising losslessly.")
        opt = ctx.tmp("lossless.pdf")
        if _lossless(src, opt) and opt.stat().st_size < original:
            ctx.finish(opt, final)
        else:
            shutil.copy2(src, final)
        ctx.progress(100, f"Done: {human(final.stat().st_size)}")
        return final

    best: Path | None = None
    best_size = original
    start = _predict_step(target / original)

    for step in range(start, len(IMAGE_STEPS)):
        ctx.check_cancel()
        dpi, quality = IMAGE_STEPS[step]
        ctx.progress(10 + 55 * (step - start + 1) / max(1, len(IMAGE_STEPS) - start),
                     f"Pass {step + 1}/{len(IMAGE_STEPS)} — images at {dpi} DPI, q{quality}")
        cand = ctx.tmp(f"img_{step}.pdf")
        ok = _recompress_images(src, cand, dpi, quality, ctx)
        if not ok or not cand.exists() or cand.stat().st_size == 0:
            gsc = ctx.tmp(f"gs_{step}.pdf")
            ok = _ghostscript(src, gsc, dpi, quality, ctx)
            cand = gsc if ok else cand
        if not cand.exists() or cand.stat().st_size == 0:
            continue
        size = cand.stat().st_size
        ctx.log(f"  → {human(size)}")
        if size < best_size:
            if best:
                best.unlink(missing_ok=True)
            best, best_size = cand, size
        else:
            cand.unlink(missing_ok=True)
        if best_size <= target:
            break

    if best_size > target:
        ctx.progress(70, "Target not met — rebuilding as compressed images.")
        raster = ctx.tmp("raster.pdf")
        if _rasterise(best or src, raster, ctx, target) and raster.stat().st_size < best_size:
            best, best_size = raster, raster.stat().st_size

    if best is None:
        raise RuntimeError("Compression produced no output.")

    ctx.finish(best, final)
    size = final.stat().st_size
    verdict = "target met" if size <= target else "target NOT met — best effort"
    ctx.progress(100, f"Done: {human(original)} → {human(size)} "
                      f"({100 - size * 100 // original}% smaller, {verdict})")
    return final
