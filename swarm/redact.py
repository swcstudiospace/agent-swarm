"""Secret redaction for run-log payloads and telemetry strings (stdlib only).

`redact()` returns a redacted COPY; the caller's object is never mutated. Two independent layers:
  * sensitive keys (see `is_sensitive_key`: a name token that IS or ENDS WITH token, secret, password, credential,
    apikey ... (`PGPASSWORD`, `accesstoken`), a whole-word signing/private/cookie/auth(orization)/bearer, a
    `api|access|secret|private|signing|ssh|encryption|master|auth|session|license|client|ed25519|hmac|jwt|handoff|signer`
    + `key|seed` pair (`SWARM_ED25519_KEY`), or a `<db|mysql|pg|...>_pass|pwd` pair; a name ending in `file`/`path`
    (`SWARM_ED25519_KEY_FILE`) names a location, not a secret): every string beneath such a dict key is replaced.
    Numbers, booleans and None stay, so a counter such as `tokens: 1200` survives. A bare `key`, `seed`, `sort_key`,
    `cache_key`, `author` or `authority` is not a credential.
  * text patterns inside any string: GitHub, `sk-`, AWS, Slack and Bearer tokens, `Authorization: Basic`, JWTs, PEM
    private-key blocks, `NAME=value`, `NAME: value`, `--flag value`, `"NAME": "value"` / `'NAME': 'value'` /
    `\\"NAME\\": \\"value\\"` pairs with a sensitive NAME, and URL userinfo (`scheme://user:pass@host`, the password may
    contain `@`).
A benign name never hides a later assignment (`user=alice,password=x`, `--mode a;--password x`): the scan is one forward pass
that steps past a benign separator, while a sensitive name masks its whole comma-bearing value.
Identifiers (task ids, run ids, correlation ids, `sha256:<64 hex>` digests) and ordinary prose never match. Every
quantifier that can be re-entered from many start positions is bounded, so adversarial input of hundreds of KB is
scanned in time linear in its size.
Best effort, not a guarantee: it covers the carriers above plus the command-line forms `curl -u user:pass`, `mysql -pSECRET`,
`sshpass -p`, `docker login -p`, `redis-cli -a`, `sqlcmd -P`, `-pass pass:X`; vendor tokens (GitHub, Stripe, Google, SendGrid, Slack, npm,
GitLab, DigitalOcean, AWS key ids, ...); `<vendor>_KEY=<long mixed-case value>`; and YAML `- name: X_PASSWORD` / `value:` pairs and
block scalars (`password: |`). Identifier fields are never free text and are not passed through it (see `swarm.runlog.emit`).
"""
from __future__ import annotations

import functools
import re

REDACTED = "[REDACTED]"

_SENSITIVE_TOKENS = frozenset({
    "token", "secret", "password", "passwd", "passphrase", "credential", "credentials",
    "signing", "private", "cookie", "cookies", "auth", "oauth", "authorization", "authorisation", "authentication",
    "apikey", "accesskey", "secretkey", "privatekey", "signingkey", "sshkey", "masterkey", "encryptionkey", "bearer",
})
# Plural `tokens`/`secrets`/`passwords` are deliberately absent: they are counts and category labels in this codebase
# (`tokens: 1200`, `max_tokens`, `ux_tokens`, `runs: {"secrets": "pass"}`); the singular names hold credentials. The one
# exception is a plural right after one of `_PLURAL_CONTEXT` (`SUBSTRATE_OPERATOR_TOKENS`, `api_keys`: credential lists).
# `<prefix>_key` / `<prefix>_seed` is a credential only for these prefixes (`SWARM_ED25519_KEY`, `signing_seed`); a bare
# `key`/`seed`, `sort_key`, `cache_key`, `idempotency_key`, `random_seed` ... is an identifier and stays readable.
_KEY_PREFIXES = frozenset({"api", "access", "secret", "private", "signing", "ssh", "encryption", "master", "auth", "account",
                           "session", "license", "licence", "client", "ed25519", "hmac", "jwt", "handoff", "signer"})
_KEY_WORDS = frozenset({"key", "seed"})
_PLURAL_WORDS = frozenset({"tokens", "secrets", "passwords", "keys"})
_PLURAL_CONTEXT = frozenset({"operator", "api", "access", "bearer", "auth", "session"})
# A name whose LAST token is one of these holds a path to the secret, not the secret (`SWARM_ED25519_KEY_FILE`).
_PATH_TOKENS = frozenset({"file", "path"})
# A name token that merely ENDS with one of these is a glued compound (`PGPASSWORD`, `accesstoken`, `clientsecret`,
# `sessionkey`): sensitive too. Plural forms (`tokens`, `secrets`) do not end with them and stay readable.
_SENSITIVE_SUFFIXES = tuple(sorted({
    "password", "passwd", "passphrase", "token", "secret", "credential", "credentials", "apikey", "accesskey",
    "secretkey", "privatekey", "signingkey", "sshkey", "masterkey", "encryptionkey",
} | {p + w for p in _KEY_PREFIXES for w in _KEY_WORDS}))
# `DB_PASS`, `MYSQL_PWD`: `pass`/`pwd` are credentials only right after a service/account word (a bare `PWD` is the cwd,
# `gate_pass` is a verdict).
_PASS_TOKENS = frozenset({"pass", "pwd"})
_PASS_CONTEXT = frozenset({"db", "database", "sql", "mysql", "pg", "postgres", "postgresql", "mongo", "mongodb", "redis",
                           "smtp", "imap", "ftp", "sftp", "ssh", "ldap", "amqp", "rabbitmq", "user", "admin", "root",
                           "login", "mail", "auth", "master"})
# Whole-word names too generic for the prose-prone `name: value` and `--flag value` forms; `=` and JSON pairs keep them.
_WEAK_TOKENS = frozenset({"auth", "oauth", "private", "signing", "cookie", "cookies", "authentication", "authorization",
                          "authorisation", "bearer"})
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT_RE = re.compile(r"[^A-Za-z0-9]+")
_NAME_CAP = 256  # names longer than this are not credential names (and are never scanned in full)
_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # ASCII only: `str.isalnum` is Unicode-aware, JS `[A-Za-z0-9]` is not


def _name_tokens(name: str) -> list[str]:
    """Lowercase tokens of `name`, split on non-alphanumerics and camelCase (`apiKey` -> api, key)."""
    return [t.lower() for part in _SPLIT_RE.split(name[:_NAME_CAP]) for t in _CAMEL_RE.split(part) if t]


def _sensitive(toks: list[str], *, strict: bool) -> bool:
    if len(toks) > 1 and toks[-1] in _PATH_TOKENS:
        return False
    for i, t in enumerate(toks):
        if t in _SENSITIVE_TOKENS:
            if not (strict and t in _WEAK_TOKENS):
                return True
        elif t.endswith(_SENSITIVE_SUFFIXES):
            return True
        elif i > 0:
            if t in _KEY_WORDS and toks[i - 1] in _KEY_PREFIXES:
                return True
            if t in _PASS_TOKENS and toks[i - 1] in _PASS_CONTEXT:
                return True
            if t in _PLURAL_WORDS and toks[i - 1] in _PLURAL_CONTEXT:
                return True
    return False


def is_sensitive_key(name: object) -> bool:
    """True when `name` looks like it names a credential: a token that is, or ends with, password/passwd/passphrase/
    token/secret/credential(s)/apikey/accesskey/secretkey/privatekey/... (`PGPASSWORD`, `accesstoken`, `github_token`),
    a whole-word signing/private/cookie/auth/authorization, a `key` token directly preceded by one of `_KEY_PREFIXES`
    (api_key, `ACCESS-KEY`, clientKey ...), or `pass`/`pwd` after a service word (DB_PASS, MYSQL_PWD).
    `author`/`authority` never match; neither does a bare `key`, plural `tokens`/`secrets` or `max_tokens`."""
    if not isinstance(name, str):
        return False
    return _sensitive(_name_tokens(name), strict=False)


def _strict_key(name: str) -> bool:
    """Like `is_sensitive_key` minus the generic whole words (auth, private, cookie, ...) that read as plain prose."""
    return _sensitive(_name_tokens(name), strict=True)


_PEM_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z0-9 ]{0,40}PRIVATE KEY(?: BLOCK)?-----|\Z)",
    re.DOTALL)
_JWT_RE = re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_TOKEN_RES = (
    re.compile(r"(?<![A-Za-z0-9_])(?:gh[oprsu]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])(?:xox[baprs]|xapp)-[A-Za-z0-9-]{10,}"),
    # the shapes scripts/sec_gate.py detects (Stripe, Google, GitHub) plus the other prefixed vendor tokens that appear in logs
    re.compile(r"(?<![A-Za-z0-9])(?:(?:[spr]k_live|whsec)_[A-Za-z0-9]{16,}|AIza[A-Za-z0-9_-]{30,}"
               r"|SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}|npm_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{16,}"
               r"|dop_v1_[A-Za-z0-9]{32,}|hf_[A-Za-z0-9]{30,}|dckr_pat_[A-Za-z0-9_-]{20,}|shpat_[A-Za-z0-9]{32,}"
               r"|lin_api_[A-Za-z0-9]{30,}|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{20,})"),
)
# `re.ASCII` on every pattern that uses `\s`, `\b` or IGNORECASE: Python's `\s` / `\b` / case folding are Unicode-aware, JS's (twin in
# omp/src/swarm_bar.ts, which spells the ASCII classes out) are not; a `接続postgres://u:p@h` or a NEL / U+FEFF split must behave alike.
_BEARER_RE = re.compile(r"(?<![A-Za-z0-9])([Bb]earer\s{1,16})[A-Za-z0-9._~+/=-]{16,}", re.ASCII)
# `Authorization: Basic <base64>` (and Proxy-Authorization); a bare "basic <word>" in prose is not touched.
_BASIC_RE = re.compile(r"(?<![A-Za-z0-9])((?:Proxy-)?Authorization[ \t]{0,3}[:=][ \t]{0,3}[\"']?Basic[ \t]{1,16})[A-Za-z0-9+/_-]{4,}={0,2}",
                       re.IGNORECASE | re.ASCII)
# Any other `Cookie:` / `Set-Cookie:` / `Authorization:` header (`Authorization: Token x`, `Cookie: sid=x; theme=dark`): the rest of
# the line up to a quote is the credential. Bearer/Basic keep their scheme visible (handled above).
_HEADER_RE = re.compile(r"(?<![A-Za-z0-9_-])((?:Set-|Proxy-)?(?:Cookie|Authorization)[ \t]{0,3}:(?![ \t]{0,3}(?:Bearer|Basic)\b)[ \t]{0,3})[^\s\"'][^\r\n\"']{0,511}",
                        re.IGNORECASE | re.ASCII)
# scheme://user:password@host: the user cannot contain ':' (so the split is unambiguous), the password runs to the LAST
# '@' before the first '/' or whitespace (`postgres://u:p@ss@host/db`).
_USERINFO_RE = re.compile(r"((?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://)[^/\s@:]{0,256}:[^/\s]{0,256}@", re.ASCII)
# ',' is part of an unquoted value (`TOKENS=one,two`): a credential may contain it, and a list of credentials is comma-joined.
_VALUE = r"""(\\"(?:[^"\\\n]|\\(?!"))*\\"|"[^"\n]*"|'[^'\n]*'|(?:[^\s&;"'\\]|\\(?!"))+)"""
# The second lookbehind keeps a `a.b.c.d` / `a-b-c` run from re-starting a 128-char scan at every dot or dash.
# NAME=value / NAME = value (any sensitive name) and NAME: value / NAME : value (strict names only; prose-safe). The regex
# matches only `NAME<sep>`; `_scan_name_values` then consumes the value (`_VALUE_RE`) of a SENSITIVE name and merely steps
# past the separator of a benign one, so a later assignment inside its "value" (`user=alice,password=x`) is still scanned.
_NAME_SEP_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][.-])([A-Za-z_][A-Za-z0-9_.-]{0,127})([ \t]{0,32}[=:][ \t]{0,32})")
_VALUE_RE = re.compile(_VALUE, re.ASCII)
# `--password hunter2`, `-db-password hunter2`: a sensitive flag followed by a separate value that is not another flag. Scanned
# like NAME=value: the regex matches `--flag<ws>` only, a benign flag never hides a later one (`--mode a;--password x`).
_FLAG_SEP_RE = re.compile(r"(?<![A-Za-z0-9_-])(--?[A-Za-z][A-Za-z0-9_-]{0,63})([ \t]{1,8})(?![-\s])", re.ASCII)
_FLAG_VALUE_RE = re.compile(r"""(\\"(?:[^"\\\n]|\\(?!"))*\\"|"[^"\n]*"|'[^'\n]*'|(?:[^\s"'\\]|\\(?!"))+)""", re.ASCII)
# "name": "value", 'name': 'value', "name": 'value' (JSON, Python repr, JS).
_PAIR_RE = re.compile(
    r"""((?P<q>["'])(?P<name>[^"'\\\n]{1,80})(?P=q)[ \t]{0,8}:[ \t]{0,8})(?P<v>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")
# JSON that was serialised again into a string: {\"password\": \"x\"}
_ESC_PAIR_RE = re.compile(
    r"""(\\"(?P<name>[^"'\\\n]{1,80})\\"[ \t]{0,8}:[ \t]{0,8}\\")(?:[^"\\]|\\(?!"))*(\\")""")


@functools.lru_cache(maxsize=4096)
def _name_hit(name: str, eq: bool) -> bool:
    return is_sensitive_key(name) if eq else _strict_key(name)


# A masked value can swallow the NAME of a further sensitive assignment (`password=a,token: b`, `key=a,token = b`,
# `key=a;--password b`) and stop right before that assignment's own value (a quote, or whitespace around the `:`/`=`/flag):
# the value goes on. A quote right after any other value is not taken as a continuation: it is as likely the closing quote
# of an enclosing string.
_TAIL_NAME_RE = re.compile(r"(?<![A-Za-z0-9_])(?<![A-Za-z0-9_][.-])([A-Za-z_][A-Za-z0-9_.-]{0,127})([=:]?)")
_TAIL_FLAG_RE = re.compile(r"(?<![A-Za-z0-9_-])--?[A-Za-z][A-Za-z0-9_-]{0,63}")
_AFTER_NAME_RE = re.compile(r"[ \t]{0,32}([=:])[ \t]{0,32}")
_AFTER_SEP_RE = re.compile(r"[ \t]{0,32}")
_FLAG_GAP_RE = re.compile(r"[ \t]{1,8}(?![-\s])", re.ASCII)
_TAIL_WINDOW = 200
_TAIL_DELIMS = (",", ";", "&", "/", "?")


# `<vendor>_KEY=<long mixed-case value>` (`STRIPE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`): a bare `*_key` name is too common to mask on its
# name alone (`sort_key=name`), so it is masked when the VALUE looks like a credential (>= 20 token-alphabet characters, upper and
# lower case and a digit: not a hash, uuid, path or kebab-case id) and the word before `key` is not a lookup-style qualifier.
_VENDOR_KEY_SKIP = frozenset({"cache", "sort", "idempotency", "partition", "primary", "foreign", "public", "lookup", "group", "shard",
                              "unique", "index", "hash", "object", "route", "routing", "dedupe", "dedup", "lock", "message"})
_LONG_SECRET_RE = re.compile(r"[A-Za-z0-9_+/=~.-]{20,}")
# the value of a `*_key` name inside running text: one token of at most 160 characters, optionally quoted (bounded, so a long run
# of `STRIPE_KEY=` repeats is not re-read in full at every name)
_KEY_VALUE_RE = re.compile(r"""(\\?["']?)([A-Za-z0-9_+/=~.-]{20,160})(?![A-Za-z0-9_+/=~.-])""")


@functools.lru_cache(maxsize=4096)
def _key_name(name: str) -> bool:
    toks = _name_tokens(name)
    return len(toks) > 1 and toks[-1] == "key" and toks[-2] not in _VENDOR_KEY_SKIP


def _long_secret(value: str) -> bool:
    v = value.strip("\"'\\")
    return (_LONG_SECRET_RE.fullmatch(v) is not None and any(c.isdigit() for c in v)
            and any("a" <= c <= "z" for c in v) and any("A" <= c <= "Z" for c in v))


def _credential_pair(name: object, value: object) -> bool:
    """True when `name` holds a credential: a sensitive name, or a `*_key` name with a credential-shaped string value."""
    return is_sensitive_key(name) or (isinstance(name, str) and isinstance(value, str) and _key_name(name) and _long_secret(value))


def _key_value_end(s: str, pos: int) -> int:
    """End of the credential-shaped token (and its closing quote) that starts at s[pos], or -1."""
    m = _KEY_VALUE_RE.match(s, pos)
    if m is None or not _long_secret(m.group(2)):
        return -1
    end, q = m.end(), m.group(1)
    return end + len(q) if q and s.startswith(q, end) else end


# ---- command-line credentials ---------------------------------------------------------------------------------------------
# `curl -u user:pass`, `--user user:pass`, `--proxy-user a:b` (a docker `-u 1000:1000` uid:gid pair is not a credential).
_CLI_VALUE = r"""(?:"[^"\n]*"|'[^'\n]*'|[^ \t\r\n\f\v"'\\;&|]+)"""
# explicit ASCII whitespace classes below (`\s` differs between Python and JS: the twin in omp/src/swarm_bar.ts must agree byte for byte)
_USER_PASS_FLAG_RE = re.compile(
    r"""(?<![A-Za-z0-9_-])((?:-u|--user|--proxy-user)(?:[ \t]{1,8}|=))(?![0-9]{1,10}:[0-9]{1,10}(?:[ \t\r\n\f\v"';&|]|$))"""
    r"""(?:"[^"\n:]{0,128}:[^"\n]*"|'[^'\n:]{0,128}:[^'\n]*'|[^ \t\r\n\f\v:"'\\;&|]{1,128}:[^ \t\r\n\f\v"'\\;&|]+)""")
_OPENSSL_PASS_RE = re.compile(r"""(?<![A-Za-z0-9_-])(-pass(?:in|out)?[ \t]{1,8}["']?pass:)[^ \t\r\n\f\v"';&|]+""")


def _cli_re(commands: str, flag: str, *, glued_only: bool = False) -> re.Pattern[str]:
    """`<command> ... -<flag><value>` / `-<flag> <value>` within one shell segment (at most 10 arguments after the command; the lazy
    gap finds the first such flag). `glued_only`: the value must touch the flag (`mysql -pSECRET`; a bare `-p` is a prompt)."""
    after = r"(?=[^ \t\r\n\f\v;&|])" if glued_only else r"(?:[ \t]{1,8}(?=[^ \t\r\n\f\v-])|(?=[^ \t\r\n\f\v;&|]))"
    return re.compile(rf"(?<![A-Za-z0-9_./-])((?:{commands})(?:[ \t]+[^ \t\r\n\f\v;&|]{{1,128}}){{0,10}}?[ \t]+-{flag}{after}){_CLI_VALUE}")


# (substrings that must be present for the regex to be worth running, regex)
_CLI_RES = (
    (("-u",), _USER_PASS_FLAG_RE),
    (("-pass",), _OPENSSL_PASS_RE),
    (("mysql", "mariadb"), _cli_re(r"mysql|mysqladmin|mysqldump|mysqlcheck|mysqlimport|mysqlshow|mysqlpump|mysqlsh|mariadb|mariadb-dump|mariadb-admin",
                                   "p", glued_only=True)),
    (("sshpass", "login", "mongo"), _cli_re(r"sshpass|(?:docker|podman|buildah|skopeo|crane|oras|nerdctl)[ \t]+login|helm[ \t]+registry[ \t]+login"
                                            r"|az[ \t]+login|mongo|mongosh|mongodump|mongorestore|mongoexport|mongoimport|mongostat|mongotop", "p")),
    (("redis-cli",), _cli_re("redis-cli", "a")),
    (("sqlcmd", "bcp"), _cli_re("sqlcmd|bcp", "P")),
    (("ssh-keygen",), _cli_re("ssh-keygen", "N")),
)


def _mask_cli(s: str) -> str:
    for hints, rx in _CLI_RES:
        if any(h in s for h in hints):
            s = rx.sub(lambda m: m.group(1) + REDACTED, s)
    return s


# ---- YAML: `- name: DB_PASSWORD` / `value: x` pairs (Kubernetes env) and block scalars (`password: |`) ------------------------
_YAML_NAME_RE = re.compile(
    r"""(?<![^\n])[ \t]*(?:-[ \t]+)?name[ \t]*:[ \t]*(["']?)([A-Za-z_][A-Za-z0-9_.-]{0,127})\1[ \t]*(?:#[^\r\n]*)?\r?\n([ \t]*)value[ \t]*:[ \t]*(?=[^ \t\r\n#])""")
_YAML_BLOCK_KEY_RE = re.compile(r"""(?<![^\n])([ \t]*)(?:-[ \t]+)?["']?([A-Za-z_][A-Za-z0-9_.-]{0,127})["']?[ \t]*:[ \t]*(?=[|>])""")
_YAML_INDICATOR_RE = re.compile(r"[|>][+-]?[0-9]?[+-]?[ \t]*(?:#[^\r\n]*)?(?=\r?\n|\Z)")


def _yaml_value_end(s: str, start: int, indent: int) -> int:
    """End of the scalar that begins at s[start]: a block scalar (`|`, `>-`, ...) runs through the following lines indented deeper than
    `indent` (blank lines inside it included); any other value is the rest of its line."""
    ind = _YAML_INDICATOR_RE.match(s, start)
    if ind is None:
        eol = s.find("\n", start)
        eol = len(s) if eol < 0 else eol
        return eol - 1 if s[eol - 1:eol] == "\r" else eol
    end = ind.end()
    pos = s.find("\n", end)
    while pos >= 0:
        ls = pos + 1
        le = s.find("\n", ls)
        le = len(s) if le < 0 else le
        line = s[ls:le]
        if line.strip(" \t\r"):
            if len(line) - len(line.lstrip(" \t")) <= indent:
                break
            end = le - 1 if line.endswith("\r") else le
        pos = le if le < len(s) else -1
    return end


def _mask_yaml(s: str) -> str:
    if "\n" not in s or ":" not in s:
        return s
    for rx, indent_group in ((_YAML_NAME_RE, 3), (_YAML_BLOCK_KEY_RE, 1)):
        out: list[str] = []
        last = 0
        for m in rx.finditer(s):
            if m.start() < last or not is_sensitive_key(m.group(2)):
                continue
            start = m.end()
            end = _yaml_value_end(s, start, len(m.group(indent_group)))
            if end > start:
                out.append(s[last:start])
                out.append(REDACTED)
                last = end
        if out:
            out.append(s[last:])
            s = "".join(out)
    return s


def _continuation(s: str, pos: int, end: int, value_re: re.Pattern[str]) -> int:
    """End of the next value segment that still belongs to the secret whose value spans s[pos:end], or -1."""
    c = s[end:end + 1]
    if c != " " and c != "\t" and c != '"' and c != "'" and not s.startswith('\\"', end):
        return -1
    lo = max(pos, end - _TAIL_WINDOW)
    cut = max(s.rfind(d, lo, end) for d in _TAIL_DELIMS)
    if cut >= 0:
        lo = cut + 1
    elif end - pos > _TAIL_WINDOW:
        return -1
    colon = False  # `NAME://...` is a URL, not `NAME: value` (see _scan)
    if (a := _TAIL_NAME_RE.fullmatch(s, lo, end)) is not None:
        if a.group(2):
            if not _name_hit(a.group(1), a.group(2) == "="):
                return -1
            gap, colon = _AFTER_SEP_RE.match(s, end), a.group(2) == ":"
        else:
            gap = _AFTER_NAME_RE.match(s, end)
            if gap is None or not _name_hit(a.group(1), gap.group(1) == "="):
                return -1
            colon = gap.group(1) == ":"
    elif _TAIL_FLAG_RE.fullmatch(s, lo, end) is not None and _name_hit(s[lo:end], False):
        gap = _FLAG_GAP_RE.match(s, end)
        if gap is None:
            return -1
    else:
        return -1
    if colon and s.startswith("//", gap.end()):
        return -1
    v = value_re.match(s, gap.end())
    return -1 if v is None else v.end()


def _scan(s: str, sep_re: re.Pattern[str], value_re: re.Pattern[str], eq_sep: bool) -> str:
    """Mask the value that follows every sensitive `NAME<sep>` match of `sep_re`: one forward pass with a cursor (no
    recursion, no rescans). A benign name only moves the cursor past its separator, so what looks like its value is scanned too."""
    out: list[str] = []
    pos = last = 0
    while (m := sep_re.search(s, pos)) is not None:
        pos = m.end()
        if eq_sep and "=" not in m.group(2) and s.startswith("//", pos):
            continue  # `auth://host`, `token://x`: a URL scheme, not `NAME: value` (`PASSWORD=//x` is still a secret)
        name = m.group(1)
        hit = _name_hit(name, eq_sep and "=" in m.group(2))
        if not hit and not _key_name(name):
            continue
        if hit:
            if (v := value_re.match(s, pos)) is None:
                continue
            end = v.end()
        elif (end := _key_value_end(s, pos)) < 0:
            continue
        if s.startswith(REDACTED, pos) and end > pos + len(REDACTED) and s[pos + len(REDACTED)] not in _ALNUM:
            end = pos + len(REDACTED)  # a value an earlier pass already masked (`[REDACTED],`): idempotent, the comma stays
        while (more := _continuation(s, pos, end, value_re)) >= 0:
            end = more
        out.append(s[last:pos])
        out.append(REDACTED)
        last = pos = end
    if not out:
        return s
    out.append(s[last:])
    return "".join(out)


def _pair(m: re.Match[str]) -> str:
    v = m.group("v")
    if not _credential_pair(m.group("name"), v[1:-1]):
        return m.group(0)
    return f"{m.group(1)}{v[0]}{REDACTED}{v[0]}"


def _esc_pair(m: re.Match[str]) -> str:
    return f"{m.group(1)}{REDACTED}{m.group(3)}" if is_sensitive_key(m.group("name")) else m.group(0)


def redact_text(s: str) -> str:
    """Replace every secret-looking span of `s` with `[REDACTED]`; everything else is returned unchanged."""
    if not isinstance(s, str) or len(s) < 6:
        return s
    s = _PEM_RE.sub(REDACTED, s)
    s = _JWT_RE.sub(REDACTED, s)
    for rx in _TOKEN_RES:
        s = rx.sub(REDACTED, s)
    s = _BEARER_RE.sub(lambda m: m.group(1) + REDACTED, s)
    s = _BASIC_RE.sub(lambda m: m.group(1) + REDACTED, s)
    s = _USERINFO_RE.sub(lambda m: f"{m.group(1)}{REDACTED}@", s)
    s = _HEADER_RE.sub(lambda m: m.group(1) + REDACTED, s)
    s = _mask_yaml(_mask_cli(s))
    s = _scan(s, _NAME_SEP_RE, _VALUE_RE, True)
    s = _scan(s, _FLAG_SEP_RE, _FLAG_VALUE_RE, False)
    s = _PAIR_RE.sub(_pair, s)
    return _ESC_PAIR_RE.sub(_esc_pair, s)


def _walk(value, level: int, max_depth: int, sensitive: bool):
    if isinstance(value, str):
        return REDACTED if sensitive and value else redact_text(value)
    if isinstance(value, (dict, list, tuple)):
        if level > max_depth:
            return REDACTED  # depth cap: never recurse unbounded (cycles included), never pass an unexamined subtree
        if isinstance(value, dict):
            return {k: _walk(v, level + 1, max_depth, sensitive or _credential_pair(k, v)) for k, v in value.items()}
        return [_walk(v, level + 1, max_depth, sensitive) for v in value]
    return value


def redact(value, *, max_depth: int = 12):
    """A redacted copy of `value` (dict/list/tuple/str recursed, tuples become lists, other scalars unchanged).
    Containers nested more than `max_depth` levels deep are replaced by `[REDACTED]`."""
    return _walk(value, 1, max_depth, False)
