"""Session workspace: scoped temp dirs, progress reporting, cancellation."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from . import config

log = logging.getLogger(config.APP_NAME)


class Cancelled(Exception):
    """Raised inside a worker when the user aborts the operation."""


class OpContext:
    """Everything an operation needs: scratch space, progress, cancel, options."""

    def __init__(self, temp_dir: Path, on_progress: Callable[[float, str], None],
                 is_cancelled: Callable[[], bool], options: dict | None = None):
        self.temp_dir = temp_dir
        self.result_dir = config.RESULT_DIR
        self._on_progress = on_progress
        self._is_cancelled = is_cancelled
        self.options = options or {}

    # -- progress / control --
    def progress(self, pct: float, msg: str = "") -> None:
        self.check_cancel()
        self._on_progress(max(0.0, min(100.0, pct)), msg)

    def log(self, msg: str) -> None:
        self._on_progress(-1, msg)

    def check_cancel_flag(self) -> bool:
        """Non-raising form, for handing to worker pools."""
        return self._is_cancelled()

    def check_cancel(self) -> None:
        if self._is_cancelled():
            raise Cancelled()

    def scoped(self, lo: float, hi: float, prefix: str = "") -> "OpContext":
        """Child context whose 0–100% maps onto the lo–hi slice of the parent bar."""
        def relay(pct: float, msg: str) -> None:
            label = f"{prefix}{msg}" if prefix and msg else msg
            self._on_progress(pct if pct < 0 else lo + (hi - lo) * pct / 100.0, label)
        return OpContext(self.temp_dir, relay, self._is_cancelled, self.options)

    # -- helpers --
    def tmp(self, name: str) -> Path:
        p = self.temp_dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def tmpdir(self, name: str) -> Path:
        p = self.temp_dir / name
        p.mkdir(parents=True, exist_ok=True)
        return p

    def out(self, stem: str, suffix: str, source: Path | None = None) -> Path:
        """Reserve a unique output path ATOMICALLY.

        The old check-then-create version raced: with several worker processes
        converting files that share a basename, each saw the name as free and
        they overwrote one another. Reservation now uses O_CREAT|O_EXCL (or
        mkdir, which is equally atomic) so exactly one caller can win a name.

        `source` supplies provenance: two files called claim.pdf in different
        case folders become claim.md and CaseB_claim.md, not claim.md and
        claim_1.md, so the output still says where it came from.
        """
        self.result_dir.mkdir(parents=True, exist_ok=True)
        stem = _safe_name(stem) or "output"

        names = [stem]
        if source is not None:
            parent = _safe_name(source.parent.name)
            if parent and parent.lower() not in stem.lower():
                names.append(f"{parent}_{stem}")
        for i in range(1, 10000):
            names.append(f"{stem}_{i}")

        for name in names:
            cand = self.result_dir / f"{name}{suffix}"
            try:
                if suffix:
                    fd = os.open(cand, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
                    os.close(fd)
                else:
                    cand.mkdir(parents=False)
                return cand
            except FileExistsError:
                continue
            except OSError as exc:
                raise RuntimeError(f"Cannot write to {self.result_dir}: {exc}") from exc
        raise RuntimeError(f"Could not find a free output name for '{stem}'.")

    @staticmethod
    def finish(temp_path: Path, final_path: Path) -> Path:
        """Move a finished artefact onto its reserved path.

        os.replace, not shutil.move: `out()` has already created a placeholder,
        and shutil.move would fall back to a full byte copy rather than
        overwrite it — ruinous for a multi-GB PDF. Same volume by construction.
        """
        try:
            os.replace(temp_path, final_path)
        except OSError:
            shutil.copy2(temp_path, final_path)
            try:
                Path(temp_path).unlink()
            except OSError:
                pass
        return final_path

    def run(self, cmd: list[str], timeout: int | None = None) -> subprocess.CompletedProcess:
        """Run an external tool, hidden console, cancel-aware."""
        self.check_cancel()
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        log.debug("exec: %s", " ".join(cmd))
        # Output goes to a file, not a pipe. With PIPE we only read after exit,
        # so any child that writes more than the ~64 KB buffer blocks forever
        # and we sit here until the timeout. LibreOffice and Tesseract both do.
        sink = self.temp_dir / f"proc_{uuid.uuid4().hex[:8]}.log"
        try:
            with open(sink, "w", encoding="utf-8", errors="replace") as fh:
                proc = subprocess.Popen(cmd, stdout=fh, stderr=subprocess.STDOUT,
                                        creationflags=flags, text=True)
                started = time.time()
                while proc.poll() is None:
                    if self._is_cancelled():
                        proc.kill()
                        proc.wait(timeout=10)
                        raise Cancelled()
                    if timeout and time.time() - started > timeout:
                        proc.kill()
                        proc.wait(timeout=10)
                        raise TimeoutError(f"{Path(cmd[0]).name} timed out "
                                           f"after {timeout}s")
                    time.sleep(0.2)
            try:
                output = sink.read_text(encoding="utf-8", errors="replace")[-8000:]
            except OSError:
                output = ""
            return subprocess.CompletedProcess(cmd, proc.returncode, output, "")
        finally:
            try:
                sink.unlink()
            except OSError:
                pass


def _safe_name(name: str) -> str:
    """Strip anything Windows refuses in a filename, and trim length."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if cleaned.upper().split(".")[0] in {
            "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
            "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
            "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}:
        cleaned = f"_{cleaned}"
    return cleaned[:120]


@contextmanager
def session_temp() -> Iterator[Path]:
    """Unique scratch dir; removed unconditionally on exit."""
    d = config.TEMP_ROOT / f"job_{uuid.uuid4().hex[:10]}"
    d.mkdir(parents=True, exist_ok=True)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


STALE_AFTER_SECONDS = 6 * 3600


def purge_temp_root(max_age: int = STALE_AFTER_SECONDS) -> None:
    """Remove scratch dirs left by a crash — but ONLY genuinely stale ones.

    The previous version deleted everything under temp/ at launch, so opening a
    second window destroyed the first window's in-flight job. Age-gated now.
    """
    if not config.TEMP_ROOT.exists():
        return
    cutoff = time.time() - max_age
    for child in config.TEMP_ROOT.iterdir():
        try:
            if child.stat().st_mtime > cutoff:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def human(size: int) -> str:
    f = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:,.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"
