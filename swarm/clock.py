"""Injectable clocks and the shared elapsed-time format.

Durations are ALWAYS differences of `monotonic()`; `wall()` is for display timestamps only, so a wall-clock
change (NTP step, manual change) never corrupts an elapsed value. `format_elapsed` has a TypeScript twin in
`omp/src/swarm_bar.ts`; both are pinned by `tests/fixtures/elapsed_cases.json`.
"""
from __future__ import annotations

import math
import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Source of time: `monotonic()` never decreases, `wall()` is epoch seconds and may jump."""

    def monotonic(self) -> float: ...

    def wall(self) -> float: ...


class SystemClock:
    """The real clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


SYSTEM_CLOCK: Clock = SystemClock()


class FakeClock:
    """Deterministic clock for tests: `advance` moves both readings, `jump_wall` moves only the wall reading."""

    def __init__(self, start_mono: float = 1000.0, start_wall: float = 1_760_000_000.0) -> None:
        self._mono = float(start_mono)
        self._wall = float(start_wall)

    def advance(self, seconds: float) -> None:
        """Move both clocks forward; a negative step would break the monotonic guarantee and is rejected."""
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"advance: seconds must be a finite number >= 0, got {seconds!r}")
        self._mono += seconds
        self._wall += seconds

    def jump_wall(self, delta: float) -> None:
        """Simulate a wall-clock change (either direction); `monotonic()` is unaffected."""
        if not isinstance(delta, (int, float)) or isinstance(delta, bool) or not math.isfinite(delta):
            raise ValueError(f"jump_wall: delta must be a finite number, got {delta!r}")
        self._wall += delta

    def monotonic(self) -> float:
        return self._mono

    def wall(self) -> float:
        return self._wall


def format_elapsed(seconds: float) -> str:
    """`59s`, `4m12s`, `1h05m`. Fractions are floored; negative, NaN, inf or non-numeric input renders `0s`."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
        return "0s"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        return f"{total // 60}m{total % 60:02d}s"
    return f"{total // 3600}h{(total % 3600) // 60:02d}m"
