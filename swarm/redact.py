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
Identifiers (task ids, run ids, correlation ids, `sha256:<64 hex>` digests) and ordinary prose never match.

How a string is redacted (the design that keeps passes from interacting): EVERY detector (names, flags, headers, JSON/YAML pairs, URL
userinfo, PEM, token shapes, command lines) reads the ORIGINAL text and only reports `(start, end)` spans of what must disappear;
`redact_spans` unions overlapping or touching spans and `redact_text` replaces each union by ONE `[REDACTED]`. Nothing is rewritten
while detecting, so no detector ever measures a value another detector already shortened, the order of detectors is irrelevant, and a
secret hidden by any one of them is hidden. A detector that needs a boundary scans linearly to the TRUE end of the value: a value is
never cut by a length cap (a cap would leave a readable tail). Only syntax is bounded (a name <= 127 characters, a gap <= 32 blanks);
command lines are read word by word (see "command-line credentials" below), so no argument is ever skipped by length or count. Every
quantifier that can be re-entered from many start positions is bounded, or its failed scans are remembered, so adversarial input of
megabytes is scanned in time linear in its size.
Masking is idempotent on every shared vector; a mask can newly expose a word boundary (`AKIA...--password x`) and let a second pass hide
more, never less.
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
# the line is the credential (Bearer/Basic keep their scheme visible, see above). The regex matches the lead only; `_header_end`
# scans the value to its TRUE end (line end, or an enclosing quote that does not open a quoted cookie value): never a length cap,
# a cap would leave the tail of a long session token readable.
_HEADER_RE = re.compile(r"(?<![A-Za-z0-9_-])((?:Set-|Proxy-)?(?:Cookie|Authorization)[ \t]*:(?![ \t]*(?:Bearer|Basic)\b)[ \t]*)(?=[^\s\"'])",
                        re.IGNORECASE | re.ASCII)
_HEADER_STOP_RE = re.compile(r"[\r\n\"']")
_HEADER_CLOSE_RES = {'"': re.compile(r"[\r\n\"]"), "'": re.compile(r"[\r\n']")}
# scheme://user:password@host: the user cannot contain ':' (so the split is unambiguous), the password runs to the LAST
# '@' before the first '/' or whitespace (`postgres://u:p@ss@host/db`). Neither part is length-capped: a scan stops at the next
# '/' (every `://` ends the previous scan), so the scans of different schemes never overlap.
_USERINFO_RE = re.compile(r"((?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]{0,31}://)[^/\s@:]*:[^/\s]*@", re.ASCII)
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
_DIGIT_RE, _LOWER_RE, _UPPER_RE = re.compile("[0-9]"), re.compile("[a-z]"), re.compile("[A-Z]")
# the value of a `*_key` name inside running text: the WHOLE token (never length-capped: a cap leaves the tail readable), optionally
# quoted. `_scan` remembers where a failed check ended, so a long run of `STRIPE_KEY=` repeats is not re-read at every name.
_KEY_VALUE_RE = re.compile(r"""(\\?["']?)([A-Za-z0-9_+/=~.-]{20,})""")


@functools.lru_cache(maxsize=4096)
def _key_name(name: str) -> bool:
    toks = _name_tokens(name)
    return len(toks) > 1 and toks[-1] == "key" and toks[-2] not in _VENDOR_KEY_SKIP


def _long_secret(value: str) -> bool:
    v = value.strip("\"'\\")
    return (_LONG_SECRET_RE.fullmatch(v) is not None and _DIGIT_RE.search(v) is not None
            and _LOWER_RE.search(v) is not None and _UPPER_RE.search(v) is not None)


def _credential_pair(name: object, value: object) -> bool:
    """True when `name` holds a credential: a sensitive name, or a `*_key` name with a credential-shaped string value."""
    return is_sensitive_key(name) or (isinstance(name, str) and isinstance(value, str) and _key_name(name) and _long_secret(value))


def _key_value_end(s: str, pos: int) -> tuple[int, int]:
    """(end of the credential-shaped token and its closing quote that starts at s[pos], or -1; end of the token run that was checked,
    or 0 when there is no run). A failed check fails for every later start inside the same run: a suffix has no more classes."""
    m = _KEY_VALUE_RE.match(s, pos)
    if m is None:
        return -1, 0
    if not _long_secret(m.group(2)):
        return -1, m.end()
    end, q = m.end(), m.group(1)
    return (end + len(q) if q and s.startswith(q, end) else end), m.end()


# ---- command-line credentials ---------------------------------------------------------------------------------------------
# `curl -u user:pass`, `--user user:pass`, `--proxy-user a:b` (a docker `-u 1000:1000` uid:gid pair is not a credential), `mysql -pSECRET`,
# `docker login -p`, `sshpass -p`, `redis-cli -a`, `sqlcmd -P`, `ssh-keygen -N`, `openssl -passin pass:X`.
# A command line is read WORD by word, never through a regex that has to SKIP arguments: a regex needs a bound on the length and the
# number of the arguments it skips, and an argument above the bound (`docker login -u admin:<300 characters> -p pw`) hid the password after
# it. A word ends at a blank (` \t\r\n\f\v`) or at `;&|`; a segment (the arguments of one command) ends at a newline or at `;&|`. An option
# is a word that starts with `-` after any run of delimiters (backtick, quote, `(`, `[`, `{`, `<`, Markdown emphasis ...); its value is the
# next word (or the `=value` / `-pVALUE` attached form), taken whole: a quoted value to its closing quote, an unterminated one to the end
# of its line, any other to the next blank, `;&|` or quote. Nothing is capped, nothing reads text another detector rewrote, and every
# scan moves forward only: a value that failed a check is remembered (`run`, `colon` below), so `x=-u=-u=-u=...` stays linear.
_CLI_BREAKS = "\r\n\f\v;&|"
_CLI_STOPS = " \t" + _CLI_BREAKS
_CLI_WORD_RE = re.compile(r"[^ \t\r\n\f\v;&|]+")
_CLI_BLANKS_RE = re.compile(r"[ \t]*")
_CLI_UNQUOTED_RE = re.compile(r"""[^ \t\r\n\f\v;&|"']+""")
_CLI_LINE_RE = re.compile(r"[^\r\n]*")
_CLI_QUOTED_RES = {'"': re.compile(r'"[^"\n]*"'), "'": re.compile(r"'[^'\n]*'"),
                   '\\"': re.compile(r'\\"[^\n]*?\\"'), "\\'": re.compile(r"\\'[^\n]*?\\'")}
_CLI_UID_GID_RE = re.compile(r"[0-9]{1,10}:[0-9]{1,10}")
# `-u`/`--user`/`--proxy-user` value, and the openssl `-pass`/`-passin`/`-passout` pass:X form; the option may follow ANY non-name character
# (a backtick, a quote, a bracket, `=`, `_` of Markdown emphasis ...), not a letter, digit or `-`.
_CLI_OPTION_RE = re.compile(r"(?<![A-Za-z0-9-])(?:(?:--user|--proxy-user|-u)(=|(?=[ \t]))|(-pass(?:in|out)?)(?=[ \t]))")
_CLI_CORE_RE = re.compile(r"[A-Za-z0-9_./-]+")
_CLI_NOT_LEAD = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-")
_CLI_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")  # no `_`: no command name has one, `__mysql` is emphasis
_CLI_CLOSERS = frozenset("`\"')]}>*_.,;:")  # Markdown/prose closers: a word `-p` followed by only these is a flag in prose, not `-pPASSWORD`
# the commands whose flags are checked (kind bits), the first words of a two-word command, the flag letter of each kind
_CLI_MYSQL, _CLI_LOGIN, _CLI_REDIS, _CLI_SQLCMD, _CLI_KEYGEN = 1, 2, 4, 8, 16
_CLI_COMMANDS = {
    **dict.fromkeys(("mysql", "mysqladmin", "mysqldump", "mysqlcheck", "mysqlimport", "mysqlshow", "mysqlpump", "mysqlsh", "mariadb",
                     "mariadb-dump", "mariadb-admin"), _CLI_MYSQL),
    **dict.fromkeys(("sshpass", "mongo", "mongosh", "mongodump", "mongorestore", "mongoexport", "mongoimport", "mongostat", "mongotop"), _CLI_LOGIN),
    "redis-cli": _CLI_REDIS, "sqlcmd": _CLI_SQLCMD, "bcp": _CLI_SQLCMD, "ssh-keygen": _CLI_KEYGEN}
_CLI_TWO_WORD = {**dict.fromkeys(("docker", "podman", "buildah", "skopeo", "crane", "oras", "nerdctl", "az"), 1), "helm": 2}  # 1: `login`; 2: `registry login`
_CLI_FLAG_KINDS = {"p": _CLI_MYSQL | _CLI_LOGIN, "a": _CLI_REDIS, "P": _CLI_SQLCMD, "N": _CLI_KEYGEN}
_CLI_COMMAND_HINTS = ("mysql", "mariadb", "sshpass", "login", "mongo", "redis-cli", "sqlcmd", "bcp", "ssh-keygen")


def _cli_attached(s: str, start: int, at: int, end: int) -> bool:
    """Whether the flag at s[at:at + 2] (`-p`) of the word s[start:end] carries its value attached: `-pVALUE`, `-p'VALUE'`.
    A quote that opens the value (one the word's own prefix did not open: `"-p"` is a closer) makes everything after it the value, even when it is
    only punctuation; an unquoted value is one unless it is made only of closers (`-p`, `(-p)`, `**-p**`) that end the word."""
    v = at + 2
    if v >= end:
        return False
    c = s[v]
    quote = c if c in ('"', "'") else s[v:v + 2] if c == "\\" and s[v + 1:v + 2] in ('"', "'") else ""
    if quote and quote[-1] not in s[start:at]:
        return end - v > len(quote) or s[end:end + 1] in (";", "&", "|")  # a lone quote opens a value only where `;&|` ended the word (`-p"&x"`): `"mysql -p" ok`
    return any(ch not in _CLI_CLOSERS for ch in s[v:end])


def _cli_value_end(s: str, start: int) -> int:
    """End of the value that starts at s[start] (not a blank, not `;&|`): a quoted value runs to its closing quote, or to the end of the
    line when it never closes (a truncated log line); any other value to the next blank, `;&|` or quote. `start` when it is empty."""
    c = s[start]
    if c == "\\" and s[start + 1:start + 2] in ('"', "'"):
        c = s[start:start + 2]
    rx = _CLI_QUOTED_RES.get(c)
    if rx is not None:
        m = rx.match(s, start)
        return m.end() if m else _CLI_LINE_RE.match(s, start).end()
    m = _CLI_UNQUOTED_RE.match(s, start)
    return m.end() if m else start


def _cli_option_spans(s: str, add) -> None:
    """The values of `-u user:pass` (and `--user`, `--proxy-user`, also `=user:pass`) and of `-passin pass:X`."""
    if "-u" not in s and "-pass" not in s:
        return
    n = len(s)
    run_from = run_to = -1  # [run_from, run_to) holds no blank, `;&|` or quote: the unquoted run every start inside it ends at
    colon_from, colon_at = 0, -1  # no ':' in [colon_from, colon_at), a ':' at colon_at (n when there is none)
    pushed_end = -1  # end of the last unquoted value reported: a later start (starts only grow) inside that run is covered
    for m in _CLI_OPTION_RE.finditer(s):
        start = m.end() if m.group(1) == "=" else _CLI_BLANKS_RE.match(s, m.end()).end()
        if start < pushed_end or start >= n or s[start] in _CLI_STOPS:
            continue  # (a start inside a reported run is covered)
        if m.group(2) is not None:  # openssl: `-passin pass:SECRET`, `-passin "pass:SECRET"`
            q = start + 1 if s[start] in "\"'" else start
            if s.startswith("pass:", q):
                value = _CLI_UNQUOTED_RE.match(s, q + 5)
                if value:
                    add((q + 5, value.end()))
            continue
        c = s[start]
        if c in "\"'" or (c == "\\" and s[start + 1:start + 2] in ('"', "'")):
            end = _cli_value_end(s, start)
            if s.find(":", start + 1, end) >= 0:
                add((start, end))
            continue
        if not run_from <= start < run_to:
            run_from, run_to = start, _CLI_UNQUOTED_RE.match(s, start).end()
        if not colon_from <= start <= colon_at:
            colon_from = start
            colon_at = s.find(":", start)
            if colon_at < 0:
                colon_at = n
        # a user, `:`, a password (both non-empty); a docker uid:gid pair is not a credential
        if start < colon_at and colon_at + 1 < run_to and not (colon_at - start <= 10 and run_to - colon_at <= 11
                                                               and _CLI_UID_GID_RE.fullmatch(s, start, run_to)):
            add((start, run_to))
            pushed_end = run_to


def _cli_command_spans(s: str, add) -> None:
    """The password flag of `mysql -pSECRET`, `docker login -p SECRET`, `sshpass -p`, `redis-cli -a`, `sqlcmd -P`, `ssh-keygen -N`: once
    a command is seen in a segment its flag is checked in EVERY later word of that segment, however long or numerous the arguments."""
    if not any(h in s for h in _CLI_COMMAND_HINTS):
        return
    active = pending = want = prev_end = 0  # command kinds seen in this segment; `login` expected; a flag's value expected
    for m in _CLI_WORD_RE.finditer(s):
        start, end = m.span()
        if (start != prev_end + 1 or s[prev_end] not in " \t") and s[prev_end:start].strip(" \t"):
            active = pending = want = 0  # a newline or `;&|`: the next command
        prev_end = end
        if want:  # the value is taken whole, and its words are still read: a value can hold the next command (`redis-cli -a $(sshpass -p x)`)
            want = 0
            if s[start] != "-":
                value_end = _cli_value_end(s, start)
                if value_end > start:
                    add((start, value_end))
        at = start
        while at < end and s[at] not in _CLI_NOT_LEAD:  # `, ", (, [, <, ** ... may precede an option
            at += 1
        if at < end and s[at] == "-":
            pending = 0
            kinds = active & _CLI_FLAG_KINDS.get(s[at + 1:at + 2], 0) if active else 0
            if kinds:
                if _cli_attached(s, start, at, end):  # `-pSECRET`: the value touches the flag
                    add((at + 2, _cli_value_end(s, at + 2)))
                    continue
                if kinds != _CLI_MYSQL:  # a bare `-p` is a prompt for mysql, not for the others: the value is the next word
                    want = 1
        if pending:
            core = _CLI_CORE_RE.search(s, start, end) if end - start <= 24 else None
            if pending == 2:
                pending = 3 if core and core[0] == "registry" else 0
            else:
                if core and core[0] == "login":
                    active |= _CLI_LOGIN
                pending = 0
        base_start = end
        floor = max(start, end - 41)
        while base_start > floor and s[base_start - 1] in _CLI_NAME_CHARS:
            base_start -= 1
        if base_start < end and end - base_start <= 40:  # the command name is the last run of name characters (`/usr/bin/mysql`, `$(mysql`)
            base = s[base_start:end]
            kind = _CLI_COMMANDS.get(base)
            if kind:
                active |= kind
            elif base in _CLI_TWO_WORD:
                pending = _CLI_TWO_WORD[base]


def _cli_spans(s: str, add) -> None:
    _cli_option_spans(s, add)
    _cli_command_spans(s, add)


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


def _yaml_spans(s: str, add) -> None:
    if "\n" not in s or ":" not in s:
        return
    for rx, indent_group in ((_YAML_NAME_RE, 3), (_YAML_BLOCK_KEY_RE, 1)):
        last = 0
        for m in rx.finditer(s):
            if m.start() < last or not is_sensitive_key(m.group(2)):
                continue
            start = m.end()
            end = _yaml_value_end(s, start, len(m.group(indent_group)))
            if end > start:
                add((start, end))
                last = end


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


_NAME_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")


def _tail_name_start(s: str, floor: int, end: int) -> int:
    """Where the name (and its `=`/`:`) that a value may have swallowed at its end starts: at most 130 characters back, never before `floor`."""
    i = end
    if i > floor and s[i - 1] in "=:":
        i -= 1
    low = max(floor, i - 130)
    while i > low and s[i - 1] in _NAME_CHARS:
        i -= 1
    return i


def _open_quote_value(s: str, pos: int, value_re: re.Pattern[str]) -> re.Match[str] | None:
    """The value of a name whose opening quote never closes on its line (`password="abc def`, a truncated log line): the quote is
    noise and the bare value after it is the secret. None when `s[pos]` is no quote."""
    skip = 2 if s.startswith('\\"', pos) else 1 if s[pos:pos + 1] in ('"', "'") else 0
    return value_re.match(s, pos + skip) if skip else None


def _scan(s: str, sep_re: re.Pattern[str], value_re: re.Pattern[str], eq_sep: bool, add) -> None:
    """Report the value that follows every sensitive `NAME<sep>` match of `sep_re`: one forward pass with a cursor (no
    recursion, no rescans). A benign name only moves the cursor past its separator, so what looks like its value is scanned too."""
    pos = key_fail = 0
    while (m := sep_re.search(s, pos)) is not None:
        pos = m.end()
        if eq_sep and "=" not in m.group(2) and s.startswith("//", pos):
            continue  # `auth://host`, `token://x`: a URL scheme, not `NAME: value` (`PASSWORD=//x` is still a secret)
        name = m.group(1)
        hit = _name_hit(name, eq_sep and "=" in m.group(2))
        if not hit and not _key_name(name):
            continue
        start = pos
        reopen = False
        if hit:
            if (v := value_re.match(s, pos)) is None:
                if (v := _open_quote_value(s, pos, value_re)) is None:
                    continue
                reopen = True
            start, end = v.start(), v.end()
            if s.startswith(REDACTED, start) and end > start + len(REDACTED) and s[start + len(REDACTED)] not in _ALNUM:
                end = start + len(REDACTED)  # an already-masked value (`[REDACTED],x`) ends where the mask does, like a closing quote
        else:
            if pos < key_fail:
                continue  # inside a token run whose tail was already found not to look like a credential
            end, run_end = _key_value_end(s, pos)
            if end < 0:
                key_fail = max(key_fail, run_end)
                continue
        while (more := _continuation(s, pos, end, value_re)) >= 0:
            end = more
        add((start, end))
        # the value of a quote that never closes is a guess (`--token "--password x`): scanning goes on INSIDE it. A value can also end
        # with the NAME of the next assignment (`--api-key = {--password=\"x\"`): scanning resumes where that name starts
        pos = start if reopen else _tail_name_start(s, pos, end)


def _pair_spans(s: str, add) -> None:
    for m in _PAIR_RE.finditer(s):
        v = m.group("v")
        if len(v) > 2 and _credential_pair(m.group("name"), v[1:-1]):
            add((m.start("v") + 1, m.end("v") - 1))
    for m in _ESC_PAIR_RE.finditer(s):
        if m.end(1) < m.start(3) and is_sensitive_key(m.group("name")):
            add((m.end(1), m.start(3)))


def _header_end(s: str, value_start: int) -> int:
    """End of the header value that begins at `value_start` (the first non-blank, non-quote character): the end of the line, or an
    enclosing quote. A quote right after `=` opens a quoted cookie value (`session="..."`) and is skipped to its closing quote; a
    quote that never closes on the line ends the value. Each character is read once."""
    i = value_start + 1
    while (m := _HEADER_STOP_RE.search(s, i)) is not None:
        j = m.start()
        c = s[j]
        if c == "\r" or c == "\n" or s[j - 1] != "=":
            return j
        close = _HEADER_CLOSE_RES[c].search(s, j + 1)
        if close is None or s[close.start()] != c:
            return j
        i = close.end()
    return len(s)


def _header_spans(s: str, add) -> None:
    pos = 0
    while (m := _HEADER_RE.search(s, pos)) is not None:
        pos = _header_end(s, m.end())
        add((m.end(), pos))


def _spans(s: str) -> list[tuple[int, int]]:
    """Every `(start, end)` of `s` that must disappear. EVERY detector reads the original text and only reports: nothing is rewritten
    while detecting, so no detector can measure a value another detector has already shortened, and the union below does not depend
    on the order in which they run."""
    spans: list[tuple[int, int]] = []
    add = spans.append
    for m in _PEM_RE.finditer(s):
        add((m.start(), m.end()))
    for rx in (_BEARER_RE, _BASIC_RE):
        for m in rx.finditer(s):
            add((m.end(1), m.end()))
    for m in _USERINFO_RE.finditer(s):
        add((m.end(1), m.end() - 1))  # the '@' stays
    _header_spans(s, add)
    _cli_spans(s, add)
    _yaml_spans(s, add)
    _scan(s, _NAME_SEP_RE, _VALUE_RE, True, add)
    _scan(s, _FLAG_SEP_RE, _FLAG_VALUE_RE, False, add)
    for rx in (_JWT_RE, *_TOKEN_RES):
        for m in rx.finditer(s):
            add((m.start(), m.end()))
    _pair_spans(s, add)
    return spans


def redact_spans(s: str) -> list[tuple[int, int]]:
    """The disjoint, ascending `(start, end)` ranges of `s` that `redact_text` replaces: the union of every detector's spans, where
    overlapping or touching spans become one range."""
    if not isinstance(s, str) or len(s) < 6:
        return []
    spans = _spans(s)
    if not spans:
        return []
    spans.sort()
    merged: list[tuple[int, int]] = []
    cur_start, cur_end = spans[0]
    for start, end in spans:
        if start > cur_end:
            merged.append((cur_start, cur_end))
            cur_start, cur_end = start, end
        elif end > cur_end:
            cur_end = end
    merged.append((cur_start, cur_end))
    return merged


def redact_text(s: str) -> str:
    """Replace every secret-looking span of `s` with `[REDACTED]`; everything else is returned unchanged. Overlapping or touching
    spans of different detectors become ONE `[REDACTED]`."""
    merged = redact_spans(s)
    if not merged:
        return s
    out: list[str] = []
    last = 0
    for start, end in merged:
        out.append(s[last:start])
        out.append(REDACTED)
        last = end
    out.append(s[last:])
    return "".join(out)


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
