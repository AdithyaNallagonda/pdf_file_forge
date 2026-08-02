"""Persisted user preferences.

Stored as JSON beside the workspace. Every value is validated on load: a
hand-edited or corrupt file must never crash the app or smuggle a bad type into
an operation, so anything unrecognised falls back to the default.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(config.APP_NAME)
SETTINGS_FILE = config.BASE_DIR / "settings.json"

DEFAULTS: dict[str, Any] = {
    "target_mb": config.DEFAULT_TARGET_MB,
    "split_mb": config.DEFAULT_SPLIT_MB,
    "image_dpi": 200,
    "ocr_dpi": 300,
    "ocr_lang": "eng+hin+tel",
    "force_ocr": False,
    "auto_langs": True,
    "extract_tables": True,
    "describe_figures": True,
    "mermaid": True,
    "export_figures": False,
    "recursive": True,
    "workers": 0,            # 0 => decide from the CPU count at runtime
    "page_breaks": True,
    "last_folder": "",
}

# (type, minimum, maximum) for the numeric settings
_NUMERIC = {
    "target_mb": (int, 1, 20000),
    "split_mb": (float, 0.0, 100.0),
    "image_dpi": (int, 50, 600),
    "ocr_dpi": (int, 100, 600),
    "workers": (int, 0, 32),
}


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS[key]
    try:
        if key in _NUMERIC:
            kind, lo, hi = _NUMERIC[key]
            return max(lo, min(hi, kind(value)))
        if isinstance(default, bool):
            return bool(value)
        if isinstance(default, str):
            return str(value)[:400]
    except (TypeError, ValueError):
        pass
    return default


def load() -> dict[str, Any]:
    data = dict(DEFAULTS)
    try:
        if SETTINGS_FILE.exists():
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in DEFAULTS:
                    if key in raw:
                        data[key] = _coerce(key, raw[key])
    except Exception as exc:                       # corrupt file, bad encoding…
        log.warning("settings unreadable (%s); using defaults", exc)
    return data


def save(values: dict[str, Any]) -> bool:
    """Atomic write — a crash mid-save must not leave an unreadable file."""
    clean = {k: _coerce(k, values.get(k, DEFAULTS[k])) for k in DEFAULTS}
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        tmp.replace(SETTINGS_FILE)
        return True
    except Exception as exc:
        log.warning("could not save settings: %s", exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
