"""FileForge entry point.

Prefers the Flet UI; falls back to the Tkinter one if Flet is unavailable, so a
missing optional dependency degrades the look rather than killing the app.
"""
from __future__ import annotations

import logging
import multiprocessing
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import config  # noqa: E402

logging.basicConfig(
    filename=config.LOG_DIR / "fileforge.log", filemode="a", level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(config.APP_NAME)


def run() -> None:
    force_tk = "--classic-ui" in sys.argv
    if not force_tk:
        try:
            from app.flet_ui import main as flet_main
            flet_main([a for a in sys.argv[1:] if a != "--classic-ui"])
            return
        except ImportError as exc:
            log.warning("Flet unavailable (%s); using the classic UI.", exc)
        except Exception:
            log.exception("Flet UI failed to start; falling back to the classic UI.")
    from app.ui import main as tk_main
    tk_main([a for a in sys.argv[1:] if a != "--classic-ui"])


if __name__ == "__main__":
    multiprocessing.freeze_support()   # frozen apps otherwise respawn their own GUI
    run()
