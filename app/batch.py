"""Folder discovery and parallel execution across files.

Parallelism is deliberately *across* files, never within one. Each worker owns a
whole document, so there is no shared state, no lock contention, and one poisoned
file cannot corrupt another's output. Speedup is roughly linear in worker count
until disk or RAM saturates.

Per-file progress is not reported in parallel mode — with eight documents in
flight a single bar cannot mean anything useful, so the parent reports completions
("47 of 180 done"), which is the number that matters on a long run.
"""
from __future__ import annotations

import multiprocessing as mp
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

from . import config

# Files the app itself produced, plus noise that should never be swept up.
SKIP_DIRS = {"result_files", "temp", "logs", "$recycle.bin",
             "system volume information", "node_modules", ".git"}


def collect_files(roots: Iterable[Path], extensions: set[str],
                  recursive: bool = True, limit: int = 20000) -> list[Path]:
    """Every matching file under the given folders, de-duplicated and ordered.

    Skips the app's own workspace so a previous run's output is never fed back in.
    """
    exts = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensions}
    seen: dict[str, Path] = {}
    workspace = str(config.BASE_DIR).lower()

    for root in roots:
        root = Path(root)
        if not root.is_dir():
            if root.suffix.lower() in exts:
                seen.setdefault(str(root.resolve()).lower(), root)
            continue
        walker = os.walk(root) if recursive else [(str(root), [], os.listdir(root))]
        for dirpath, dirnames, filenames in walker:
            low = dirpath.lower()
            if low.startswith(workspace):
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames
                           if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if Path(name).suffix.lower() not in exts:
                    continue
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                    if st.st_size == 0 or not p.is_file():
                        continue
                except OSError:
                    continue
                seen.setdefault(str(p.resolve()).lower(), p)
                if len(seen) >= limit:
                    return _ordered(seen)
    return _ordered(seen)


def _ordered(seen: dict[str, Path]) -> list[Path]:
    # Smallest first: the user sees results early, and a pathological 5 GB file
    # does not hold a worker hostage while the pool sits idle at the end.
    # stat() is re-read here and the file may have gone since the walk, so a
    # missing file sorts last rather than killing the whole scan.
    def size_of(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 1 << 62
    return sorted(seen.values(), key=lambda p: (size_of(p), str(p).lower()))


def default_workers() -> int:
    cpu = os.cpu_count() or 4
    return max(1, min(4, cpu - 1))


# --------------------------------------------------------------------------
# worker (module level so it survives pickling under the 'spawn' start method)
# --------------------------------------------------------------------------
def _run_one(payload: tuple[str, str, dict]) -> tuple[str, str, str]:
    op_id, path_str, options = payload
    from .registry import OPERATIONS
    from .workspace import OpContext, session_temp

    options = dict(options)
    options["ocr_workers"] = 1          # this process IS the unit of parallelism
    op = next((o for o in OPERATIONS if o.id == op_id), None)
    if op is None:
        return path_str, "", f"unknown operation '{op_id}'"
    try:
        with session_temp() as tmp:
            ctx = OpContext(tmp, lambda pct, msg: None, lambda: False, options)
            out = op.handler([Path(path_str)], ctx)
        return path_str, str(out), ""
    except Exception as exc:                      # one bad file must not stop 200
        return path_str, "", f"{type(exc).__name__}: {exc}"


def run_parallel(op_id: str, files: list[Path], options: dict, workers: int,
                 on_progress: Callable[[float, str], None],
                 is_cancelled: Callable[[], bool],
                 on_file: Callable[[Path, str, str], None] | None = None
                 ) -> tuple[list[str], list[str]]:
    """Returns (outputs, failures).

    on_file(source, output, error) fires as each document finishes, so a caller
    can update a per-file status table and a byte-based ETA. Cancellation drops
    queued work immediately; documents already converting run to completion.
    """
    outputs: list[str] = []
    failures: list[str] = []
    total = len(files)
    done = 0

    ctx_mp = mp.get_context("spawn")              # Windows has no fork
    # NOTE: leaving the pool waits for in-flight files. Cancelling drops the
    # queue immediately but a document already being converted runs to
    # completion — killing mid-write would leave a truncated output.
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx_mp) as pool:
        futures = {pool.submit(_run_one, (op_id, str(f), options)): f for f in files}
        try:
            for fut in as_completed(futures):
                if is_cancelled():
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    src, out, err = fut.result()
                except Exception as exc:
                    # BrokenProcessPool, OOM-killed child, unpicklable failure...
                    src = str(futures.get(fut, "?"))
                    out, err = "", f"worker died: {type(exc).__name__}: {exc}"
                done += 1
                name = Path(src).name
                if on_file:
                    try:
                        on_file(Path(src), out, err)
                    except Exception:
                        pass
                if err:
                    failures.append(f"{name}: {err}")
                    on_progress(-1, f"    FAILED {name} — {err}")
                else:
                    outputs.append(out)
                on_progress(100 * done / total,
                            f"{done} of {total} done ({len(failures)} failed)")
        finally:
            if is_cancelled():
                pool.shutdown(wait=False, cancel_futures=True)
    return outputs, failures
