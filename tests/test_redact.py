"""Redaction: every pattern has positives and negatives; identifiers and prose are untouched; runlog.emit applies it."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from swarm import runlog
from swarm.redact import REDACTED, is_sensitive_key, redact, redact_text

# Built at runtime so no secret-shaped literal sits in the repo.
GHP = "ghp_" + "a1B2c3D4" * 5
PAT = "github_pat_" + "11ABCDEFG0" * 3
SK = "sk-" + "Zx9Qw8Er7Ty6Ui5O"
AKIA = "AKIA" + "ABCDEFGHIJ012345"
SLACK = "xoxb-" + "1234567890-abcdefghij"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
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
    return value if isinstance(value, str) else "".join(value)


_SHARED = json.loads((Path(__file__).parent / "fixtures" / "redact_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _SHARED, ids=[c["name"] for c in _SHARED])
def test_shared_vectors(case):
    """The same vectors run in omp/test/swarm_bar.test.ts against redactText: the two implementations cannot drift."""
    out = redact_text(_join(case["input"]))
    for needle in case["must_not_contain"]:
        assert _join(needle) not in out
    for needle in case["must_contain"]:
        assert _join(needle) in out


def test_megabyte_inputs_are_scanned_in_linear_time():
    import time
    for unit in ("a://b:b:b:", "--password ", "mode=fast ", "'a':'", "Authorization: Basic ", "-----BEGIN PRIVATE KEY-----", "Bearer ", "a.b-",
                 "SWARM_ED25519_KEY=", "SWARM_ED25519_KEY_FILE=", "a,", "k=a,", "--ed25519-key ", "ed25519.seed.", "Cookie: ", "Authorization:x"):
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
