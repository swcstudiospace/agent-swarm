"""Redaction: every pattern has positives and negatives; identifiers and prose are untouched; runlog.emit applies it."""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import os
import random
import re
import string
from pathlib import Path

import pytest

from swarm import runlog
from swarm.redact import REDACTED, is_sensitive_key, redact, redact_spans, redact_text

# Built at runtime so no secret-shaped literal sits in the repo.
GHP = "ghp_" + "a1B2c3D4" * 5
PAT = "github_pat_" + "11ABCDEFG0" * 3
SK = "sk-" + "Zx9Qw8Er7Ty6Ui5O"
AKIA = "AKIA" + "ABCDEFGHIJ012345"
SLACK = "xoxb-" + "1234567890-abcdefghij"
JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0" + "." + "dBjftJeZ4CVPmB92K27uhbUJU1p1r"
PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\nabc/def+ghi=\n-----END RSA PRIVATE KEY-----"
DIGEST = "sha256:" + "ab12" * 16


@pytest.mark.parametrize(("secret", "label"), [
    (GHP, "ghp"), (PAT, "github_pat"), ("gho_" + "x" * 30, "gho"), ("ghs_" + "y" * 30, "ghs"), ("ghu_" + "z" * 30, "ghu"),
    (SK, "sk"), (AKIA, "akia"), (SLACK, "slack"), ("xoxp-" + "9" * 12 + "-" + "a" * 8, "slack-p"), (JWT, "jwt"),
])
def test_token_positives(secret, label):
    out = redact_text(f"call failed with {secret} again")
    assert out == f"call failed with {REDACTED} again", label
    assert secret not in out


def test_bearer_keeps_scheme_and_hides_token():
    assert redact_text("Authorization: Bearer abcdefghij0123456789xyz") == f"Authorization: Bearer {REDACTED}"
    assert redact_text("bearer abcdefghij0123456789xyz") == f"bearer {REDACTED}"


def _join(value):
    """A vector string: one string or a list of fragments, with its `{{tok:...}}` secret placeholders expanded."""
    return expand_tokens(value if isinstance(value, str) else "".join(value))


_SHARED = json.loads((Path(__file__).parent / "fixtures" / "redact_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _SHARED, ids=[c["name"] for c in _SHARED])
def test_shared_vectors(case):
    """The same vectors run in omp/test/swarm_bar.test.ts against redactText: the two implementations cannot drift."""
    out = redact_text(_join(case["input"]))
    for needle in case["must_not_contain"]:
        assert _join(needle) not in out
    for needle in case["must_contain"]:
        assert _join(needle) in out
    if "expected" in case:
        assert out == case["expected"]
    assert redact_text(out) == out  # a masked output is a fixed point


def test_megabyte_inputs_are_scanned_in_linear_time():
    import time
    for unit in ("a://b:b:b:", "--password ", "mode=fast ", "'a':'", "Authorization: Basic ", "-----BEGIN PRIVATE KEY-----", "Bearer ", "a.b-",
                 "SWARM_ED25519_KEY=", "SWARM_ED25519_KEY_FILE=", "a,", "k=a,", "--ed25519-key ", "ed25519.seed.", "Cookie: ", "Authorization:x",
                 "--mode a;", "-v ", "password=a,token = ", "token=a,--password ", 'Cookie: a="', "x=-u=", '-u "', "A_KEY=", "STRIPE_KEY=A_KEY=",
                 "a://u:", 'password="', "-u=a:", 'Cookie: a="b" ', "-u a:", "--user=-u="):
        text = unit * (1_000_000 // len(unit))
        started = time.perf_counter()
        redact_text(text)
        assert time.perf_counter() - started < 3.0, unit  # a quadratic scan of 1 MB takes minutes


def test_megabyte_comma_separated_secret_value_is_redacted_whole_and_fast():
    import time
    started = time.perf_counter()
    out = redact_text("API_TOKEN=" + "q," * 500_000 + " done")
    assert time.perf_counter() - started < 3.0
    assert "qq" not in out and "q,q" not in out and out.endswith(f"{REDACTED} done")


def test_signer_variables_are_sensitive_dict_keys_and_key_files_are_not():
    from swarm.redact import is_sensitive_key, redact
    for name in ("SWARM_ED25519_KEY", "SWARM_SIGNING_KEY", "SWARM_ED25519_SEED", "swarmEd25519Key", "ed25519-key", "SUBSTRATE_HANDOFF_KEY",
                 "SUBSTRATE_OPERATOR_TOKENS", "bearer"):
        assert is_sensitive_key(name), name
    for name in ("SWARM_ED25519_KEY_FILE", "SWARM_ED25519_KEY_PATH", "KEYBOARD", "MONKEY", "keyframes", "sort_key", "random_seed", "seed",
                 "cache_key_prefix", "SWARM_ED25519_PUBLIC_KEY", "SWARM_BAR_KEY", "max_tokens"):
        assert not is_sensitive_key(name), name
    out = redact({"SWARM_ED25519_KEY": "0123", "SWARM_ED25519_KEY_FILE": "/home/u/key.hex", "SWARM_SIGNING_KEY": "abcd"})
    assert out == {"SWARM_ED25519_KEY": REDACTED, "SWARM_ED25519_KEY_FILE": "/home/u/key.hex", "SWARM_SIGNING_KEY": REDACTED}


def test_pem_block_whole_and_truncated():
    assert redact_text(f"key:\n{PEM}\ntail") == f"key:\n{REDACTED}\ntail"
    assert redact_text("x -----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBgkqhkiG9w0B") == f"x {REDACTED}"


def test_name_value_only_for_sensitive_names():
    assert redact_text("API_KEY=hunter2hunter2 and PATH=/usr/bin") == f"API_KEY={REDACTED} and PATH=/usr/bin"
    assert redact_text("run --password=s3cr3t --verbose") == f"run --password={REDACTED} --verbose"
    assert redact_text('db_password="two words"') == f"db_password={REDACTED}"
    assert redact_text("GITHUB_TOKEN='abc' x=1") == f"GITHUB_TOKEN={REDACTED} x=1"
    assert redact_text("https://x.test/cb?token=abc123&page=2") == f"https://x.test/cb?token={REDACTED}&page=2"
    assert redact_text("mode=fast count=3") == "mode=fast count=3"


def test_json_embedded_pairs():
    text = '{"api_key": "abc123", "name": "bob", "author": "ann", "tokens": 12}'
    out = redact_text(text)
    assert out == f'{{"api_key": "{REDACTED}", "name": "bob", "author": "ann", "tokens": 12}}'
    assert json.loads(out)["tokens"] == 12


def test_url_userinfo():
    assert redact_text("clone https://bob:pw123@example.com/repo.git now") == f"clone https://{REDACTED}@example.com/repo.git now"
    assert redact_text("https://example.com/a:b@c") == "https://example.com/a:b@c"  # '@' after the path is not userinfo
    assert redact_text("mailto:bob@example.com") == "mailto:bob@example.com"


@pytest.mark.parametrize("text", [
    "T7f3a-be", "T7f3a-be.a1", "run T7f3a-be.a2 finished", "corr-20261018-0001", DIGEST,
    f"artifact digest {DIGEST} ok", "task-sk-ab", "risk-assessment-for-the-quarterly-release-plan",
    "sk-short", "ghp_short", "AKIAlowercase0000000000", "the bearer of this message", "Bearer shortone",
    "eyJ only", "a.b.c", "Plain prose: we rotated the keys, tokens and passwords last week.",
    "Use the author field; authority is delegated", "ok", "", "path=/tmp/x key", "BEGIN PRIVATE KEY",
])
def test_negatives_are_untouched(text):
    assert redact_text(text) == text


def test_idempotent():
    once = redact_text(f"{GHP} API_KEY=zzzz1234 Bearer abcdefghij0123456789xyz {PEM}")
    assert redact_text(once) == once


@pytest.mark.parametrize("name", [
    "api_key", "API_KEY", "apiKey", "ANTHROPIC_API_KEY", "x-api-key", "access_key", "AWS_SECRET_ACCESS_KEY", "secret_key",
    "private_key", "privateKey", "signing_key", "SWARM_SIGNING_KEY", "ssh_key", "encryption_key", "master_key",
    "auth_key", "session_key", "license_key", "client_key", "apikey", "accessKey",
    "token", "Token", "access_token", "SUBSTRATE_TOKEN", "session_token", "caller_token", "GITHUB_TOKEN", "csrfToken",
    "client_secret", "secret", "Password", "db_password", "passwd", "credential", "credentials", "aws_credentials",
    "signing", "private", "Authorization", "authorization_header", "auth", "oauth", "auth_token", "Authentication",
    "Cookie", "set-cookie", "cookies",
])
def test_sensitive_keys(name):
    assert is_sensitive_key(name)


@pytest.mark.parametrize("name", [
    # identifiers and ordinary payload fields: a bare `key` is a lookup name, not a credential (runner gates use it)
    "key", "keys", "keyed", "monkey", "keyword", "keyboard", "Key", "primary_key", "foreign_key", "sort_key", "cache_key",
    "dedupe_key", "boundary_key", "idempotency_key", "lookup_key", "group-key", "partitionKey", "key_id", "hotkey",
    "task_id", "correlation_id", "run_id", "agent", "state", "digest", "payload", "msg", "ctx",
    # counts and labels that merely mention a credential word in the plural
    "tokens", "max_tokens", "input_tokens", "ux_tokens", "secrets", "passwords",
    # author / authority are not auth
    "author", "authors", "authority", "authored_by", "Author", "authorities",
    "scope", "autho", 7, None,
])
def test_non_sensitive_keys(name):
    assert not is_sensitive_key(name)


def test_runner_gate_style_key_field_survives_runlog_redaction():
    out = redact({"key": "S-be", "keys": ["S-be", "S-fe"], "sort_key": "z", "cache_key": "k1", "tokens": 1200,
                  "runs": {"secrets": "pass"}})
    assert out == {"key": "S-be", "keys": ["S-be", "S-fe"], "sort_key": "z", "cache_key": "k1", "tokens": 1200,
                   "runs": {"secrets": "pass"}}


def test_credential_compound_keys_redact_values():
    out = redact({"api_key": "x1", "apiKey": "x2", "headers": {"Authorization": "Basic abc", "x-api-key": "x3"},
                  "session_key": "x4", "client_key": "x5"})
    assert all(v == REDACTED for k, v in out.items() if k != "headers")
    assert out["headers"] == {"Authorization": REDACTED, "x-api-key": REDACTED}


def test_dict_sensitive_keys_redact_strings_but_keep_numbers():
    src = {"agent": "a05", "api_token": "abc", "tokens": 1200, "nested": {"password": ["p1", 7, {"x": "y"}], "ok": "fine"},
           "enabled_key": True, "secret": None, "cookie": ""}
    out = redact(src)
    assert out == {"agent": "a05", "api_token": REDACTED, "tokens": 1200,
                   "nested": {"password": [REDACTED, 7, {"x": REDACTED}], "ok": "fine"},
                   "enabled_key": True, "secret": None, "cookie": ""}


def test_redact_walks_lists_tuples_and_never_mutates():
    src = {"items": ["clean", f"x {GHP}", ("t", f"AWS {AKIA}")], "task_id": "T7f3a-be", "digest": DIGEST}
    snap = copy.deepcopy(src)
    out = redact(src)
    assert src == snap
    assert out == {"items": ["clean", f"x {REDACTED}", ["t", f"AWS {REDACTED}"]], "task_id": "T7f3a-be", "digest": DIGEST}
    assert out["items"] is not src["items"]


def test_scalars_pass_through():
    assert redact(None) is None and redact(5) == 5 and redact(1.5) == 1.5 and redact(True) is True
    assert redact(f"v {SK}") == f"v {REDACTED}"


def test_depth_cap():
    deep: dict = {"leaf": f"x {GHP}"}
    for _ in range(30):
        deep = {"d": deep}
    out = redact(deep)  # default cap 12
    node, levels = out, 0
    while isinstance(node, dict):
        node, levels = node["d"], levels + 1
    assert node == REDACTED and levels == 12
    assert GHP not in json.dumps(out)
    assert redact({"a": {"b": "x"}}, max_depth=1) == {"a": REDACTED}
    assert redact({"a": {"b": "x"}}, max_depth=2) == {"a": {"b": "x"}}
    assert redact({"a": 1}, max_depth=0) == REDACTED


def test_cycles_terminate():
    a: dict = {"k": "v"}
    a["self"] = a
    out = redact(a)
    assert out["k"] == "v"
    json.dumps(out)  # finite


class TestRunlogIntegration:
    @pytest.fixture(autouse=True)
    def _swarm_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SWARM_DIR", str(tmp_path))

    def test_emit_redacts_payload_and_leaves_caller_untouched(self, tmp_path):
        payload = {"msg": f"leak {GHP}", "env": {"API_KEY": "hunter2hunter2"}, "task_id": "T7f3a-be", "digest": DIGEST,
                   "list": ["Bearer abcdefghij0123456789xyz"]}
        snap = copy.deepcopy(payload)
        rec = runlog.emit("t.event", payload, source="test", correlation_id="c1", task_id="T7f3a-be", root=tmp_path, tee=False)
        assert payload == snap
        line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert REDACTED in line
        for secret in (GHP, "hunter2hunter2", "abcdefghij0123456789xyz"):
            assert secret not in line
        stored = json.loads(line.splitlines()[-1])
        assert stored["payload"] == rec["payload"]
        assert stored["payload"]["msg"] == f"leak {REDACTED}"
        assert stored["payload"]["env"] == {"API_KEY": REDACTED}
        assert stored["payload"]["task_id"] == "T7f3a-be" and stored["payload"]["digest"] == DIGEST
        assert stored["task_id"] == "T7f3a-be" and stored["correlation_id"] == "c1"

    def test_emit_still_drops_lease_id(self, tmp_path):
        rec = runlog.emit("t.event", {"lease_id": "LEASE-123", "notes": '{"lease_id": "LEASE-9", "k": 1}', "n": 1},
                          source="test", root=tmp_path, tee=False)
        assert "lease_id" not in rec["payload"]
        assert "LEASE-9" not in rec["payload"]["notes"] and "LEASE-123" not in json.dumps(rec)
        assert rec["payload"]["n"] == 1

    def test_lease_dropped_and_secret_redacted_together(self, tmp_path):
        rec = runlog.emit("t.event", {"lease_id": "L1", "text": f"tok {GHP}"}, source="test", root=tmp_path, tee=False)
        assert rec["payload"] == {"text": f"tok {REDACTED}"}

    def test_read_events_roundtrip_keeps_structure(self, tmp_path):
        runlog.emit("swarm.run.activity", {"run_id": "T1.a1", "tokens": 5, "text": "edit swarm/x.py"}, source="telemetry",
                    correlation_id="c2", root=tmp_path, tee=False)
        [rec] = runlog.read_events(correlation_id="c2", root=tmp_path)
        assert rec["payload"] == {"run_id": "T1.a1", "tokens": 5, "text": "edit swarm/x.py"}


# --- W1 review R1 fix-ups -------------------------------------------------------------------------------------------

GLUED_NAMES = ["PGPASSWORD", "accesstoken", "refreshtoken", "authtoken", "githubtoken", "clientsecret", "dbpassword",
               "sessiontoken", "DB_PASS", "MYSQL_PWD", "clientkey", "sessionkey", "xapikey", "AWS_SECRETKEY"]


@pytest.mark.parametrize("name", GLUED_NAMES)
def test_glued_credential_names_are_sensitive(name):
    assert is_sensitive_key(name)
    assert redact({"env": {name: "hunter2"}}) == {"env": {name: REDACTED}}
    assert redact_text(f"export {name}=hunter2 && run") == f"export {name}={REDACTED} && run"


@pytest.mark.parametrize("name", ["PWD", "OLDPWD", "gate_pass", "pass", "tokens", "max_tokens", "secrets", "key", "monkey",
                                  "turkey", "bypass", "compass", "passenger", "tokenizer", "secretary"])
def test_glued_suffix_rule_keeps_readable_names(name):
    assert not is_sensitive_key(name)


def test_dotenv_style_pairs_in_text_are_redacted():
    out = redact_text("PGPASSWORD=hunter2 accesstoken=abc refreshtoken='x y' DB_PASS=zzz MYSQL_PWD=qqq PWD=/home/me")
    assert out == (f"PGPASSWORD={REDACTED} accesstoken={REDACTED} refreshtoken={REDACTED} DB_PASS={REDACTED} "
                   f"MYSQL_PWD={REDACTED} PWD=/home/me")


@pytest.mark.parametrize(("text", "expected"), [
    ("Authorization: Basic dXNlcjpwYXNzd29yZA==", f"Authorization: Basic {REDACTED}"),
    ("curl -H 'Proxy-Authorization: Basic dXNlcjpwYXNz'", f"curl -H 'Proxy-Authorization: Basic {REDACTED}'"),
    ("password: hunter2", f"password: {REDACTED}"),
    ("db_password : hunter2 next", f"db_password : {REDACTED} next"),
    ("password: 'two words'", f"password: {REDACTED}"),
    ("X-Api-Key: abcd1234efgh", f"X-Api-Key: {REDACTED}"),
    ("token = abc123", f"token = {REDACTED}"),
    ("refreshtoken: a1b2c3", f"refreshtoken: {REDACTED}"),
    ("run --password hunter2 --verbose", f"run --password {REDACTED} --verbose"),
    ("tool --db-password 'p w' --x", f"tool --db-password {REDACTED} --x"),
    ("{'password': 'x', 'user': 'bob'}", f"{{'password': '{REDACTED}', 'user': 'bob'}}"),
    ("{'api_key': \"it's\"}", f"{{'api_key': \"{REDACTED}\"}}"),
    ('{"data": "{\\"password\\": \\"x y\\", \\"n\\": 1}"}', '{"data": "{\\"password\\": \\"' + REDACTED + '\\", \\"n\\": 1}"}'),
    ("DB_PASS=abc MYSQL_PWD=def", f"DB_PASS={REDACTED} MYSQL_PWD={REDACTED}"),
])
def test_additional_secret_syntaxes(text, expected):
    assert redact_text(text) == expected
    assert redact_text(expected) == expected  # idempotent


@pytest.mark.parametrize("text", [
    "basic infrastructure is fine", "the basic idea: keep it short", "tokens: 1200 max_tokens=5 secrets: pass",
    "key: S-be sort_key: z cache_key=k1", "PWD=/home/me OLDPWD=/tmp", "run --verbose --password --other", "run --tokens 5 --max-tokens 9",
    "time: 12:30 note: ok", "status: done", "gate_pass: yes", "T7f3a-be.a1 finished", "author: ann authority: root",
    "mailto:bob@example.com", "ssh://git@github.com/x/y.git", "https://example.com/a?b=c:d@e",
    "Authorization: Bearer abcdefghij0123456789xyz".replace("abcdefghij0123456789xyz", REDACTED),
    "{'user': 'bob', 'tokens': 12}", "token://not-a-pair",
])
def test_ordinary_text_and_ids_untouched_by_new_syntaxes(text):
    assert redact_text(text) == text


@pytest.mark.parametrize(("text", "expected"), [
    ("postgres://user:p@ss@host/db", f"postgres://{REDACTED}@host/db"),
    ("git clone https://bob:a@b@c@example.com/r.git", f"git clone https://{REDACTED}@example.com/r.git"),
    ("redis://:onlypw@cache:6379/0", f"redis://{REDACTED}@cache:6379/0"),
])
def test_url_passwords_containing_at_sign(text, expected):
    assert redact_text(text) == expected


def _timed(s: str) -> float:
    import time
    t0 = time.perf_counter()
    redact_text(s)
    return time.perf_counter() - t0


@pytest.mark.parametrize("name", ["dot", "dash", "plus", "scheme_colons", "dashes", "b64url", "eyj_dash", "bearer", "pem",
                                  "quote_eq", "pairs", "kv", "flags", "userinfo", "backslashes", "colon_pairs"])
def test_adversarial_200kb_inputs_scan_in_bounded_time(name):
    import base64
    n = 200_000
    cases = {
        "dot": "a." * (n // 2), "dash": "a-" * (n // 2), "plus": "a+" * (n // 2),
        "scheme_colons": "x://" + ":" * n, "dashes": "-" * n,
        "b64url": base64.urlsafe_b64encode(bytes(range(256)) * (n // 340)).decode(),
        "eyj_dash": "eyJ-" * (n // 4), "bearer": "bearer " * (n // 7),
        "pem": "-----BEGIN " * (n // 11), "quote_eq": 'a="' * (n // 3), "pairs": '"a":"' * (n // 5),
        "kv": "a=b;" * (n // 4), "flags": "--password " * (n // 11),
        "userinfo": ("a://" + "u" * 200 + ":") * (n // 205), "backslashes": '"a":"' + "\\" * n, "colon_pairs": "token: " * (n // 7),
    }
    assert _timed(cases[name]) < 1.0  # was 1-4 s (quadratic) before the quantifiers were bounded; generous for slow CI


# A quoted secret in the summary of a saved result block is JSON-escaped on disk (`\"..\"`); the secret must go and the
# JSON string around it must stay intact.
_ESCAPED_CASES = [
    ("env_assign_quoted", r'Set DB_PASSWORD=\"hunter2-very-secret\" in .env', f"Set DB_PASSWORD={REDACTED} in .env"),
    ("export_quoted", r'run export API_KEY=\"hunter2-very-secret\" first', f"run export API_KEY={REDACTED} first"),
    ("flag_quoted", r'pass --password \"hunter2-very-secret\" to it', f"pass --password {REDACTED} to it"),
    ("env_assign_plain", "Set DB_PASSWORD=hunter2-very-secret in .env", f"Set DB_PASSWORD={REDACTED} in .env"),
    ("flag_plain", "pass --password hunter2-very-secret to it", f"pass --password {REDACTED} to it"),
]


@pytest.mark.parametrize(("label", "text", "expected"), _ESCAPED_CASES, ids=[c[0] for c in _ESCAPED_CASES])
def test_json_escaped_quoted_secrets_are_redacted_and_the_string_stays_intact(label, text, expected):
    out = redact_text(text)
    assert out == expected and "hunter2" not in out
    assert json.loads(f'"{out}"') == out  # still a valid JSON string body


def test_a_backslash_in_a_bare_value_is_still_consumed_unless_it_starts_an_escaped_quote():
    assert redact_text(r"set PASSWORD=ab\cd now") == f"set PASSWORD={REDACTED} now"
    assert redact_text(r"C:\x --password a\b\c") == rf"C:\x --password {REDACTED}"


@pytest.mark.parametrize("filler", ['\\"' * 500_000, "\\" * 1_000_000, '"\\' * 500_000, 'a=\\"' * 250_000])
def test_escaped_quote_floods_scan_in_bounded_time(filler):
    assert _timed("DB_PASSWORD=" + filler) < 1.0
    assert _timed("--password " + filler) < 1.0


# ---- randomized option lists: a benign assignment must never hide a later sensitive one ---------------------------------
_BENIGN_NAMES = ("user", "name", "id", "a", "b", "mode", "region", "count", "host", "retries", "x1", "role", "timeout")
_SECRET_NAMES = ("password", "token", "API_KEY", "SECRET", "client_secret", "db_password", "access_token", "authToken")
_SEPARATORS = (",", ";", "&", " ")
_PREFIXES = ("", "run ", "https://h.test/p?", "cmd: ", "{", "set ")
_SUFFIXES = ("", " done", "\nnext")
_GENERATED_SEED, _GENERATED_COUNT = 20241019, 3000
_GENERATED_PATH = Path(__file__).parent / "fixtures" / "redact_generated.json"


def generate_option_cases(seed: int, count: int) -> list[dict]:
    """`count` seeded option lists plus the output the spec demands (a simple reference model, independent of the regexes).

    A sensitive name masks its whole value, and an unquoted value runs through `,` to the next `;`, `&` or space, so a pair
    joined to a sensitive one by `,` is inside its value (and gone); every other pair is a benign assignment and survives.
    """
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        pairs = []  # (separator before it, name, value, sensitive)
        for j in range(rng.randrange(1, 7)):
            sensitive = rng.random() < 0.4
            name = rng.choice(_SECRET_NAMES if sensitive else _BENIGN_NAMES)
            value = f"S{i}x{j}" if sensitive else "v" + "".join(rng.choice("abcdef0123456789") for _ in range(rng.randrange(1, 6)))
            if sensitive and rng.random() < 0.3:  # a token-shaped prefix inside the value: the name still owns the whole value
                value = rng.choice((f"ghp_,{value}", f"sk-{'a' * 16}{value}", f"AKIAIOSFODNN7EXAMPLE,{value}", f"sk-{'a' * 16},{value},t"))
            pairs.append((rng.choice(_SEPARATORS) if j else "", name, value, sensitive))
        prefix, suffix = rng.choice(_PREFIXES), rng.choice(_SUFFIXES)
        text, expected, inside = prefix, prefix, False
        for sep, name, value, sensitive in pairs:
            text += f"{sep}{name}={value}"
            if inside and sep == ",":
                continue  # part of the preceding secret's value
            inside = sensitive
            expected += f"{sep}{name}={REDACTED if sensitive else value}"
        cases.append({"input": text + suffix, "expected": expected + suffix,
                      "secrets": [v for _, _, v, s in pairs if s]})
    return cases


def test_generated_option_lists_follow_the_reference_model():
    cases = generate_option_cases(_GENERATED_SEED, _GENERATED_COUNT)
    assert len(cases) == _GENERATED_COUNT
    for case in cases:
        out = redact_text(case["input"])
        assert out == case["expected"], case["input"]
        assert not any(secret in out for secret in case["secrets"]), case["input"]  # (a) no sensitive value survives


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_other_seeds_follow_the_reference_model(seed):
    for case in generate_option_cases(seed, 500):
        assert redact_text(case["input"]) == case["expected"], case["input"]


_WILD_SEED, _WILD_COUNT = 20241020, 3000
_WILD_SECRET_NAMES = ("password", "token", "API_KEY", "SECRET", "client_secret", "db_password", "access_token", "authToken",
                      "--password", "--api-key")
_WILD_BENIGN_NAMES = ("user", "name", "id", "a", "mode", "region", "note", "host", "--mode", "-v", "k.x", "a-b")
_WILD_JOINERS = (",", " ", ";", "&", ", ", "\n", " ; ", ",\t", "?", "/", "&amp;")
_WILD_SEPARATORS = ("=", " = ", ": ", ":", "= ", "  :   ")


def generate_wild_cases(seed: int, count: int) -> list[dict]:
    """Option lists in every shape that has fooled a scanner so far: benign and sensitive names (flags too), `=` / `:` with
    spaces, bare / quoted / escaped-quoted / comma-bearing values, `, ; & ? /` joiners. Each secret is a unique `S<i>_<j>q`."""
    rng = random.Random(seed)
    cases = []
    for i in range(count):
        units, secrets = [], []
        for j in range(rng.randrange(1, 6)):
            sensitive = rng.random() < 0.5
            name = rng.choice(_WILD_SECRET_NAMES if sensitive else _WILD_BENIGN_NAMES)
            sep = " " if name.startswith("-") else rng.choice(_WILD_SEPARATORS)
            sv = f"S{i}_{j}q"
            if sensitive:
                secrets.append(sv)
            forms = [sv, f'"{sv}"', f"'{sv}'", f"{sv},{sv}", f"{sv}=x", f'"{sv} w"', f"{sv}\\x", f'"a b,{sv}"', f'\\"{sv}\\"']
            forms += [f"ghp_,{sv}", f"sk-{'a' * 16},{sv}", f"AKIAIOSFODNN7EXAMPLE,{sv},z", f"sk-{'a' * 16}{sv}"]  # token shape, then a tail
            if "=" in sep or name.startswith("-"):
                forms.append(f"//{sv}")  # `NAME: //x` is a URL scheme (`token://x`), deliberately not masked
            value = rng.choice(forms)
            units.append(f"{name}{sep}{value}")
        text = rng.choice(("", "run ", "{", "https://h.test/x?"))
        for k, unit in enumerate(units):
            text += unit + (rng.choice(_WILD_JOINERS) if k < len(units) - 1 else "")
        cases.append({"input": text + rng.choice(("", "}", " done")), "secrets": secrets})
    return cases


def test_wild_option_lists_never_leak_a_sensitive_value():
    """Invariant (no reference model: shapes are too varied): a sensitive value never survives, whatever precedes it."""
    leaks = []
    for case in generate_wild_cases(_WILD_SEED, _WILD_COUNT) + generate_wild_cases(11, 3000):
        out = redact_text(case["input"])
        leaks += [(case["input"], out) for secret in case["secrets"] if secret in out]
    assert leaks[:3] == []


def _golden_tokens() -> list[list[str]]:
    refs = [f"{{{{tok:{kind}:{i}{'' if length is None else f':{length}'}}}}}" for kind in _TOKEN_KINDS for i, length in enumerate((None, 40, 513))]
    return [[ref, hashlib.sha256(expand_tokens(ref).encode("utf-8")).hexdigest()[:16]] for ref in refs]


def _generated_fixture() -> dict:
    strings = generate_random_strings(_RANDOM_SEED, _RANDOM_COUNT)
    return {
        "_doc": _FIXTURE_DOC,
        "tokens": _golden_tokens(),
        "seed": _GENERATED_SEED,
        "cases": [{"input": c["input"], "expected": c["expected"]} for c in generate_option_cases(_GENERATED_SEED, _GENERATED_COUNT)],
        "wild_seed": _WILD_SEED,
        "wild": [{"input": c["input"], "expected": redact_text(c["input"])} for c in generate_wild_cases(_WILD_SEED, _WILD_COUNT)],
        "property_seed": _PROPERTY_SEED,
        "property": [c | {"digest": _digest(expand_tokens(c["input"]))} for c in generate_property_cases(_PROPERTY_SEED, _PROPERTY_EXTRA)],
        # the random strings are rebuilt in both languages from the seed and the fragment table; only digests of the Python outputs are stored
        "random": {"seed": _RANDOM_SEED, "count": _RANDOM_COUNT, "fragments": list(_RANDOM_FRAGMENTS),
                   "inputs": hashlib.sha256("\u0000".join(strings).encode("utf-8")).hexdigest()[:16],
                   "digests": [_digest(s) for s in strings]},
    }


def test_generated_fixture_is_current_and_matches_the_python_output():
    """tests/fixtures/redact_generated.json is also run by omp/test/swarm_bar.test.ts, so Python and TypeScript give identical
    output. Regenerate with REGENERATE_REDACT_FIXTURE=1 after an intended behavior change (and review the diff)."""
    wanted = _generated_fixture()
    if os.environ.get("REGENERATE_REDACT_FIXTURE") == "1":
        def rows(items):
            return ",\n".join("  " + json.dumps(c, ensure_ascii=False) for c in items)
        random_part = wanted["random"]
        digest_rows = ",\n".join("   " + ", ".join(json.dumps(d) for d in random_part["digests"][i:i + 10])
                                 for i in range(0, len(random_part["digests"]), 10))
        _GENERATED_PATH.write_text(
            f'{{"_doc": {json.dumps(_FIXTURE_DOC)},\n"tokens": [\n{rows(wanted["tokens"])}\n],\n"seed": {_GENERATED_SEED}, "wild_seed": {_WILD_SEED}, "property_seed": {_PROPERTY_SEED}, "cases": [\n{rows(wanted["cases"])}\n], '
            f'"wild": [\n{rows(wanted["wild"])}\n], "property": [\n{rows(wanted["property"])}\n], "random": {{"seed": {_RANDOM_SEED}, '
            f'"count": {_RANDOM_COUNT}, "inputs": "{random_part["inputs"]}", "fragments": {json.dumps(random_part["fragments"])}, '
            f'"digests": [\n{digest_rows}\n]}}}}\n', encoding="utf-8")
    assert json.loads(_GENERATED_PATH.read_text(encoding="utf-8")) == wanted


def test_the_property_cases_in_the_shared_fixture_are_sound():
    cases = json.loads(_GENERATED_PATH.read_text(encoding="utf-8"))["property"]
    assert len(cases) > 2000
    assert _leaks(cases)[:3] == []


def test_token_placeholders_expand_deterministically_to_provider_shapes():
    """The `tokens` golden digests of the fixture pin the expansion algorithm; omp/test/swarm_bar.test.ts checks the same digests."""
    fixture = json.loads(_GENERATED_PATH.read_text(encoding="utf-8"))
    assert len(fixture["tokens"]) == len(_TOKEN_KINDS) * 3
    for ref, sha in fixture["tokens"]:
        assert hashlib.sha256(expand_tokens(ref).encode("utf-8")).hexdigest()[:16] == sha, ref
    shapes = {"aws_key": r"AKIA[A-Z2-7]{16}", "aws_secret": r"[A-Za-z0-9/+]{40}", "ghp": r"ghp_[A-Za-z0-9]{36}", "github_pat": r"github_pat_[A-Za-z0-9_]{82}",
              "xoxb": r"xoxb-[A-Za-z0-9-]{50}", "sk_live": r"sk_live_[A-Za-z0-9]{24}", "aiza": r"AIza[A-Za-z0-9_-]{35}",
              "sg": r"SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}", "npm": r"npm_[A-Za-z0-9]{36}", "glpat": r"glpat-[A-Za-z0-9_-]{20}",
              "jwt": r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "pem": r"([A-Za-z0-9+/]{64}\n)*[A-Za-z0-9+/]{1,64}"}
    for kind, shape in shapes.items():
        assert re.fullmatch(shape, _token(kind, 9)), kind
    assert expand_tokens("a {{tok:ghp:1}} b {{tok:ghp:1:60}}") == f"a {_token('ghp', 1)} b {_token('ghp', 1, 60)}" and len(_token("ghp", 1, 60)) == 60
    assert _token("ghp", 1) != _token("ghp", 2) and "{{" not in expand_tokens("{{tok:jwt:3:600}}")
    text, spans = expand_with_spans("x={{tok:plain:1:12}};y={{tok:aws_key:2}}")
    assert [text[a:b] for a, b in spans] == [_token("plain", 1, 12), _token("aws_key", 2)]


_SECRET_SHAPES = re.compile(
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{22,}|xox[abprs]-[A-Za-z0-9-]{10,}|(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"
    r"|AIza[0-9A-Za-z_-]{35}|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}|npm_[A-Za-z0-9]{36}|glpat-[A-Za-z0-9_-]{20}"
    r"|eyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{20,}|(?i:aws_secret_access_key)\W{0,4}[A-Za-z0-9/+=]{40}"
    r"|(?i:(?:key|secret|token|passw(?:or)?d)[\"']?\s*[=:]\s*[\"']?)[A-Za-z0-9/+=_~.-]{32,}")
_DOCUMENTED_EXAMPLES = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")


def test_the_shared_fixtures_hold_no_secret_shaped_literal():
    """GitHub push protection rejects a push that carries one: secrets are `{{tok:...}}` placeholders, expanded only at runtime."""
    found = []
    for name in ("redact_generated.json", "redact_cases.json"):
        text = (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")
        found += [(name, m[0][:40]) for m in _SECRET_SHAPES.finditer(text)
                  if not any(example in m[0] for example in _DOCUMENTED_EXAMPLES) and not re.search(r"(.{4,16})\1", m[0])]  # patterned filler (`a` * 16, `0123456789abcdef` * 4) is not a secret
    assert found[:5] == []


def test_value_scanning_is_idempotent_on_generated_cases():
    for case in generate_option_cases(7, 500):
        once = redact_text(case["input"])
        assert redact_text(once) == once


def test_megabyte_assignment_floods_are_scanned_in_linear_time_and_still_find_the_password():
    import time
    for unit in ("a=", "a=a=", "user=a,", "user=a;", "x=1&"):
        text = unit * (1_000_000 // len(unit)) + "password=hunter2"
        started = time.perf_counter()
        out = redact_text(text)
        assert time.perf_counter() - started < 3.0, unit
        assert out.endswith("password=" + REDACTED) and "hunter2" not in out, unit


# ---- RV-J fixes: new carriers (the same shapes run as shared vectors in both implementations) -----------------------------
def test_new_carrier_flood_inputs_are_linear_and_still_find_the_password():
    import time
    for unit in ("mysql ", "docker login ", "sshpass -p ", "-u a:b ", "- name: PASSWORD\n  value: ", "password: |\n  ", "STRIPE_KEY=",
                 "redis-cli ", "- name: ", "name: PASSWORD\n", "password:  "):
        text = unit * (1_000_000 // len(unit)) + "\npassword=hunter2"
        started = time.perf_counter()
        out = redact_text(text)
        assert time.perf_counter() - started < 3.0, unit
        assert out.endswith("password=" + REDACTED) and "hunter2" not in out, unit


def test_ten_megabytes_of_new_carrier_text_complete():
    assert redact_text(("- name: PASSWORD\n  value: x\nmysql -u a -pb db\n" * 250_000)).count(REDACTED) == 500_000


# ---- GL6 round 6: command checks read whole arguments, Markdown delimiters around an option ---------------------------------------------
_COMMAND_LINES = {  # name -> (text before the secret, text after it, whether the flag glues the value to itself)
    "docker login": ("docker login -u me -p ", ""),
    "podman login": ("podman login -p ", " reg.io"),
    "helm registry login": ("helm registry login reg.io -p ", ""),
    "az login": ("az login -u me -p ", ""),
    "sshpass": ("sshpass -p ", " ssh host"),
    "mysql": ("mysql -u root -p", " db"),
    "mongosh": ("mongosh --host h -u me -p ", ""),
    "redis-cli": ("redis-cli -a ", " ping"),
    "sqlcmd": ("sqlcmd -S h -U sa -P ", ""),
    "ssh-keygen": ("ssh-keygen -t ed25519 -N ", " -f k"),
}


@pytest.mark.parametrize("count", [0, 1, 10, 11, 40])
@pytest.mark.parametrize("size", [1, 128, 129, 1000])
@pytest.mark.parametrize("name", list(_COMMAND_LINES))
def test_command_flags_are_checked_after_arguments_of_any_length_and_number(name, size, count):
    """A password flag is found after any number of arguments of any length (a regex that skipped at most 10 arguments of 128 characters
    left the password of `docker login -u admin:<300 characters> -p pw` readable)."""
    before, after = _COMMAND_LINES[name]
    secret = _token("plain", size + count, 16)
    head, tail = before.split(" -")[0], " -" + " -".join(before.split(" -")[1:])
    arguments = "".join(f" --argument{i}=" + "v" * size for i in range(count))
    text = head + arguments + tail + secret + after
    assert redact_text(text) == head + arguments + tail + REDACTED + after


@pytest.mark.parametrize("wrapper", [("`", "`"), ("(", ")"), ("[", "]"), ("<", ">"), ("**", "**"), ("*", "*"), ("__", "__"), ("~~", "~~"), ('"', '"'), ("'", "'"),
                                     ("{", "}"), ("**`", "`**"), ("(`", "`)"), ("[`", "`](docs)"), ('\\"', '\\"'), ("$(", ")"), ("<code>", "</code>")])
@pytest.mark.parametrize("option", ["--user admin:%s", "-u admin:%s", "--user=admin:%s", "-u=admin:%s", "--proxy-user a:%s", "docker login -u me -p %s",
                                    "mysql -u root -p%s db", "sshpass -p %s ssh h", "redis-cli -a %s ping", "sqlcmd -S h -P %s", "openssl rsa -passin pass:%s"])
def test_markdown_delimiters_around_a_command_option_keep_its_password_masked(wrapper, option):
    """`--user admin:hunter2` in backticks, parentheses, bold, brackets, quotes ... : the option may follow any delimiter."""
    secret = _token("plain", 7, 18)
    opener, closer = wrapper
    text = f"Run {opener}{option % secret}{closer} now"
    out = redact_text(text)
    assert secret not in out and REDACTED in out and out.startswith(f"Run {opener}") and out.count(REDACTED) == 1, (text, out)


def test_the_round_six_review_findings():
    assert redact_text("docker login -u admin: -p pw123") == f"docker login -u admin: -p {REDACTED}"
    assert redact_text("docker login -u admin:" + "x" * 200 + " -p pw123 reg.io") == f"docker login -u {REDACTED} -p {REDACTED} reg.io"
    assert redact_text("Run `--user admin:hunter2` now") == f"Run `--user {REDACTED} now"
    assert redact_text("Run (--user=admin:hunter2)") == f"Run (--user={REDACTED}"
    assert redact_text("**--user admin:hunter2**") == f"**--user {REDACTED}"
    assert redact_text('"--user admin:hunter2" ok') == f'"--user {REDACTED}" ok'


def test_a_new_line_or_a_semicolon_ends_the_command_whose_flags_are_checked():
    unchanged = "docker login reg.io\nls -p x\nmysql -u root -p\nredis-cli -h h -p 6379 ping\ndocker login reg.io; ls -p x && tar -a y"
    assert redact_text(unchanged) == unchanged
    assert redact_text("mysql -u root\nls -pSECRETX") == "mysql -u root\nls -pSECRETX"


def test_unterminated_and_escaped_quoted_command_values():
    assert redact_text("sshpass -p 'abc def") == f"sshpass -p {REDACTED}"
    assert redact_text('docker login -p \\"abc def\\" reg') == f"docker login -p {REDACTED} reg"
    assert redact_text('curl -u "admin:my pass" https://h') == f"curl -u {REDACTED} https://h"


def test_a_credential_value_that_swallowed_the_next_sensitive_name_still_masks_that_names_value():
    assert redact_text('run --token \\"--password hunter2') == f'run --token \\"{REDACTED} {REDACTED}'
    assert redact_text('--api-key = {--password=\\"user:hunter2\\"') == f"--api-key {REDACTED} {REDACTED}"


@pytest.mark.parametrize("unit", ["mysql -u a ", "docker login x ", "`--user a:", "docker login -p ", "redis-cli -a ", "-passin pass:", "--cmd=mysql ", "-u=-u=",
                                  "x -u ", "-u  ", "mysql -p'", 'sshpass -p "', "docker login -u a: -p ", "sqlcmd -P ", "ssh-keygen -N ", "__-u a:", "(-u a:"])
def test_command_line_floods_are_linear_and_still_find_the_trailing_password(unit):
    import time
    text = unit * (1_000_000 // len(unit)) + "\npassword=hunter2"
    started = time.perf_counter()
    out = redact_text(text)
    assert time.perf_counter() - started < 3.0, unit
    assert out.endswith("password=" + REDACTED) and "hunter2" not in out, unit


@pytest.mark.parametrize("text", [
    f"{GHP} API_KEY=zzzz1234 mysql -u root -pS3cretPass db docker login -u me -p S3cretPass",
    "- name: DB_PASSWORD\n  value: pw1\nsecret: |\n  a\n  b\nplain: x\n",
    "const cfg = { password: \"x1\", user: 'x' }",
    "STRIPE_KEY={{tok:mixed:3:33}} curl -u a:b redis-cli -a pw",
])
def test_redaction_is_idempotent_on_the_new_carriers(text):
    once = redact_text(expand_tokens(text))
    assert redact_text(once) == once


def test_dict_values_of_vendor_key_names_are_masked_by_shape():
    long = _token("mixed", 4, 33)
    assert redact({"STRIPE_KEY": long, "sort_key": long, "cache_key": long, "SERVICE_KEY": "short"}) == {
        "STRIPE_KEY": REDACTED, "sort_key": long, "cache_key": long, "SERVICE_KEY": "short"}
    assert redact_text(f'{{"STRIPE_KEY": "{long}", "sort_key": "{long}"}}') == f'{{"STRIPE_KEY": "{REDACTED}", "sort_key": "{long}"}}'


def test_account_key_names_are_sensitive():
    assert is_sensitive_key("AccountKey") and is_sensitive_key("STORAGE_ACCOUNT_KEY") and is_sensitive_key("SharedAccessKey")


# ---- RV-J P2-1: identifiers are never redacted ------------------------------------------------------------------------------
@pytest.mark.parametrize("task_id", ["sk-onboarding-flow-v2", "github_pat_" + "ABCDEFGHIJKLMNOPQRSTUV", "T-plain-0001"])
def test_runlog_keeps_identifier_fields_and_redacts_free_text(tmp_path, monkeypatch, task_id):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    from swarm import runlog
    rec = runlog.emit("swarm.run.activity", {"run_id": f"{task_id}.a1", "handover_id": f"H-{task_id}", "task_id": task_id,
                                             "text": f"token {GHP} ok", "agent": "a05-backend"},
                      source="test", correlation_id="c1", task_id=task_id, root=tmp_path, tee=False)
    assert rec["payload"] == {"run_id": f"{task_id}.a1", "handover_id": f"H-{task_id}", "task_id": task_id,
                              "text": f"token {REDACTED} ok", "agent": "a05-backend"}
    assert runlog.read_events(task_id=task_id, root=tmp_path)[0]["payload"]["run_id"] == f"{task_id}.a1"


def test_runlog_redacts_an_identifier_field_that_is_not_id_shaped(tmp_path, monkeypatch):
    monkeypatch.setenv("SWARM_DIR", str(tmp_path / ".swarm"))
    from swarm import runlog
    rec = runlog.emit("x.y", {"run_id": f"oops password={'hunter2hunter2'}"}, source="test", root=tmp_path, tee=False)
    assert "hunter2" not in rec["payload"]["run_id"]


# ---- secret placeholders: no secret-shaped literal sits in the repo (GitHub push protection rejects them) ----------------------------
# A fixture stores `{{tok:KIND:INDEX}}` or `{{tok:KIND:INDEX:LENGTH}}`; this file and omp/test/swarm_bar.test.ts expand it at runtime with
# ONE algorithm, written identically in both languages (`_token` here, `token` there; pinned by the `tokens` golden digests of the fixture):
#   seed  = FNV-1a 32-bit over the UTF-8 bytes of "KIND:INDEX:LENGTH" (LENGTH is 0 when omitted); state = seed, or 1 when that is 0
#   next(bound) = one xorshift32 step (x ^= x << 13; x ^= x >> 17; x ^= x << 5, 32 bits), then state % bound
#   every random character is ALPHABET[next(len(ALPHABET))], drawn left to right; LENGTH is the whole token (default: the provider's own)
#   KIND: ghp gho ghs ghu (prefix + alnum, 40) github_pat (93) xoxb xoxp (55) sk (51) sk_live (32) aiza (39) glpat (26) npm (40)
#         aws_key (AKIA + [A-Z2-7], 20) aws_secret ([A-Za-z0-9/+], 40) sg (SG.22.43) jwt (eyJ.part.sig, 80) pem (64-column body, 128)
#         plain comma quoted urlsafe bearer b64 mixed userinfo user cli lines (generic value shapes of a given LENGTH, default 24..33)
# `expected` outputs never hold a secret (they hold [REDACTED]); a secret check compares against the expanded token.
_TOKEN_RE = re.compile(r"\{\{tok:([a-z0-9_]+):(\d+)(?::(\d+))?\}\}")
_ALNUM_CHARS = string.ascii_letters + string.digits
_PLAIN_CHARS = _ALNUM_CHARS + "_.-+/=~"
_URLSAFE_CHARS = _ALNUM_CHARS + "_-"
_TOKEN_PREFIXED = {  # kind -> (prefix, alphabet after it, default total length)
    "ghp": ("ghp_", _ALNUM_CHARS, 40), "gho": ("gho_", _ALNUM_CHARS, 40), "ghs": ("ghs_", _ALNUM_CHARS, 40), "ghu": ("ghu_", _ALNUM_CHARS, 40),
    "github_pat": ("github_pat_", _ALNUM_CHARS + "_", 93), "xoxb": ("xoxb-", _ALNUM_CHARS + "-", 55), "xoxp": ("xoxp-", _ALNUM_CHARS + "-", 55),
    "sk": ("sk-", _URLSAFE_CHARS, 51), "sk_live": ("sk_live_", _ALNUM_CHARS, 32), "aiza": ("AIza", _URLSAFE_CHARS, 39),
    "glpat": ("glpat-", _URLSAFE_CHARS, 26), "npm": ("npm_", _ALNUM_CHARS, 40), "aws_key": ("AKIA", string.ascii_uppercase + "234567", 20),
}

_TOKEN_KINDS = (*_TOKEN_PREFIXED, "sg", "aws_secret", "jwt", "plain", "comma", "quoted", "urlsafe", "bearer", "b64", "mixed", "pem", "userinfo",
                "user", "cli", "lines")
_FIXTURE_DOC = ("Shared vectors for swarm/redact.py (tests/test_redact.py) and omp/src/swarm_bar.ts (omp/test/swarm_bar.test.ts). No secret-shaped "
                "literal is stored (GitHub push protection): a planted secret is a placeholder `{{tok:KIND:INDEX}}` or `{{tok:KIND:INDEX:LENGTH}}` "
                "that both suites expand at runtime with one algorithm. seed = FNV-1a 32 over the UTF-8 bytes of 'KIND:INDEX:LENGTH' (LENGTH 0 when "
                "omitted), state = seed or 1; next(bound) = xorshift32 step (<<13, >>17, <<5) then state % bound; each random character is "
                "ALPHABET[next(len)], drawn in order; LENGTH is the whole token. `property` secrets are exactly the expanded placeholders; "
                "`tokens` pins the algorithm as [placeholder, first 16 hex of sha256(expansion)]; `expected` holds [REDACTED], never a secret.")


class _Xorshift:
    """xorshift32: the same few lines in tests (Python) and omp/test/swarm_bar.test.ts, so both build identical strings from a seed."""

    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF or 1

    def next(self, bound: int) -> int:
        x = self.state
        x = (x ^ (x << 13)) & 0xFFFFFFFF
        x = x ^ (x >> 17)
        x = (x ^ (x << 5)) & 0xFFFFFFFF
        self.state = x
        return x % bound


def _fnv1a32(text: str) -> int:
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h = ((h ^ byte) * 0x01000193) & 0xFFFFFFFF
    return h


def _pick(rng: _Xorshift, alphabet: str, n: int) -> str:
    return "".join(alphabet[rng.next(len(alphabet))] for _ in range(n))


def _token(kind: str, index: int, length: int | None = None) -> str:
    rng = _Xorshift(_fnv1a32(f"{kind}:{index}:{length or 0}"))
    if kind in _TOKEN_PREFIXED:
        prefix, alphabet, default = _TOKEN_PREFIXED[kind]
        return prefix + _pick(rng, alphabet, max(length or default, len(prefix)) - len(prefix))
    if kind == "sg":
        return "SG." + _pick(rng, _URLSAFE_CHARS, 22) + "." + _pick(rng, _URLSAFE_CHARS, 43)
    if kind == "aws_secret":
        return _pick(rng, _ALNUM_CHARS + "/+", length or 40)
    if kind == "jwt":
        rest = max(length or 80, 15) - 15  # `eyJ` + 5, `.` + 5, `.`
        a, b = rng.next(rest + 1), rng.next(rest + 1)
        first, second = min(a, b), max(a, b)
        return "eyJ" + _pick(rng, _URLSAFE_CHARS, 5 + first) + "." + _pick(rng, _URLSAFE_CHARS, 5 + second - first) + "." + _pick(rng, _URLSAFE_CHARS, rest - second)
    if kind == "plain":
        n = length or 24
        return _pick(rng, _ALNUM_CHARS, 1) + _pick(rng, _PLAIN_CHARS, n - 1)
    if kind == "comma":
        n, text = max(length or 24, 3), ""
        while len(text) < n:
            text += _pick(rng, _ALNUM_CHARS, 1 + rng.next(12)) + ","
        return text[: n - 1] + "z"
    if kind == "quoted":
        n = length or 24
        head = _pick(rng, _ALNUM_CHARS, 1)
        if n == 1:
            return head
        return head + _pick(rng, _ALNUM_CHARS + " ,;:=/+.-", n - 2) + _pick(rng, _ALNUM_CHARS, 1)
    if kind == "urlsafe":
        return _pick(rng, _URLSAFE_CHARS, length or 32)
    if kind == "bearer":
        return _pick(rng, _ALNUM_CHARS + "._~+/=-", length or 32)
    if kind == "b64":
        return _pick(rng, _ALNUM_CHARS + "+/_-", length or 32)
    if kind == "mixed":
        body = _pick(rng, _ALNUM_CHARS + "_+/=~.-", max(length or 33, 3))
        upper, lower, digit = (string.ascii_uppercase[rng.next(26)], string.ascii_lowercase[rng.next(26)], string.digits[rng.next(10)])
        return upper + lower + digit + body[3:]
    if kind == "pem":
        n = length or 128
        body = _pick(rng, _ALNUM_CHARS + "+/", n)
        return "\n".join(body[i:i + 64] for i in range(0, n, 64))
    if kind == "userinfo":
        n = length or 24
        head = _pick(rng, _ALNUM_CHARS, 1)
        if n == 1:
            return head
        return head + _pick(rng, _ALNUM_CHARS + "_.~%!$'()*+,;=:@-", n - 2) + _pick(rng, _ALNUM_CHARS, 1)
    if kind == "user":
        return _pick(rng, _ALNUM_CHARS + "_.~%-", length or 24)
    if kind == "cli":
        return _pick(rng, _ALNUM_CHARS, 1) + _pick(rng, _ALNUM_CHARS + "_.~%+,=:@!*-", (length or 24) - 1)
    if kind == "lines":
        n = length or 80
        body = _pick(rng, _ALNUM_CHARS, n)
        return "\n  ".join(body[i:i + 40] for i in range(0, n, 40))
    raise ValueError(f"unknown token kind {kind!r}")


def expand_tokens(text: str) -> str:
    return _TOKEN_RE.sub(lambda m: _token(m[1], int(m[2]), int(m[3]) if m[3] else None), text)


def expand_with_spans(text: str) -> tuple[str, list[tuple[int, int]]]:
    """The expanded text and the `(start, end)` of every expanded token in it."""
    out, spans, last, size = [], [], 0, 0
    for m in _TOKEN_RE.finditer(text):
        out.append(text[last:m.start()])
        size += m.start() - last
        token = _token(m[1], int(m[2]), int(m[3]) if m[3] else None)
        spans.append((size, size + len(token)))
        out.append(token)
        size += len(token)
        last = m.end()
    out.append(text[last:])
    return "".join(out), spans


# carrier secret kind -> (token kind, smallest length); `vendor` draws one of _VENDORS
_VENDORS = (("ghp", 24), ("sk", 19), ("xoxb", 15), ("github_pat", 31), ("glpat", 22), ("npm", 34), ("aws_key", 20))  # (token kind, smallest length)
_KINDS = {"plain": ("plain", 1), "comma": ("comma", 3), "jwt": ("jwt", 15), "vendor": ("vendor", 20), "bearer": ("bearer", 16),
          "b64": ("b64", 4), "mixed": ("mixed", 20), "quoted": ("quoted", 1), "jsontext": ("quoted", 1), "pem": ("pem", 1),
          "userinfo": ("userinfo", 1), "user": ("user", 1), "cli": ("cli", 1), "lines": ("lines", 1)}


# ---- class-level properties (GL5-B): every detector reads the ORIGINAL text and reports spans; one union replaces them -------------
# 1. soundness: a secret of ANY length (1..5000) planted in ANY carrier is covered, byte for byte, by the reported spans;
# 2. union: spans(a + "\n" + b) == spans(a) + shifted spans(b), in every order (a detector never reads another's rewrite);
# 3. parity: omp/test/swarm_bar.test.ts replays the same cases (and 50k random strings) and gets identical spans and output.
_PROPERTY_SEED, _PROPERTY_EXTRA = 20241021, 1200
_RANDOM_SEED, _RANDOM_COUNT = 20241022, 50_000
_SENSITIVE_NAMES = ("password", "API_KEY", "github_token", "client_secret", "db_password", "ANTHROPIC_API_KEY", "SECRET", "access_token",
                    "authToken", "PGPASSWORD", "refreshtoken")
_STRICT_NAMES = ("password", "token", "API_KEY", "client_secret", "db_password", "access_token", "authToken")  # prose-safe `NAME: value`
_FLAG_NAMES = ("--password", "--api-key", "--db-password", "-password", "--client-secret", "--token")
_EDGE_LENGTHS = (1, 2, 20, 160, 161, 512, 513, 5000)  # 160 / 512 were the value caps of the old patterns


_ANY = ("plain", "comma", "jwt")
# (label, secret kinds, template): %S% the planted secret, %N% a sensitive name, %T% a prose-safe sensitive name, %F% a sensitive flag
_CARRIERS = (
    ("env_bare", ("plain", "comma", "jwt", "vendor", "mixed"), "%N%=%S%"),
    ("env_dq", ("quoted", "plain", "comma", "jwt"), '%N%="%S%"'),
    ("env_sq", ("quoted", "plain", "jwt"), "%N%='%S%'"),
    ("env_escaped", ("quoted", "plain"), 'set %N%=\\"%S%\\" now'),
    ("env_export", ("plain", "jwt"), "export %N%=%S%"),
    ("env_unterminated", ("plain", "jwt"), '%N%="%S%'),
    ("colon_bare", ("plain", "comma", "jwt", "vendor"), "%T%: %S%"),
    ("colon_spaced", ("plain", "jwt"), "%T% : %S%"),
    ("colon_dq", ("quoted", "plain"), '%T%: "%S%"'),
    ("flag_space", _ANY, "run %F% %S%"),
    ("flag_equals", ("plain", "jwt"), "run %F%=%S%"),
    ("flag_dq", ("quoted", "plain"), 'run %F% "%S%"'),
    ("flag_sq", ("quoted",), "run %F% '%S%'"),
    ("flag_escaped", ("quoted",), 'run %F% \\"%S%\\"'),
    ("json_dq", ("jsontext", "plain"), '{"%N%": "%S%", "user": "bob"}'),
    ("json_sq", ("jsontext",), "{'%N%': '%S%'}"),
    ("json_escaped", ("jsontext",), '{"data": "{\\"%N%\\": \\"%S%\\", \\"n\\": 1}"}'),
    ("cookie", ("plain", "jwt", "comma", "mixed"), "Cookie: session=%S%; theme=dark"),
    ("cookie_late", ("plain", "jwt"), "Cookie: a=1; theme=dark; session=%S%"),
    ("set_cookie", ("plain", "jwt"), "Set-Cookie: sid=%S%; Path=/; HttpOnly"),
    ("cookie_in_single_quotes", ("plain", "jwt"), "curl -H 'Cookie: sid=%S%' https://h.test"),
    ("cookie_in_double_quotes", ("plain", "jwt"), 'log "Cookie: sid=%S%" end'),
    ("cookie_quoted_value", ("plain", "jwt"), 'Set-Cookie: session="%S%"; Path=/'),
    ("cookie_quoted_middle", ("plain", "jwt"), 'Cookie: a=1; session="%S%"; b=2'),
    ("authorization_token", ("plain", "jwt", "comma"), "Authorization: Token %S%"),
    ("proxy_authorization", ("plain", "jwt"), "Proxy-Authorization: Negotiate %S%"),
    ("x_api_key", ("plain", "jwt", "mixed"), "X-Api-Key: %S%"),
    ("bearer", ("bearer",), "Authorization: Bearer %S%"),
    ("bearer_quoted", ("bearer",), "-H 'Authorization: Bearer %S%'"),
    ("bearer_bare", ("bearer",), "bearer %S%"),
    ("basic", ("b64",), "Authorization: Basic %S%"),
    ("userinfo_password", ("userinfo",), "git clone postgres://user:%S%@host:5432/db now"),
    ("userinfo_empty_user", ("userinfo",), "redis://:%S%@cache:6379/0"),
    ("userinfo_user", ("user",), "https://%S%:x@h.test/p"),
    ("shape_prose", ("jwt", "vendor"), "error: %S% was rejected"),
    ("shape_parenthesized", ("jwt", "vendor"), "(%S%)"),
    ("shape_query", ("jwt", "vendor"), "https://h.test/cb?code=%S%&x=1"),
    ("pem", ("pem",), "-----BEGIN PRIVATE KEY-----\n%S%\n-----END PRIVATE KEY-----"),
    ("yaml_env_pair", ("plain", "jwt", "vendor"), "- name: DB_PASSWORD\n  value: %S%\n- name: X\n  value: y"),
    ("yaml_env_pair_quoted", ("plain", "jwt"), '  - name: API_TOKEN\n    value: "%S%"'),
    ("yaml_block", ("lines",), "secret: |\n  %S%\nplain: x"),
    ("cli_curl_user", ("cli",), "curl -u user:%S% https://h.test"),
    ("cli_curl_long_user", ("cli",), "curl --user admin:%S% -s"),
    ("cli_curl_quoted", ("cli",), 'curl -u "user:%S%" https://h'),
    ("cli_mysql_glued", ("cli",), "mysql -u root -p%S% db"),
    ("cli_sshpass", ("cli",), "sshpass -p %S% ssh h"),
    ("cli_docker_login", ("cli",), "docker login -u me -p %S% registry.io"),
    ("cli_redis", ("cli",), "redis-cli -a %S% ping"),
    ("cli_sqlcmd", ("cli",), "sqlcmd -S h -P %S%"),
    ("cli_openssl", ("cli",), "openssl enc -passin pass:%S%"),
    ("vendor_key_equals", ("mixed",), "STRIPE_KEY=%S%"),
    ("vendor_key_quoted", ("mixed",), 'SUPABASE_SERVICE_ROLE_KEY="%S%"'),
    ("vendor_key_colon", ("mixed",), "STRIPE_KEY: %S%"),
    ("vendor_key_json", ("mixed",), "{'STRIPE_KEY': '%S%'}"),
    ("credential_list", ("comma",), "SUBSTRATE_OPERATOR_TOKENS=%S%"),
    ("chained_colon", ("plain",), "password=a,token = %S%"),
    ("chained_flag", ("plain",), "key=a;--password %S%"),
    ("benign_then_secret", ("plain",), 'user=alice,password="x y",token: %S%'),
    ("env_spaced", ("plain", "jwt"), "%N% = %S%"),
    ("env_spaced_dq", ("quoted", "plain"), '%N% = "%S%"'),
    ("env_query", ("plain", "jwt"), "https://h.test/x?token=%S%&page=2"),
    ("env_dotenv_lines", ("plain", "jwt"), "A=1\nPASSWORD=%S%\nB=2"),
    ("cookie_wide_gap", ("plain", "jwt"), "Cookie:     session=%S%"),
    ("cookie_lowercase", ("plain", "jwt"), "cookie: session=%S%"),
    ("set_cookie_uppercase", ("plain", "jwt"), "SET-COOKIE: sid=%S%; Secure"),
    ("http_crlf", ("plain", "jwt"), "GET / HTTP/1.1\r\nCookie: sid=%S%\r\nHost: x"),
    ("bearer_wide_gap", ("bearer",), "Authorization: Bearer   %S%"),
    ("curl_user_secret", ("user",), "curl -u %S%:pw https://h.test"),
    ("yaml_block_folded", ("lines",), "token: >-\n  %S%\nnext: 1"),
    # round 6: command checks skip complete arguments (any length, any number), Markdown delimiters around an option, a command glued to
    # another word, a value that swallowed the next sensitive name
    ("cli_docker_login_incomplete_user", ("cli",), "docker login -u admin: -p %S% reg.io"),
    ("cli_docker_login_long_user", ("cli",), "docker login -u admin:" + "x" * 200 + " -p %S% reg.io"),
    ("cli_docker_login_long_argument", ("cli",), "docker login --registry-mirror=https://" + "m" * 200 + ".io -p %S%"),
    ("cli_mysql_many_long_arguments", ("cli",), "mysqldump --opt a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 a11 " + "z" * 300 + " -u root -p%S% db"),
    ("cli_redis_many_long_arguments", ("cli",), "redis-cli " + " ".join(f"--opt{i}=" + "v" * 140 for i in range(12)) + " -a %S% ping"),
    ("cli_sqlcmd_long_server", ("cli",), "sqlcmd -S " + "h" * 200 + " -U sa -P %S%"),
    ("cli_sshpass_many_arguments", ("cli",), "sshpass -e -o A=1 -o B=2 -o C=3 -o D=4 -t -t -p %S% ssh -v host"),
    ("cli_helm_registry_long_argument", ("cli",), "helm registry login reg.io --insecure " + "q" * 200 + " -u me -p %S%"),
    ("cli_user_in_backticks", ("cli",), "Run `--user admin:%S%` to log in"),
    ("cli_user_in_parentheses", ("cli",), "(--user admin:%S%)"),
    ("cli_user_in_brackets", ("cli",), "[-u admin:%S%]"),
    ("cli_user_in_angle_brackets", ("cli",), "<--user=admin:%S%>"),
    ("cli_user_in_bold", ("cli",), "**--proxy-user a:%S%**"),
    ("cli_user_in_underscores", ("cli",), "__-u a:%S%__"),
    ("cli_user_in_double_quotes", ("cli",), 'see "--user admin:%S%" above'),
    ("cli_user_after_equals", ("cli",), 'OPTS="-u admin:%S%" x=-u y:%S%'),
    ("cli_docker_login_in_backticks", ("cli",), "`docker login -u me -p %S%`"),
    ("cli_mysql_in_parentheses", ("cli",), "(mysql -u root -p%S% db)"),
    ("cli_command_glued_to_a_flag", ("cli",), "--cmd=redis-cli -a %S% ping"),
    ("cli_command_path", ("cli",), "/usr/bin/mysql -u root -p%S%"),
    ("cli_escaped_quote_value", ("cli",), "docker login -p \\\"%S%\\\" reg"),
    ("flag_swallowed_by_open_quote", ("plain",), "run --token \\\"--password %S%"),
    ("flag_swallowed_name", ("plain",), "--api-key = {--password=\\\"%S%\\\""),
)
_PROP_PREFIXES = ("", "run ", "error: ", "cmd: ", "(", "{", "`", "**", "(`", "[", "<")
_PROP_SUFFIXES = ("", " done", "\nnext line", " ok.", ")", "}", "`", "**", "`)", "]", ">")
_PROP_JOINERS = (" ", "\n", " ; ", "\t")


def _plant(rng: random.Random, label: str, kinds: tuple, template: str, length: int, serial: int) -> str:
    """One carrier: its text, with the planted secret as a `{{tok:KIND:serial:LENGTH}}` placeholder (expanded by `expand_with_spans`)."""
    kind, floor = _KINDS[rng.choice(kinds)]
    if kind == "vendor":
        kind, floor = rng.choice(_VENDORS)
    size = 20 if kind == "aws_key" else max(length, floor)  # an AWS key id has no length of its own
    body = (template.replace("%N%", rng.choice(_SENSITIVE_NAMES)).replace("%T%", rng.choice(_STRICT_NAMES)).replace("%F%", rng.choice(_FLAG_NAMES)))
    prefix, suffix = rng.choice(("", "\n") if label.startswith("yaml") else _PROP_PREFIXES), rng.choice(_PROP_SUFFIXES)  # YAML keys start a line
    return prefix + body.replace("%S%", f"{{{{tok:{kind}:{serial}:{size}}}}}") + suffix


def _case(text: str, label: str) -> dict:
    return {"input": text} | ({"open": 1} if label == "env_unterminated" else {})


def generate_property_cases(seed: int, extra: int) -> list[dict]:
    """Every carrier at every edge length, then `extra` random (carrier, length) draws, then `extra // 4` pairs of closed carriers on one
    line. Each case is `{"input": text}` (ASCII; the secrets are `{{tok:...}}` placeholders, so the planted ranges are the expanded
    tokens: `expand_with_spans`), plus `"open": 1` for a carrier that leaves a quote open (it would pair with the next carrier's quote)."""
    rng = random.Random(seed)
    serial = itertools.count()
    cases = []
    for label, kinds, template in _CARRIERS:
        for length in _EDGE_LENGTHS:
            cases.append(_case(_plant(rng, label, kinds, template, length, next(serial)), label))
    for _ in range(extra):
        draw = rng.random()
        length = rng.randint(1, 40) if draw < 0.6 else rng.randint(41, 600) if draw < 0.9 else rng.randint(601, 5000)
        label, kinds, template = rng.choice(_CARRIERS)
        cases.append(_case(_plant(rng, label, kinds, template, length, next(serial)), label))
    closed = [c for c in _CARRIERS if c[0] != "env_unterminated"]
    for _ in range(extra // 4):
        (first_label, first_kinds, first_template), (second_label, second_kinds, second_template) = rng.choice(closed), rng.choice(closed)
        first = _plant(rng, first_label, first_kinds, first_template, rng.randint(1, 200), next(serial))
        second = _plant(rng, second_label, second_kinds, second_template, rng.randint(1, 200), next(serial))
        joiner = "\n" if second_label.startswith("yaml") else rng.choice(_PROP_JOINERS)  # YAML keys start a line
        cases.append({"input": first + joiner + second})
    return cases


def _covered(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(a <= start and end <= b for a, b in spans)  # merged spans are maximal: a covered range lies inside ONE of them


def _leaks(cases: list[dict]) -> list[tuple]:
    leaks = []
    for case in cases:
        text, secrets = expand_with_spans(case["input"])
        spans = redact_spans(text)
        for start, end in secrets:
            if not _covered(spans, start, end):
                leaks.append((text[:200], text[start:end][:80], redact_text(text)[:200]))
    return leaks


def test_a_planted_secret_of_any_length_is_covered_in_every_carrier():
    """Soundness: no byte of a planted secret (1..5000 characters: JWTs, cookies, comma lists, quoted values, glued flags, ...) survives
    in any carrier. A length cap, or a detector measuring a value another had already shortened, fails here."""
    assert _leaks(generate_property_cases(_PROPERTY_SEED, 2000) + generate_property_cases(7, 1500) + generate_property_cases(8, 1500))[:3] == []


@pytest.mark.parametrize("length", [513, 600, 4096, 100_000])
@pytest.mark.parametrize("carrier", ["Cookie: session=%s", "Set-Cookie: session=%s; Path=/", "Cookie: a=1; session=%s; theme=dark",
                                     "curl -H 'Cookie: session=%s' https://h.test", "Authorization: Token %s"])
def test_long_session_cookies_are_masked_whole(carrier, length):
    """The Greptile finding: a header value longer than the old 512-character cap kept its tail, and the JWT pass could no longer
    recognise those bytes. Every byte of the token goes, whatever its length."""
    jwt = _token("jwt", length, length)
    text = "saw " + carrier % jwt + " next"
    out = redact_text(text)
    at = 4 + carrier.index("%s")
    assert _covered(redact_spans(text), at, at + len(jwt))
    assert not any(jwt[i:i + 12] in out for i in range(0, len(jwt) - 12, 7)) and out.startswith("saw ") and REDACTED in out


def _joined_spans(parts: list[str], joiner: str) -> list[tuple[int, int]]:
    spans, offset = [], 0
    for part in parts:
        spans += [(a + offset, b + offset) for a, b in redact_spans(part)]
        offset += len(part) + len(joiner)
    return spans


def _union_groups(cases: list[dict]) -> list[tuple[list[str], str]]:
    """Index-derived (so the TypeScript twin builds the same ones) groups of three closed carriers, each in both orders; every seventh
    group is joined by a blank line instead of a newline."""
    closed = [expand_tokens(c["input"]) for c in cases if "open" not in c][:700]
    n = len(closed)
    groups = []
    for i in range(n):
        trio = [closed[i], closed[(i * 7 + 3) % n], closed[(i * 13 + 5) % n]]
        joiner = "\n\n" if i % 7 == 0 else "\n"
        groups += [(trio, joiner), (trio[::-1], joiner)]
    return groups


def test_redacting_is_invariant_under_splitting_into_carriers_in_any_order():
    """The union property: detectors only read the original text, so carriers joined by a newline neither hide nor expose each other."""
    for parts, joiner in _union_groups(json.loads(_GENERATED_PATH.read_text(encoding="utf-8"))["property"]):
        assert redact_spans(joiner.join(parts)) == _joined_spans(parts, joiner), parts
        assert redact_text(joiner.join(parts)) == joiner.join(redact_text(p) for p in parts)


# ---- seeded random strings: the parity corpus shared with the TypeScript twin ---------------------------------------------------
_RANDOM_FRAGMENTS = (
    "password", "token", "API_KEY", "client_secret", "db_password", "authToken", "SECRET", "user", "mode", "name", "id", "x", "abc", "S3cret",
    "=", ":", " = ", ": ", "  ", " ", "\t", ",", ";", "&", "\n", "\r\n", "/", "?", "#", "(", ")", "{", "}", "[", "]", "|", "@", "-", ".", "_", "!",
    '"', "'", '\\"', "\\", "--password ", "--api-key ", "--user ", "-u ", "-p", "-pass ", "-a ", "--mode ", "-v ", "Bearer ", "Basic ", "Cookie: ",
    "Set-Cookie: ", "Authorization: ", "Proxy-Authorization: ", "X-Api-Key: ", "://", "https://", "postgres://", "a.b", "k.x", "é", "日本",
    "\u00a0", "\u0085", "\u2028", "\ufeff", "\u200b",
    "{{tok:jwt:1}}", "{{tok:ghp:1}}", "{{tok:sk:1}}", "{{tok:aws_key:1}}", "{{tok:xoxb:1}}", "{{tok:github_pat:1}}", "eyJhbGciOiJIUzI1NiJ9", "ghp_", "sk-", "AKIA", "sk-aaaaaaaaaaaaaaaa", "AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----", "MIIEowIBAAKCAQEA", "mysql ", "docker login ", "sshpass ", "curl ", "redis-cli ",
    "sqlcmd ", "openssl ", "pass:", "STRIPE_KEY", "{{tok:mixed:5:33}}", "- name: ", "name: ", "value: ", "|\n  ", "  value: ",
    "DB_PASSWORD", "[REDACTED]", "1000:1000", "a:b", "user:pw@", "u:p@h", "pw", "x=1", "a=b", "--", "-",
    "`--user ", "**", "__", "~~", "<", ">", "sshpass -p ", "docker login -u a: -p ", "--cmd=", "--token ", "redis-cli -a ", "-passin ", "--user=a:", "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
)


def generate_random_strings(seed: int, count: int) -> list[str]:
    fragments = [expand_tokens(f) for f in _RANDOM_FRAGMENTS]  # the table holds placeholders, never a secret-shaped literal
    rng = _Xorshift(seed)
    out = []
    for _ in range(count):
        out.append("".join(fragments[rng.next(len(fragments))] for _ in range(2 + rng.next(11))))
    return out


def _digest(text: str) -> str:
    """8 hex digits over the redacted output and the reported spans (UTF-16 offsets: the corpus is BMP-only, so they equal Python's)."""
    spans = ";".join(f"{a},{b}" for a, b in redact_spans(text))
    return hashlib.sha256((redact_text(text) + "\u0000" + spans).encode("utf-8", "surrogatepass")).hexdigest()[:8]
