"""File-claim overlap and ClaimTable semantics (swarm/claims.py)."""
import threading

import pytest

from swarm.claims import Claim, ClaimTable, normalize_pattern, overlaps, parse_claims

OVERLAP_CASES = [
    ("a/**", "a/b.py", True),
    ("a/**", "a/b/c/d.py", True),
    ("a/*.py", "a/b/c.py", False),
    ("a/*.py", "a/b.py", True),
    ("a/*.py", "a/b*", True),
    ("a/*.py", "a/*.md", False),
    ("a/b.py", "a/b.py", True),
    ("a/b.py", "a/c.py", False),
    ("a/b/c.py", "a/b/d.py", False),
    ("**", "x/y/z.py", True),
    ("**", "**", True),
    ("**/*.py", "a/b/c.py", True),
    ("**/*.py", "a/b/c.md", False),
    ("a/**/c.py", "a/b/c.py", True),
    ("a/**/c.py", "a/b/d.py", False),
    ("a/**", "b/**", False),
    ("src/", "src/x/y.py", True),
    ("src/", "tests/x.py", False),
    ("a/[bc].py", "a/b.py", True),
    ("a/?.py", "a/bc.py", False),
    ("./a//b.py", "a/b.py", True),
    ("a\\b.py", "a/b.py", True),
    ("a/**", "a", True),
]


@pytest.mark.parametrize("a,b,expected", OVERLAP_CASES)
def test_overlap_table(a, b, expected):
    assert overlaps(a, b) is expected
    assert overlaps(b, a) is expected


@pytest.mark.parametrize("bad", ["", "   ", "/etc/passwd", "../x", "a/../b", "C:/x", "~/x", "./", "//"])
def test_invalid_patterns_rejected(bad):
    with pytest.raises(ValueError):
        normalize_pattern(bad)
    with pytest.raises(ValueError):
        overlaps(bad, "a/b.py")
    with pytest.raises(ValueError):
        Claim(bad)


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        Claim("a/b.py", "weird")


def test_parse_claims_forms():
    notes = {"claims": ["a/**", {"pattern": "docs/", "mode": "shared"}, Claim("b.py", "shared")]}
    claims = parse_claims(notes)
    assert claims == [Claim("a/**"), Claim("docs/**", "shared"), Claim("b.py", "shared")]
    assert claims[0].mode == "exclusive"
    assert parse_claims({}) == [] and parse_claims(None) == [] and parse_claims({"claims": None}) == []
    assert parse_claims({"claims": "x.py"}) == [Claim("x.py")]


@pytest.mark.parametrize("notes", [{"claims": 5}, {"claims": [3]}, {"claims": ["/abs"]}, {"claims": [{"mode": "shared"}]}])
def test_parse_claims_malformed_raises(notes):
    with pytest.raises(ValueError):
        parse_claims(notes)


def test_exclusive_vs_shared_semantics():
    t = ClaimTable()
    assert t.acquire("r1", [Claim("a/**", "shared")]).ok
    assert t.acquire("r2", [Claim("a/b.py", "shared")]).ok  # shared + shared never conflicts
    res = t.acquire("r3", [Claim("a/b.py", "exclusive")])  # exclusive vs overlapping shared conflicts
    assert not res.ok
    assert {c.owner for c in res.conflicts} == {"r1", "r2"}
    assert t.acquire("r3", [Claim("z/**")]).ok  # disjoint is fine
    assert not t.acquire("r4", [Claim("z/q.py", "shared")]).ok  # shared vs overlapping exclusive conflicts


def test_acquire_is_all_or_nothing():
    t = ClaimTable()
    assert t.acquire("r1", [Claim("a/x.py")]).ok
    res = t.acquire("r2", [Claim("free/**"), Claim("a/*.py")])
    assert not res.ok and len(res.conflicts) == 1
    assert res.conflicts[0].requested == Claim("a/*.py") and res.conflicts[0].held == Claim("a/x.py")
    assert "r2" not in t.held()
    assert t.acquire("r3", [Claim("free/**")]).ok  # the claim r2 tried first was not leaked


def test_owner_does_not_conflict_with_itself_and_reacquire_is_idempotent():
    t = ClaimTable()
    assert t.acquire("r1", [Claim("a/**")]).ok
    assert t.acquire("r1", [Claim("a/b.py"), Claim("a/**")]).ok
    assert t.held()["r1"] == (Claim("a/**"), Claim("a/b.py"))


def test_release_idempotent_and_frees_retry():
    t = ClaimTable()
    assert t.acquire("r1", [Claim("a/**")]).ok
    want = [Claim("a/b.py")]
    assert not t.acquire("r2", want).ok
    assert t.release("r1") == 1
    assert t.release("r1") == 0
    assert t.release("never") == 0
    assert t.acquire("r2", want).ok


def test_held_is_a_snapshot():
    t = ClaimTable()
    t.acquire("r1", [Claim("a/**")])
    snap = t.held()
    t.release("r1")
    assert snap == {"r1": (Claim("a/**"),)}
    assert t.held() == {}


def test_empty_acquire_ok_and_holds_nothing():
    t = ClaimTable()
    assert t.acquire("r1", []).ok
    assert t.held() == {}


def test_concurrent_acquire_exactly_nonconflicting_subset_wins():
    t = ClaimTable()
    # 16 owners: 4 groups of 4; owners in a group claim overlapping exclusive patterns, groups are disjoint,
    # so exactly one owner per group may win -> 4 winners.
    groups = ["g0/**", "g1/**", "g2/**", "g3/**"]
    barrier = threading.Barrier(16)
    results: dict[str, bool] = {}
    lock = threading.Lock()

    def worker(n: int) -> None:
        owner = f"r{n}"
        pattern = groups[n % 4] if n < 8 else f"g{n % 4}/**/*.py"  # overlaps every other claim in its group
        barrier.wait()
        ok = t.acquire(owner, [Claim(pattern)]).ok
        with lock:
            results[owner] = ok

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    winners = [o for o, ok in results.items() if ok]
    assert len(winners) == 4
    by_group = {int(o[1:]) % 4 for o in winners}
    assert by_group == {0, 1, 2, 3}
    assert set(t.held()) == set(winners)


def test_concurrent_shared_all_win():
    t = ClaimTable()
    barrier = threading.Barrier(16)
    oks: list[bool] = []

    def worker(n: int) -> None:
        barrier.wait()
        oks.append(t.acquire(f"r{n}", [Claim("docs/**", "shared")]).ok)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(16)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert all(oks) and len(oks) == 16


# --- W1 review R1 fix-up (P3-8): an exclusive bare name reserves the name AND everything under it --------------------
@pytest.mark.parametrize(("held", "held_mode", "want", "want_mode", "conflict"), [
    ("swarm/tools", "exclusive", "swarm/tools/a05_tools.py", "exclusive", True),   # the reviewer's scenario
    ("swarm/tools/a05_tools.py", "exclusive", "swarm/tools", "exclusive", True),   # symmetric
    ("swarm/tools", "exclusive", "swarm/tools/deep/x.py", "shared", True),         # exclusive vs shared child
    ("swarm/tools/x.py", "shared", "swarm/tools", "exclusive", True),
    ("swarm/tools", "shared", "swarm/tools/x.py", "shared", False),                # shared never conflicts
    ("swarm/tools", "shared", "swarm/tools/x.py", "exclusive", False),             # a SHARED bare name is just that path
    ("swarm/tools", "exclusive", "swarm/tools2/x.py", "exclusive", False),         # a sibling with the same prefix
    ("swarm/tools", "exclusive", "swarm/other/x.py", "exclusive", False),
    ("swarm/*.py", "exclusive", "swarm/x.py/inner", "exclusive", False),           # glob-ended patterns are unchanged
    ("a.py", "exclusive", "a.py", "exclusive", True),
])
def test_bare_name_exclusive_claim_covers_the_directory(held, held_mode, want, want_mode, conflict):
    table = ClaimTable()
    assert table.acquire("run-a", [Claim(held, held_mode)]).ok
    assert table.acquire("run-b", [Claim(want, want_mode)]).ok is (not conflict)
