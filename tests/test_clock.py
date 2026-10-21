"""Clock invariants: wall jumps never move monotonic time; elapsed text matches the shared parity fixture."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from swarm.clock import SYSTEM_CLOCK, Clock, FakeClock, format_elapsed

FIXTURE = Path(__file__).parent / "fixtures" / "elapsed_cases.json"


def _cases() -> list[tuple[float, str]]:
    # JSON has no NaN/Infinity literal, so the fixture spells them as strings (Number("NaN") parses them in Bun too).
    return [(float(sec), text) for sec, text in json.loads(FIXTURE.read_text(encoding="utf-8"))]


def test_fixture_covers_boundaries():
    secs = [s for s, _ in _cases()]
    assert len(secs) >= 14
    for needed in (0, 59.9, 60, 3599, 3600, 86399, -1):
        assert needed in secs
    assert any(math.isnan(s) for s in secs) and any(math.isinf(s) for s in secs)


@pytest.mark.parametrize(("seconds", "text"), _cases())
def test_format_elapsed_fixture(seconds, text):
    assert format_elapsed(seconds) == text


@pytest.mark.parametrize("bad", [None, "12", True, [1], float("-inf")])
def test_format_elapsed_non_numeric_is_zero(bad):
    assert format_elapsed(bad) == "0s"


def test_format_elapsed_is_monotone_in_unit_boundaries():
    assert format_elapsed(59.999) == "59s"
    assert format_elapsed(60.0) == "1m00s"
    assert format_elapsed(3599.999) == "59m59s"
    assert format_elapsed(3600.0) == "1h00m"


def test_fake_clock_defaults_and_advance_moves_both():
    c = FakeClock()
    assert (c.monotonic(), c.wall()) == (1000.0, 1_760_000_000.0)
    c.advance(2.5)
    assert (c.monotonic(), c.wall()) == (1002.5, 1_760_000_002.5)


def test_jump_wall_changes_wall_but_never_monotonic():
    c = FakeClock()
    m0, w0 = c.monotonic(), c.wall()
    c.jump_wall(-3600)
    assert c.monotonic() == m0 and c.wall() == w0 - 3600
    c.jump_wall(7200)
    assert c.monotonic() == m0 and c.wall() == w0 + 3600


def test_elapsed_from_monotonic_survives_wall_jump():
    c = FakeClock()
    started = c.monotonic()
    c.advance(125)
    c.jump_wall(-86400)  # NTP step back a day
    assert format_elapsed(c.monotonic() - started) == "2m05s"


def test_fake_clock_rejects_backwards_or_non_finite_steps():
    c = FakeClock()
    with pytest.raises(ValueError):
        c.advance(-1)
    with pytest.raises(ValueError):
        c.advance(float("nan"))
    with pytest.raises(ValueError):
        c.jump_wall(float("inf"))
    assert c.monotonic() == 1000.0


def test_clock_protocol_and_system_clock():
    assert isinstance(SYSTEM_CLOCK, Clock) and isinstance(FakeClock(), Clock)
    a = SYSTEM_CLOCK.monotonic()
    assert SYSTEM_CLOCK.monotonic() >= a
    assert SYSTEM_CLOCK.wall() > 1_700_000_000
