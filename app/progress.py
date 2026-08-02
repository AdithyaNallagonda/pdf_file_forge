"""Throughput tracking and time-remaining estimates.

Estimating from file *count* is misleading when a folder holds a 2 MB letter
next to a 700 MB scanned volume. Bytes are the better proxy for work, and a
rolling average smooths out the fast small files at the front of the queue.
"""
from __future__ import annotations

import time


def human_duration(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:            # negative or NaN
        return ""
    seconds = int(seconds)
    if seconds < 45:
        return "under a minute"
    if seconds < 3600:
        return f"about {max(1, round(seconds / 60))} min"
    hours, rem = divmod(seconds, 3600)
    mins = round(rem / 60)
    if mins == 60:
        hours, mins = hours + 1, 0
    return f"about {hours} h" + (f" {mins} min" if mins else "")


class Estimator:
    """Feed it completed byte counts; ask it how much longer."""

    def __init__(self, total_bytes: int, total_items: int = 0):
        self.total = max(1, int(total_bytes))
        self.total_items = total_items
        self.done_bytes = 0
        self.done_items = 0
        self.started = time.time()
        self._samples: list[tuple[float, int]] = []   # (elapsed, cumulative bytes)

    def advance(self, nbytes: int) -> None:
        self.done_bytes = min(self.total, self.done_bytes + max(0, int(nbytes)))
        self.done_items += 1
        self._samples.append((time.time() - self.started, self.done_bytes))
        if len(self._samples) > 40:
            del self._samples[0]

    @property
    def fraction(self) -> float:
        return self.done_bytes / self.total

    def remaining_seconds(self) -> float | None:
        """None until there is enough signal to say anything honest."""
        if self.done_bytes <= 0 or len(self._samples) < 2:
            return None
        # rate over the recent window, not the whole run
        (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
        span, moved = t1 - t0, b1 - b0
        if span <= 0 or moved <= 0:
            return None
        rate = moved / span
        left = self.total - self.done_bytes
        return left / rate if rate > 0 else None

    def remaining_text(self) -> str:
        secs = self.remaining_seconds()
        if secs is None:
            return "estimating…"
        text = human_duration(secs)
        return f"{text} remaining" if text else ""

    def summary(self) -> str:
        elapsed = human_duration(time.time() - self.started)
        return f"finished in {elapsed}" if elapsed else "finished"
