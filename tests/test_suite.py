"""FileForge regression suite.

Runs without PyMuPDF/Tesseract so it can gate a build on any machine. Covers the
concurrency, filesystem and text-assembly logic — the parts where failures are
silent (a lost output file) rather than loud (a stack trace).

    python tests/test_suite.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import batch, ocr, registry                               # noqa: E402
from app.ops.convert_markdown import _RollingWriter, _legacy_match  # noqa: E402
from app.ops.figures import build_mermaid, pick_caption, cluster_ids  # noqa: E402
from app.workspace import Cancelled, OpContext, purge_temp_root, session_temp  # noqa: E402
from app import config                                             # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))


def ctx_in(tmp: Path, cancelled=lambda: False) -> OpContext:
    return OpContext(tmp, lambda p, m: None, cancelled, {})


# ---------------------------------------------------------------- concurrency
def test_output_reservation_is_atomic() -> None:
    """Regression: check-then-create raced; 12 workers produced 2 filenames."""
    with session_temp() as t:
        ctx = ctx_in(t)
        names, lock = [], threading.Lock()

        def grab():
            p = ctx.out("claim", ".md")
            time.sleep(0.002)
            p.write_text("x", encoding="utf-8")
            with lock:
                names.append(p.name)

        threads = [threading.Thread(target=grab) for _ in range(16)]
        [x.start() for x in threads]
        [x.join() for x in threads]
        check("out() unique under concurrency", len(set(names)) == 16,
              f"{len(set(names))}/16 unique")
        for f in ctx.result_dir.glob("claim*"):
            f.unlink()


def test_provenance_in_names() -> None:
    with session_temp() as t:
        ctx = ctx_in(t)
        a = ctx.out("claim", ".md", source=Path("/x/Case A/claim.pdf"))
        b = ctx.out("claim", ".md", source=Path("/x/Case B/claim.pdf"))
        check("same basename, different folders stay distinct", a != b)
        check("provenance appears in the name", "Case" in b.name)
        a.unlink(); b.unlink()


def test_subprocess_does_not_deadlock_on_large_output() -> None:
    """Regression: PIPE + read-after-exit hung on any chatty child."""
    with session_temp() as t:
        ctx = ctx_in(t)
        done = []
        def go():
            try:
                r = ctx.run([sys.executable, "-c", "print('x'*3000000)"], timeout=30)
                done.append(r.returncode == 0)
            except Exception:
                done.append(False)
        th = threading.Thread(target=go, daemon=True)
        th.start(); th.join(35)
        check("run() survives 3 MB of child output", bool(done) and done[0])


def test_temp_purge_spares_live_jobs() -> None:
    """Regression: launching a 2nd window wiped the 1st window's scratch."""
    live = config.TEMP_ROOT / "job_live_test"
    live.mkdir(parents=True, exist_ok=True)
    (live / "x.bin").write_bytes(b"in flight")
    purge_temp_root()
    check("purge spares a fresh job dir", live.exists())
    old = config.TEMP_ROOT / "job_old_test"
    old.mkdir(parents=True, exist_ok=True)
    ancient = time.time() - 30 * 3600
    os.utime(old, (ancient, ancient))
    purge_temp_root()
    check("purge removes a stale job dir", not old.exists())
    shutil.rmtree(live, ignore_errors=True)


# ---------------------------------------------------------------- filesystem
def test_hostile_filenames() -> None:
    with session_temp() as t:
        ctx = ctx_in(t)
        made = []
        for bad in ["CON", "a/b\\c", "n*w?<>", "..", "   ", "x" * 400,
                    "emoji_\U0001f525", "trail.", "évidence", "tab\tname"]:
            p = ctx.out(bad, ".md")
            p.write_text("x", encoding="utf-8")
            made.append(p)
        check("hostile names sanitised, none escape the output dir",
              all(m.parent == ctx.result_dir for m in made))
        check("names stay within Windows limits",
              all(len(m.name) <= 140 for m in made))
        check("reserved device names avoided",
              not any(m.stem.upper() == "CON" for m in made))
        for m in made:
            m.unlink()


def test_folder_scan() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        for i in range(400):
            d = root / f"case{i % 20}" / f"sub{i % 5}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"doc{i}.pdf").write_bytes(b"x" * (i % 89 + 1))
        (root / "empty.pdf").write_bytes(b"")
        (root / "notes.txt").write_bytes(b"x")
        (root / "dir.pdf").mkdir()
        found = batch.collect_files([root], {".pdf"})
        check("recursive scan finds every real PDF", len(found) == 400, str(len(found)))
        check("zero-byte and directory decoys excluded",
              not any(p.name in {"empty.pdf", "dir.pdf"} for p in found))
        check("queued smallest-first",
              found[0].stat().st_size <= found[-1].stat().st_size)
        check("workspace never re-ingested",
              batch.collect_files([config.BASE_DIR], {".pdf"}) == []
              or all(str(config.RESULT_DIR).lower() not in str(p).lower()
                     for p in batch.collect_files([config.BASE_DIR], {".pdf"})))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_vanished_file_does_not_break_sort() -> None:
    root = Path(tempfile.mkdtemp())
    f = root / "gone.pdf"
    f.write_bytes(b"x")
    f.unlink()
    try:
        batch._ordered({"k": f})
        check("scan survives a file deleted mid-run", True)
    except Exception as exc:
        check("scan survives a file deleted mid-run", False, type(exc).__name__)
    shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------- assembly
def test_rolling_writer() -> None:
    with session_temp() as t:
        ctx = ctx_in(t)
        stage = t / "s"; stage.mkdir()
        w = _RollingWriter(stage, 3 * 1024)
        for i in range(1, 61):
            w.note(i, "table (~9 rows)" if i % 10 == 0 else "")
            w.write_page(i, f"## Clause {i}\n\n" + "body text " * 40)
        out = w.finalize(ctx, Path("Award 2019.pdf"), 60)
        text = "".join(p.read_text(encoding="utf-8")
                       for p in (sorted(out.glob("*.md")) if out.is_dir() else [out]))
        import re
        pages = [int(x) for x in re.findall(r"<!-- page (\d+) -->", text)]
        check("split loses no pages", pages == list(range(1, 61)), f"{len(pages)} pages")
        check("part headers state their page range", "pages 1" in text)
        check("manifest emitted", "Contents of this part" in text)
        shutil.rmtree(out) if out.is_dir() else out.unlink()

    with session_temp() as t:                     # oversized single page
        ctx = ctx_in(t)
        stage = t / "s2"; stage.mkdir()
        w = _RollingWriter(stage, 1024)
        w.write_page(1, "y" * 500_000)
        w.write_page(2, "z")
        out = w.finalize(ctx, Path("big.pdf"), 2)
        text = "".join(p.read_text(encoding="utf-8")
                       for p in (sorted(out.glob("*.md")) if out.is_dir() else [out]))
        check("page larger than the split budget is not dropped",
              "page 1" in text and "page 2" in text)
        shutil.rmtree(out) if out.is_dir() else out.unlink()


def test_cancellation() -> None:
    with session_temp() as t:
        flag = {"c": False}
        ctx = ctx_in(t, lambda: flag["c"])
        stage = t / "s"; stage.mkdir()
        w = _RollingWriter(stage, 4096)
        stopped = 0
        try:
            for i in range(1, 2000):
                ctx.check_cancel()
                w.write_page(i, "body " * 20)
                stopped = i
                if i == 40:
                    flag["c"] = True
        except Cancelled:
            pass
        check("cancel stops within one page", stopped <= 41, f"page {stopped}")


# ---------------------------------------------------------------- extraction
def test_legacy_fonts() -> None:
    legacy = ["Kruti Dev 010", "KrutiDev010", "Shree-Tel-0800", "Godavari",
              "DVB-TTYogeshEN", "APS-DV-Priyanka", "ML-TTKarthika"]
    modern = ["ArialMT-Caps", "Manuscript", "Mangal", "NotoSansDevanagari",
              "Nirmala UI", "TimesNewRomanPSMT", "SmallCaps-Regular", "Gautami",
              "MyriadPro-Caption", "Calibri"]
    check("legacy Indic fonts detected", all(_legacy_match(f) for f in legacy))
    check("modern fonts not misflagged (no false OCR)",
          not any(_legacy_match(f) for f in modern))


def test_languages() -> None:
    check("uninstalled languages dropped",
          ocr.normalise_langs("eng+hin+tam", ["eng", "tel"]) == "eng")
    check("language string never empties", ocr.normalise_langs("", []) == "eng")
    check("duplicates collapsed",
          ocr.normalise_langs("eng+eng+hin", ["eng", "hin"]) == "eng+hin")
    check("narrowing keeps only scripts present",
          ocr.narrow_langs("eng+hin+tel", {"Latin"}) == "eng")
    check("narrowing never invents a language",
          ocr.narrow_langs("eng+hin", {"Tamil"}) == "eng+hin")
    check("failed detection falls back to full selection",
          ocr.narrow_langs("eng+hin+tel", set()) == "eng+hin+tel")


def test_mermaid_refuses_ambiguity() -> None:
    boxes = [((100, 100, 200, 140), "Notice"), ((100, 200, 200, 240), "Claim"),
             ((100, 300, 200, 340), "Defence"), ((100, 400, 200, 440), "Award")]
    segs = [((150, 140), (150, 200)), ((150, 240), (150, 300)),
            ((150, 340), (150, 400))]
    check("real flowchart reconstructed", build_mermaid(boxes, segs) is not None)
    for label, nodes, segments in [
            ("unlabelled boxes", [(b, "") for b, _ in boxes], segs),
            ("too few boxes", boxes[:2], segs[:1]),
            ("no connectors", boxes, []),
            ("orphan node", boxes, segs[:1]),
            ("stray lines", boxes, [((900, 900), (950, 950)), ((800, 800), (850, 850))])]:
        check(f"refuses to invent structure: {label}",
              build_mermaid(nodes, segments) is None)
    check("caption matched to artwork",
          pick_caption([((100, 450, 300, 462), "Fig 4.2 - Cross Section")],
                       (100, 100, 300, 440)) != "")


def test_figure_clusters_stay_separate() -> None:
    """Regression: analyse() used to feed build_mermaid the WHOLE page's
    geometry at once, so an unrelated shape elsewhere on the page (a
    signature block, a second unrelated drawing) could get treated as part
    of the same diagram — inventing a connection the page never drew."""
    touching = [(0, 0, 10, 10), (8, 8, 20, 20)]
    far_away = (500, 500, 520, 520)
    ids = cluster_ids(touching + [far_away], tol=2.0)
    check("touching boxes share a cluster", ids[0] == ids[1])
    check("a far-away box is its own cluster", ids[0] != ids[2])
    bridged = cluster_ids([(0, 0, 10, 10), (30, 0, 40, 10), (10, 4, 30, 6)], tol=1.0)
    check("a connecting line bridges two boxes into one cluster",
          bridged[0] == bridged[1] == bridged[2])


def test_mixed_image_types_allowed_for_images_to_pdf() -> None:
    """Regression: every extension was its own bucket, so selecting a .jpg
    and a .png together (the normal way to use 'Images -> PDF') silently
    dropped every action — operations_for() returned an empty list."""
    mixed_images = [Path("photo.jpg"), Path("scan.png"), Path("cover.tiff")]
    ops = registry.operations_for(mixed_images)
    check("mixed image extensions still offer Images -> PDF",
          any(o.id == "img_to_pdf" for o in ops))
    mixed_docs = [Path("claim.pdf"), Path("reply.docx")]
    check("genuinely different document types are still rejected",
          registry.operations_for(mixed_docs) == [])


def test_transparent_pixels_flattened_to_white() -> None:
    """Regression: a plain .convert("RGB") on RGBA/palette-with-transparency
    does not composite against a background, so a transparent PNG's
    background turned solid black in the assembled PDF instead of staying
    white."""
    from PIL import Image
    from app.ops.convert_image import _flatten
    im = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    flat = _flatten(im)
    check("transparent RGBA pixel becomes white, not black",
          flat is not None and flat.getpixel((5, 5)) == (255, 255, 255))
    opaque = Image.new("RGB", (10, 10), (10, 20, 30))
    check("already-opaque RGB is left alone", _flatten(opaque) is None)


def test_bitonal_images_recompressed_as_grayscale() -> None:
    """Regression: bitonal ("1") scans -- the common case for old exhibits --
    were promoted straight to RGB, tripling the encoded size for content
    that carries no colour at all."""
    from app.ops.compress import _target_mode
    check("bitonal source becomes single-channel grayscale", _target_mode("1") == "L")
    check("already-gray source stays gray", _target_mode("L") == "L")
    check("colour source stays RGB", _target_mode("RGB") == "RGB")
    check("palette/CMYK/alpha sources normalise to RGB", _target_mode("P") == "RGB")


def test_figure_text_not_duplicated_in_body() -> None:
    """Regression: a caption or in-drawing label appeared twice — garbled in
    the ordinary paragraph flow (short, out-of-reading-order text) and again,
    properly, under the figure's own description block."""
    from app.pdfengine import Span
    from app.ops._markdown_build import page_markdown

    class FakePage:
        def tables(self):
            return []

        def spans(self):
            return [
                Span((10, 10, 200, 30), "Real paragraph text that stays.", 11.0, False, "Arial"),
                Span((10, 100, 120, 112), "Inside The Figure", 8.0, False, "Arial"),
            ]

    excluded = page_markdown(FakePage(), exclude_boxes=[(0, 90, 300, 200)])
    check("real paragraph text is kept", "Real paragraph text" in excluded)
    check("figure-region text is excluded from the paragraph flow",
          "Inside The Figure" not in excluded)
    unexcluded = page_markdown(FakePage())
    check("without an exclude box, behaviour is unchanged",
          "Inside The Figure" in unexcluded)


# ---------------------------------------------------------------- pool
def test_pool() -> None:
    root = Path(tempfile.mkdtemp())
    try:
        files = []
        for i in range(24):
            f = root / f"d{i}.pdf"
            f.write_bytes(b"%PDF-1.4")
            files.append(f)
        msgs = []
        outs, fails = batch.run_parallel("no_such_op", files, {}, 3,
                                         lambda p, m: msgs.append(p), lambda: False)
        check("every file accounted for", len(outs) + len(fails) == len(files))
        check("failures carry a clean message",
              all("unknown operation" in f for f in fails))
        prog = [p for p in msgs if p >= 0]
        check("progress monotonic and completes",
              prog == sorted(prog) and abs(prog[-1] - 100) < 0.01)

        done = {}
        def go():
            t0 = time.time()
            batch.run_parallel("no_such_op", files * 2, {}, 3,
                               lambda p, m: None, lambda: True)
            done["t"] = time.time() - t0
        th = threading.Thread(target=go, daemon=True)
        th.start(); th.join(60)
        check("cancellation returns promptly", "t" in done,
              f"{done.get('t', 0):.1f}s" if done else "hung")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_settings_persistence() -> None:
    from app import settings as prefs
    backup = prefs.SETTINGS_FILE.read_text(encoding="utf-8") \
        if prefs.SETTINGS_FILE.exists() else None
    try:
        data = prefs.load()
        data["target_mb"] = 750
        data["ocr_lang"] = "eng+tel"
        prefs.save(data)
        check("settings survive a round trip",
              prefs.load()["target_mb"] == 750 and prefs.load()["ocr_lang"] == "eng+tel")

        bad = prefs.load()
        bad["target_mb"] = "not a number"
        bad["image_dpi"] = 99999
        prefs.save(bad)
        reloaded = prefs.load()
        check("bad values coerced to defaults",
              reloaded["target_mb"] == prefs.DEFAULTS["target_mb"])
        check("out-of-range values clamped", reloaded["image_dpi"] <= 600)

        prefs.SETTINGS_FILE.write_text("{ this is not json", encoding="utf-8")
        check("corrupt settings file does not crash",
              prefs.load()["target_mb"] == prefs.DEFAULTS["target_mb"])
    finally:
        if backup is not None:
            prefs.SETTINGS_FILE.write_text(backup, encoding="utf-8")
        elif prefs.SETTINGS_FILE.exists():
            prefs.SETTINGS_FILE.unlink()


def test_eta() -> None:
    from app.progress import Estimator, human_duration
    check("durations read naturally",
          human_duration(30) == "under a minute" and "h" in human_duration(5400))
    check("negative duration handled", human_duration(-5) == "")
    est = Estimator(1000, 10)
    check("no estimate before evidence", est.remaining_seconds() is None)
    for _ in range(4):
        time.sleep(0.02)
        est.advance(100)
    check("fraction tracks bytes not file count", abs(est.fraction - 0.4) < 0.01)
    check("estimate produced once moving", est.remaining_seconds() is not None)
    est.advance(10 ** 9)
    check("progress cannot exceed 100%", est.fraction <= 1.0)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        try:
            fn()
        except Exception as exc:
            check(f"{fn.__name__} raised", False, f"{type(exc).__name__}: {exc}")

    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, ok, detail in RESULTS:
        if not ok:
            failed += 1
        flag = "PASS" if ok else "FAIL"
        print(f"  {flag}  {name:<{width}}  {detail}")
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
