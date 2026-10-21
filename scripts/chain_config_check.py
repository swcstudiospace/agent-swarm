#!/usr/bin/env python3
"""A28 - validate chain and RPC configuration files: https endpoints, no credentials embedded in URLs, chain-id consistency and an explicit mainnet opt-in.

Scans JSON, TOML, YAML (only when PyYAML imports) and .env files under --root/--path for chain configuration: RPC URLs
(keys that contain rpc, endpoint, websocket, nodeurl or providerurl) and chain ids (chainId, chain_id, EIP-3085 hex or
decimal text). It reports: userinfo credentials and API keys in URLs (blocker), plaintext http or ws to a non-loopback
host (major), a mainnet-grade or unknown chain id without an explicit opt-in key such as allowMainnet (major), a chain id
that contradicts the network name or the endpoint host (blocker or major) and one network name mapped to two chain ids
(major). A network name denotes the chain ids of its family ("base" denotes 8453 and, as a leading word, 84532): a leg
whose chain id is outside that family is a blocker, while a name that matches no leg of a bridge labels the bridge and
gives no finding. Keys with a direction word (source, destination, ...) are legs: each one's chain id and endpoint host
are compared within its direction, two ids of one direction that disagree are a major conflict, and a test-network and a
mainnet id in one object are a minor note that does not fail the scan. A name in an object with a chain id of its own is judged
by that id, and an ancestor leg does not silence it. Directional rule: a directional name (source_network, destinationChainName,
...) whose network has a known family is a blocker when no chain id in its own direction matches that family, its own object's
ids serving when it has no same-direction id; a directional name with no known family is silent. A name's findings are counted,
not listed one per offending leg: at most five locations per name, most severe kind first, and when a kind has more offenders
the finding states how many there are in all. The verdict is the same as one finding per leg would give.
Endpoint-label rule, decided by the key that holds the entry: an entry under an endpoint container (providers, nodes, rpc,
rpcs, rpcUrl, rpcUrls, endpoints, urls) is a label whatever it carries, and its own chain id, if any, is judged as usual. Under
any other key, a name with a URL sibling and no chain id is a label only when it is an RPC-provider brand (Alchemy, Infura,
Public Node, Ankr, QuickNode); otherwise it is a network name. A label is descriptive: at most a minor note when it names a
known network that differs from its leg's chain id, never recorded as a network name. The one blocker a label can carry is a
test-network name on a mainnet or unknown chain id, checked against the object's own id or else the enclosing leg. A label
under metadata (below) is at most a minor note, a test-network name included, except under an endpoint container or under a holding
key that names chains or networks (a holding key containing chain or network, such as blockchain, subchains, networkName, or the whole
word leg with a plural: assetChains, networks, networkChains, legs, sourceLegs), which keep the blocker (ND8-5). Holding keys for
token words, leg and the other container words are read by whole word, never by substring: legal, legacy and tokenHolder are not
chain holders. The product name is chainlink in its exact spellings chainlink, Chainlink and CHAINLINK, removed wherever it occurs, so chainlinkFeeds,
ethChainlinkFeeds and feedsChainlink name no chain, and a chain word beside it still counts (chainlinkChains, chainlinkchain). It is not removed when a chain
compound prefix (block, cross, multi, side, sub, para, meta, poly, shard) comes right before it. Where the product name is spelled Chainlink or CHAINLINK, or
follows a separator, the prefix is read as a suffix of the text before it (less trailing separators) that starts at a camel word start (a capital) or
outside a lowercase word: multiChainlink, XMultiChainlink, xMultiChainlink, ETHSubChainlinkFeeds and XBlockChainlink keep their chain word, while
outsideChainlink, tradesideChainlink, xsubChainlink and Outside_Chainlink do not. An all-lower-case product name (crosschainlink) must end the text before
it with a prefix, which fails closed. ChainLink, chainLink, chain-link and chain_link keep their chain word too (R4, ND14-1, ND14-2, ND14-3, ND15-1, ND15-2).
An object with a URL-shaped key and no chain id of its own is an endpoint entry, unless it carries address, symbol or decimals keys under a token word;
then it is metadata (R5, ND12-2). The document root is not an endpoint entry (ND13-3).
Metadata rule: an object with its own chain id is metadata only as a token: a token-word holding
key (token, currency, contract, coin, erc20, with plurals; asset and assets too, unless the holding key declares a network, ND13-2) over an object with
address, symbol or decimals keys (ND8-4, R2). For a record
without a chain id, asset is not a token word: assetChains names chains (R3). The root object is never metadata (R1).
Otherwise an object with a chain id of its own is never metadata. Without one, an object is metadata when its holding key has a
token word (l2Token, wrappedToken, tokenBridge), or when it has address, symbol or decimals keys (the root excepted, R1). A metadata object's names are
labels, never network names. Network-name rule: a name in an object with no chain id of its own is a network name only under a
network holder (networks, network, chains, chain, legs, supportedChains, bridgeChains, sources, destinations), under a directional
holding key read in any spelling (destinationNetwork, destination_network, destinationLegs), under a directional name key, under a holding key that
declares a chain or network (blockchainLink, crossChainLink: checked against the chain ids in scope, ND14-4), or at the document root; under any other
holding key (an unknown holder included) it is a label, at most a minor note. URL
values, network names that could hold a secret and odd config keys are never printed: findings carry the file and a key path
whose segments are plain names. A value made only of environment-variable templates (${KEY}, $KEY, {{KEY}}, <KEY>, %KEY%) is a
reference, not a credential.
Read-only: nothing is written. Stdlib only; it skips files over 1 MiB, binaries and symlinks, stops after 2000 candidate
files (a minor scan-truncated finding says so) and never follows a symlink out of the root; one document is read for at most
200000 nodes (a YAML alias bomb is cut off and reported). At most 500 findings are listed, most severe first, and the ones
that do not fit still count in `counts` and in the status. PyYAML missing is reported as skipped:tool-missing for the YAML
check; JSON, TOML and .env are handled natively. `--dry-run` prints deterministic canned output and touches nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from collections import namedtuple
import heapq
from itertools import islice
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from swarm.errors import ErrorCode, SwarmError  # noqa: E402
from swarm.scan_status import incomplete_findings, scan_status  # noqa: E402
from swarm.script_base import AgentScript  # noqa: E402
from swarm.tools.a28_tools import CHAINS, MAX_CHAIN_ID, SEVERITY_RANK, TESTNET_NAME_TOKENS, TESTNET_NAME_WORDS, _secret_like  # noqa: E402

MAX_FILE_BYTES = 1024 * 1024
MAX_CANDIDATES = 2000
MAX_VISITED = 50000
MAX_FINDINGS = 500  # listed; up to MAX_STORED are collected so the most severe are the ones listed
MAX_STORED = 20000
MAX_DEPTH = 30
MAX_NODES = 200_000  # per document: a YAML alias bomb is a small file that expands into a huge shared graph
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".swarm", ".hg", ".svn"}
SKIP_FILES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock"}
SUFFIX_KIND = {".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".env": "env"}
NAME_KEYS = {"name", "network", "chain", "chainname", "networkname"}
# words that give a chain-id key a direction (source and destination of a bridge, the two layers): such keys form a pair, not a conflict
DIRECTION_WORDS = frozenset({"source", "destination", "src", "dst", "from", "to", "origin", "target", "l1", "l2", "parent", "child"})
OPT_IN_KEYS = {"allowmainnet", "mainnetoptin", "mainnetapproved", "enablemainnet", "mainnetenabled"}
# keys that hold endpoint entries (normalised): an entry under one is an endpoint label whatever it carries
ENDPOINT_CONTAINERS = {"providers", "nodes", "rpc", "rpcs", "rpcurl", "rpcurls", "endpoints", "urls"}
# token words, matched by whole word with plurals allowed (tokens, coins): a holding key with one of these holds token metadata
# (l2Token, wrappedToken, coin, erc20). For a record without a chain id of its own, asset is not one of them: assetChains names chains (R3)
TOKEN_WORDS = frozenset({"token", "currency", "currencies", "contract", "coin", "erc20"})
# asset words: a token word for a record with its own chain id, only when its holder declares no network (chain, network or leg word) (R2, ND12-3, ND13-2)
ASSET_WORDS = frozenset({"asset"})
# substrings of the normalised key; the product name chainlink (exact spellings) is removed first, unless a chain compound prefix comes before it
# (R4, ND14-1, ND14-2, see _names_chain). leg matches by whole word only
CHAIN_NETWORK_SUBSTRINGS = ("chain", "network")
LEG_WORDS = frozenset({"leg"})
METADATA_KEYS = {"address", "symbol", "decimals"}
# RPC-provider brands (normalised with _norm_name): a brand beside a URL with no chain id is a label, not a network name
RPC_BRANDS = {"alchemy", "infura", "public-node", "ankr", "quicknode"}
# holding keys that declare networks: a name inside an object with no chain id of its own is a network name under one of these
NETWORK_HOLDERS = {"networks", "network", "chains", "chain", "legs", "supportedchains", "bridgechains", "sources", "destinations"}
URL_KEY_PARTS = ("rpc", "endpoint", "websocket", "nodeurl", "providerurl")
NOT_RPC_URL_PARTS = ("explorer", "faucet", "icon", "website", "docs", "info", "homepage")
URL_VALUE_KEYS = {"url", "http", "https", "ws", "wss"}  # keys whose string value is an endpoint, whatever the object around them
SECRET_PARAMS = {"key", "apikey", "api_key", "api-key", "token", "access_token", "accesstoken", "auth", "secret", "authorization", "x-api-key", "password", "passwd", "pwd", "jwt", "bearer", "client_secret", "private_key", "privatekey"}
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}
MAINNET_LIKE = ("mainnet", "unknown")  # an unknown chain id is treated as a mainnet, as its finding says
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
SEGMENT_KEY_RE = re.compile(r"[A-Za-z0-9_-]{20,}")
# environment-variable templates (${KEY}, $KEY, {{KEY}}, {KEY}, <KEY>, %KEY%): a bare "%" or "$" inside a literal secret is not one
TEMPLATE_RE = re.compile(r"\$\{[^{}]+\}|\$[A-Za-z_][A-Za-z0-9_]*|\{\{[^{}]+\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}|<[^<>]+>|%[A-Za-z_][A-Za-z0-9_]*%")

NAME_TO_ID: dict[str, int] = {}
for _id, (_name, _cls, _aliases) in CHAINS.items():
    NAME_TO_ID[re.sub(r"[^a-z0-9]+", "-", _name.lower()).strip("-")] = _id
    for _alias in _aliases:
        NAME_TO_ID[_alias] = _id


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_chain_key(key, value) -> bool:
    """A key that names a chain id: chainId, chain_id, CHAIN_ID, evmChainId, NEXT_PUBLIC_CHAIN_ID, or a bare chain whose value is
    a chain id. A bare chain holding a name ("sepolia") is a name, not an id, so it is left to the name check."""
    if _norm_key(key).endswith("chainid"):
        return True
    if re.search(r"(^|[^a-z0-9])chain$", str(key).lower()):
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, str) and _parse_chain_id(value) is not None)
    return False


def _words(key) -> list[str]:
    """The lowercase words of a key, split on camelCase and on any non-alphanumeric separator (l1ChainId -> l1, chain, id)."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(key))
    return [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]


def _direction(key) -> frozenset:
    return frozenset(word for word in _words(key) if word in DIRECTION_WORDS)


def _is_name_key(key) -> bool:
    """A name key once its direction words are set aside: network, source_network, destinationChainName, chainName..."""
    return "".join(word for word in _words(key) if word not in DIRECTION_WORDS) in NAME_KEYS


def _chain_keys_of(node: dict) -> list:
    """The keys of one object that hold a chain id; a viem-shaped object's id counts when it carries rpcUrls or nativeCurrency."""
    chain_keys = [key for key in node if _is_chain_key(key, node[key])]
    if not chain_keys:
        keys = {_norm_key(key): key for key in node}
        if "id" in keys and ("rpcurls" in keys or "nativecurrency" in keys):
            chain_keys = [keys["id"]]
    return chain_keys


def _least(a, b):
    """The smaller of two (chain id, where) pairs, None counting as absent."""
    if a is None:
        return b
    if b is None:
        return a
    return a if a <= b else b




class _Scope:
    """The chain ids in scope for one object: its own and leg entries, keyed by (chain id, direction), linked to the scope of its
    parent. A child object shares its parent's scope instead of copying it, so the walk stays linear in the document."""

    __slots__ = ("entries", "parent", "_extremes", "_index")

    def __init__(self, entries: dict, parent) -> None:
        self.entries = entries
        self.parent = parent
        self._extremes: dict = {}
        self._index = None

    def items(self):
        """(chain id, direction, where) for every entry in scope, nearest object first."""
        scope = self
        while scope is not None:
            for (chain_id, dirs), where in scope.entries.items():
                yield chain_id, dirs, where
            scope = scope.parent

    def extremes(self, key_dirs: frozenset) -> tuple:
        """(lowest overlapping main, lowest overlapping test, lowest main, lowest test) in scope, each (chain id, where) or None.
        A main id is a mainnet or unknown chain; a test id a testnet or local chain. Overlapping shares a direction word with
        key_dirs. Computed once per direction set and cached on the scope, so a host check is a lookup, not a walk of the scope."""
        if key_dirs not in self._extremes:
            ov_main, ov_test, all_main, all_test = self.parent.extremes(key_dirs) if self.parent is not None else (None, None, None, None)
            for (chain_id, dirs), where in self.entries.items():
                cand = (chain_id, where)
                overlap = bool(key_dirs) and bool(dirs & key_dirs)
                if _chain_class(chain_id) in MAINNET_LIKE:
                    all_main = _least(all_main, cand)
                    ov_main = _least(ov_main, cand) if overlap else ov_main
                else:
                    all_test = _least(all_test, cand)
                    ov_test = _least(ov_test, cand) if overlap else ov_test
            self._extremes[key_dirs] = (ov_main, ov_test, all_main, all_test)
        return self._extremes[key_dirs]

    def view(self, name_dirs: frozenset) -> "_View":
        """The candidates of this scope for one direction set. The index is built on first use and shared by every name judged here."""
        if self._index is None:
            self._index = _Index(self.items())
        return self._index.view(name_dirs)


class _Index:
    """The chain ids of one scope, indexed once so that a name costs O(family + MAX_NAME_LOCATIONS) however many keys share its direction
    set (ND10-1). Each direction word is a bit. A chain id's mask holds the bits of its entries, and a name overlaps a chain id when they
    share a bit. Overlap counts come from a subset-sum table over the words present (at most the 12 DIRECTION_WORDS), and a chain id's
    position in a view is its earliest overlapping entry, so no name rescans the entries."""

    def __init__(self, entries) -> None:
        self.bit: dict = {}  # direction word -> bit
        self.mask: dict = {}  # chain id -> bits of the words of its entries
        self.first: dict = {}  # chain id -> (entry index, where) of its first entry
        self.by_word: dict = {}  # (bit, chain id) -> (entry index, where) of its first entry with that word
        for index, (chain_id, dirs, where) in enumerate(entries):
            bits = 0
            for word in dirs:
                bit = self.bit.setdefault(word, len(self.bit))
                bits |= 1 << bit
                self.by_word.setdefault((bit, chain_id), (index, where))
            if chain_id in self.mask:
                self.mask[chain_id] |= bits
            else:
                self.mask[chain_id] = bits
                self.first[chain_id] = (index, where)
        self.lists: dict = {}  # bit -> (main, test) lists of (entry index, chain id, where), sorted
        for (bit, chain_id), (index, where) in self.by_word.items():
            self.lists.setdefault(bit, ([], []))[_side(chain_id)].append((index, chain_id, where))
        self.overall = ([], [])  # (main, test) lists over every chain id in scope
        for chain_id, (index, where) in self.first.items():
            self.overall[_side(chain_id)].append((index, chain_id, where))
        for main, test in [*self.lists.values(), self.overall]:
            main.sort()
            test.sort()
        self.total = (len(self.overall[0]), len(self.overall[1]))
        size = 1 << len(self.bit)
        self.full = size - 1
        self.sub = ([0] * size, [0] * size)  # per side: the chain ids whose mask lies within each set of words
        for chain_id, bits in self.mask.items():
            self.sub[_side(chain_id)][bits] += 1
        for table in self.sub:
            for bit in range(len(self.bit)):
                step = 1 << bit
                for word_set in range(size):
                    if word_set & step:
                        table[word_set] += table[word_set ^ step]

    def view(self, name_dirs: frozenset) -> "_View":
        """The candidates for one direction set: the overlapping chain ids when there are any, else all of them in scope."""
        nb = 0
        for word in name_dirs:
            if word in self.bit:
                nb |= 1 << self.bit[word]
        outside = self.full ^ nb
        counts = (self.total[0] - self.sub[0][outside], self.total[1] - self.sub[1][outside])
        if counts[0] or counts[1]:
            return _View(self, nb, True, counts)
        return _View(self, 0, False, self.total)


class _View:
    """The chain ids one name is judged against, in scope order: the overlapping ones when there are any, else all of them. The counts are
    exact. The lists are read lazily, so a name touches only the few chain ids it reports and the family it names."""

    __slots__ = ("index", "nb", "overlap", "n_main", "n_test", "count")

    def __init__(self, index: _Index, nb: int, overlap: bool, counts: tuple) -> None:
        self.index, self.nb, self.overlap = index, nb, overlap
        self.n_main, self.n_test = counts
        self.count = self.n_main + self.n_test

    def _entries(self, side):
        """(chain id, where) in scope order for one side (0 mainnet-like, 1 test-like) or both (None). A chain id appears once, at its
        earliest overlapping entry."""
        if self.overlap:
            lists = []
            for bit in _set_bits(self.nb):
                pair = self.index.lists.get(bit)
                if pair is not None:
                    lists.extend(pair if side is None else (pair[side],))
        else:
            lists = list(self.index.overall) if side is None else [self.index.overall[side]]
        seen: set = set()
        for _, chain_id, where in heapq.merge(*lists):
            if chain_id not in seen:
                seen.add(chain_id)
                yield chain_id, where

    def use_iter(self):
        return self._entries(None)

    def test_iter(self):
        return self._entries(1)

    def main_first(self, k: int) -> list:
        return list(islice(self._entries(0), k))

    def test_first(self, k: int) -> list:
        return list(islice(self._entries(1), k))

    def only(self) -> tuple:
        return next(self._entries(None))

    def has(self, chain_id) -> bool:
        if self.overlap:
            return bool(self.index.mask.get(chain_id, 0) & self.nb)
        return chain_id in self.index.first

    def in_family(self, family) -> list:
        """(chain id, where) for the family members in view, in scope order: one lookup per family id."""
        hits = []
        for chain_id in family:
            if not self.has(chain_id):
                continue
            if self.overlap:
                index, where = min(self.index.by_word[(bit, chain_id)] for bit in _set_bits(self.index.mask[chain_id] & self.nb))
            else:
                index, where = self.index.first[chain_id]
            hits.append((index, chain_id, where))
        hits.sort()
        return [(chain_id, where) for _, chain_id, where in hits]



MAX_NAME_LOCATIONS = 5  # findings per name: the most severe groups first; a group's total says how many offenders there are in all
_SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def _side(chain_id) -> int:
    """0 for a mainnet-like chain id (mainnet or unknown), 1 for a test-like one."""
    return 0 if _chain_class(chain_id) in MAINNET_LIKE else 1


def _set_bits(value: int) -> list:
    return [bit for bit in range(value.bit_length()) if value >> bit & 1]


def _as_mainnet(chain_id: int) -> str:
    return "a mainnet" if _chain_class(chain_id) == "mainnet" else "unknown, treated as a mainnet"


def _skip_family(pairs, family, limit: int) -> list:
    """The first limit (chain id, where) pairs whose chain id is not in family; at most len(family) pairs are skipped."""
    out = []
    for chain_id, where in pairs:
        if chain_id not in family:
            out.append((chain_id, where))
            if len(out) == limit:
                break
    return out


def name_groups(chosen: _View, raw: str, norm: str, family, primary, testnet: bool, directional: bool, several: bool) -> list:
    """The offending candidates of one network name, as (severity, total, locate, message) groups. Each candidate's verdict depends
    only on its chain id and on the name, so the totals come from the family and the counts of the summary, and locate(k) gives the
    first k offenders in scope order without visiting the rest. A name's findings are the first groups' listings, most severe first."""
    shown = _safe_name(raw)
    names = ", ".join(str(i) for i in sorted(family))
    groups: list = []
    n_main, n_test = chosen.n_main, chosen.n_test
    if not family:  # a name that denotes no known chain: only the mainnet and test words are checked
        if testnet:
            groups.append(("blocker", n_main, chosen.main_first,
                           lambda c: f"the network name {shown!r} says test network but chain id {c} is {_as_mainnet(c)}"))
        if "mainnet" in norm:
            groups.append(("major", n_test, chosen.test_first,
                           lambda c: f"the network name {shown!r} says mainnet but chain id {c} is a {_chain_class(c)} chain"))
        return groups
    in_family = chosen.in_family(family)
    if testnet:  # a test-network name is a blocker on every mainnet-like candidate, whatever its family
        groups.append(("blocker", n_main, chosen.main_first,
                       lambda c: f"the network name {shown!r} says test network but chain id {c} is {_as_mainnet(c)}"))
        in_non = [pair for pair in in_family if _chain_class(pair[0]) not in MAINNET_LIKE]
        minor_in = [pair for pair in in_non if pair[0] != primary]
        out_total = n_test - len(in_non)
        out_iter = chosen.test_iter
    else:
        minor_in = [pair for pair in in_family if pair[0] != primary]
        out_total = chosen.count - len(in_family)
        out_iter = chosen.use_iter

    def out_locate(k):
        return _skip_family(out_iter(), family, k)

    def belongs(c):
        return f"the network name {shown!r} belongs to chain id {names}, the file says {c}"
    if directional:  # a directional name with a known family must match a candidate in its own direction
        groups.append(("blocker", out_total, out_locate, belongs))
    elif not several:  # one candidate outside the family: a note when the family has several ids, else a blocker
        if len(family) > 1:
            groups.append(("minor", out_total, out_locate, lambda c: f"the network name {shown!r} may mean another network (chain ids {names}); the file says {c}"))
        else:
            groups.append(("blocker", out_total, out_locate, belongs))
    # several undirected candidates outside the family are a bridge consistent with the name: they are not judged
    groups.append(("minor", len(minor_in), lambda k: minor_in[:k],
                   lambda c: f"the network name {shown!r} can also mean another network (chain ids {names}); the file says {c}"))
    return groups



def label_groups(chosen: _View, raw: str, norm: str, family, testnet: bool, capped: bool, noun: str) -> list:
    """The findings of one label or metadata name. A test-network name on a mainnet or unknown id is a blocker unless capped (a metadata
    entry that does not declare a network), and a known network that differs from the candidate is a minor note. Silent when there is
    no family, or when there are several candidates and none of them matches. noun names the entry in the message."""
    shown = _safe_name(raw)
    names = ", ".join(str(i) for i in sorted(family))
    groups: list = []
    blocks = testnet and not capped
    if blocks:
        groups.append(("blocker", chosen.n_main, chosen.main_first,
                       lambda c: f"the {noun} {shown!r} says test network but chain id {c} is {_as_mainnet(c)}"))
    silent = not family or (chosen.count > 1 and not any(chosen.has(chain_id) for chain_id in family))
    if not silent:
        in_family = chosen.in_family(family)
        if blocks:
            in_non = [pair for pair in in_family if _chain_class(pair[0]) not in MAINNET_LIKE]
            total = chosen.n_test - len(in_non)
            locate_iter = chosen.test_iter
        else:
            total = chosen.count - len(in_family)
            locate_iter = chosen.use_iter

        def locate(k):
            return _skip_family(locate_iter(), family, k)
        groups.append(("minor", total, locate, lambda c: f"the {noun} {shown!r} names another network (chain ids {names}); the file says {c}"))
    return groups



def _url_slot(slot: str) -> bool:
    """True when a value sits under an RPC endpoint key (rpcUrl, rpcUrls, url, http...), not under a label such as name."""
    return slot in URL_VALUE_KEYS or (any(part in slot for part in URL_KEY_PARTS) and not any(part in slot for part in NOT_RPC_URL_PARTS))


def _has_word(words, vocabulary) -> bool:
    """True when one of the words is in vocabulary, or is its plural with a trailing s (chains, legs, tokens). Matching is by whole
    word, never by substring: legal is not leg, legacy is not leg, tokenHolder has no chain word."""
    return any(word in vocabulary or (word.endswith("s") and word[:-1] in vocabulary) for word in words)

# a holding key's context: its direction words, its words, whether it declares a network (a chain, network or leg word), and whether it is
# a network holder. Declaring is what the asset rule reads (ND13-2)
_Hold = namedtuple("_Hold", "dirs words network declares")
ROOT_HOLD = _Hold(frozenset(), frozenset(), True, False)  # the document root declares networks
EMPTY_HOLD = _Hold(frozenset(), frozenset(), False, False)  # an unrecognised key at the root holds nothing


# the product name, in its exact spellings only: chainlink, Chainlink, CHAINLINK. ChainLink and chainLink are two words, chain and link (ND14-2)
_CHAINLINK = re.compile(r"chainlink|Chainlink|CHAINLINK")
# a chain compound prefix: a product-name occurrence right after one of these keeps its chain word (blockchainlink, crosschainlink ...)
_COMPOUND_PREFIXES = ("block", "cross", "multi", "side", "sub", "para", "meta", "poly", "shard")


def _compound_before(text: str, start: int) -> bool:
    """True when the product name at start comes right after a chain compound prefix (ND14-3, ND15-1). In the boundary regime (the product
    spelled Chainlink or CHAINLINK, or preceded by a separator), the text before it, less trailing separators, is read from each position k:
    the suffix from k is a compound prefix when it lowercases to one, and k is 0, or the character before it is not a lowercase letter, or the character
    at k is a capital (a camel word start). So XMultiChainlink, xMultiChainlink, ETHSubChainlinkFeeds, ABCMultiChainlink and XBlockChainlink keep their chain word, while tradesideChainlink (side follows
    a lowercase e), outsideChainlink, supershardChainlink, xsubChainlink and Outside_Chainlink do not. With no boundary (all lower case),
    the text before must end with a prefix, which fails closed (crosschainlink, outsidechainlink)."""
    before = text[:start]
    if not before:
        return False
    if text[start:start + len("chainlink")] in ("Chainlink", "CHAINLINK") or not before[-1].isalnum():
        core = re.sub(r"[^A-Za-z0-9]+$", "", before)
        return any(core[k:].lower() in _COMPOUND_PREFIXES and (k == 0 or not core[k - 1].islower() or core[k].isupper()) for k in range(len(core)))
    return before.lower().endswith(_COMPOUND_PREFIXES)


def _names_chain(key) -> bool:
    """True when a holding key names a chain or network anywhere (blockchain, subchains, networkName). The product name, in the exact spellings
    chainlink, Chainlink and CHAINLINK, is removed wherever it occurs (chainlinkFeeds, ethChainlinkFeeds, feedsChainlink), so the chain test
    sees what is left: a chain word beside it still counts (chainlinkChains, chainlinkchain). It is not removed when a chain compound prefix comes
    right before it (ND14-3, ND15-1; blockchainlink, crosschainlink, XMultiChainlink ...), which keeps its chain word; ChainLink, chainLink, chain-link
    and chain_link are not the product name and keep their chain (R4, ND14-1, ND14-2)."""
    text = str(key)
    kept, last = [], 0
    for match in _CHAINLINK.finditer(text):
        start = match.start()
        if _compound_before(text, start):
            continue
        kept.append(text[last:start])
        last = match.end()
    kept.append(text[last:])
    return any(part in _norm_key("".join(kept)) for part in CHAIN_NETWORK_SUBSTRINGS)


def _hold_of(key) -> _Hold:
    """The holding context of a key that is itself a holder word."""
    norm = _norm_key(key)
    words = frozenset(_words(key))
    dirs = _direction(key)
    network = norm in NETWORK_HOLDERS or bool(dirs)
    declares = _names_chain(key) or _has_word(words, LEG_WORDS)
    return _Hold(dirs, words, network, declares)


def _is_holder_key(key) -> bool:
    """A key with a meaning of its own: a network, directional, token, container, chain, network or leg word, or an RPC key. Any other
    key is a keyed-map entry or an unknown holder, and inherits the context of the holder that encloses it (ND9-3)."""
    norm = _norm_key(key)
    words = frozenset(_words(key))
    return (norm in NETWORK_HOLDERS or norm in ENDPOINT_CONTAINERS or bool(_direction(key))
            or _has_word(words, TOKEN_WORDS) or _has_word(words, ASSET_WORDS) or _has_word(words, LEG_WORDS)
            or _names_chain(key) or any(part in norm for part in URL_KEY_PARTS))



def _endpoint_shape(node: dict) -> bool:
    """True for an object with a URL-shaped key (url, rpcUrl, http, endpoint...), whatever its list key is called: an endpoint entry.
    Explorer, faucet and similar URLs do not count."""
    for key in node:
        norm = _norm_key(key)
        if any(part in norm for part in ("url", "endpoint", "rpc", "http")) and not any(part in norm for part in NOT_RPC_URL_PARTS):
            return True
    return False


def _is_label(name_norm: str, flags: dict, name_dirs: frozenset) -> bool:
    """Decided by the key that holds the entry, not by its shape. An entry under an endpoint container, or in a metadata object, is
    a label whatever it carries. An object with its own chain id names its networks: its name is a network name. Without a chain
    id of its own, a name is a network name only under a network holder, a directional holding key, the document root, or a
    directional name key, or under a holding key that declares a chain or network (ND14-4: check_names only judges a name with chain ids in scope);
    under any other holding key it is metadata, a label. Under a network holder, a name with a URL sibling is a label only when it is an RPC-provider brand."""
    if flags["container"] or flags["metadata"]:
        return True
    if flags["chain"]:
        return False
    if not (flags["network"] or name_dirs or flags["declares"]):
        return True
    return flags["url"] and name_norm in RPC_BRANDS


def _safe_key(key) -> str:
    """A config key as it may appear in a location: short names only, anything that could hold a secret is replaced."""
    text = str(key)
    return text if re.fullmatch(r"[A-Za-z0-9 _.:-]{1,60}", text) and not _secret_like(text) else "<key>"


def _safe_name(name: str) -> str:
    text = name.strip()
    return text if re.fullmatch(r"[A-Za-z0-9 ._-]{1,60}", text) and not _secret_like(text) else "<redacted>"


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


_FAMILY_CACHE: dict[str, frozenset] = {}


def _family(norm: str) -> frozenset:
    """The chain ids a normalised name can denote: its own id, and the ids of the names that extend it ("base" denotes 8453
    and, as the leading word of "base-sepolia", 84532). The prefix rule is deliberate: a family includes the testnet of its
    named network, so a mainnet-named destination on its own testnet id is a minor note, not a blocker (ND-F). Empty for a
    name that denotes no known chain."""
    if norm not in _FAMILY_CACHE:
        ids = {NAME_TO_ID[norm]} if norm in NAME_TO_ID else set()
        ids.update(chain_id for other, chain_id in NAME_TO_ID.items() if other.startswith(norm + "-"))
        _FAMILY_CACHE[norm] = frozenset(ids)
    return _FAMILY_CACHE[norm]



def _tokens(text: str) -> set[str]:
    """Whole words of a name or host, split on anything that is not a letter or digit."""
    return {part for part in re.split(r"[^a-z0-9]+", text.lower()) if part}


def _says_testnet(text: str) -> bool:
    """A name or host that names a test network: a long test-network word anywhere in its letters, or a short one as a whole word."""
    letters = re.sub(r"[^a-z0-9]", "", text.lower())
    return any(word in letters for word in TESTNET_NAME_WORDS) or not _tokens(text).isdisjoint(TESTNET_NAME_TOKENS)


def _parse_chain_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            parsed = int(text, 16) if text.lower().startswith("0x") else int(text, 10) if text.isdigit() else -1
        except ValueError:
            return None
    else:
        return None
    return parsed if 0 < parsed <= MAX_CHAIN_ID else None


def _truthy(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() in ("true", "1", "yes"))


def _chain_class(chain_id: int) -> str:
    known = CHAINS.get(chain_id)
    return known[1] if known else "unknown"


def _placeholder(value: str | None) -> bool:
    """True when the value is made only of environment-variable templates and punctuation, never of literal text."""
    return bool(value) and TEMPLATE_RE.search(value) is not None and re.search(r"[A-Za-z0-9]", TEMPLATE_RE.sub("", value)) is None


def _url_problems(url: str) -> list[tuple[str, str, str]]:
    """(rule, severity, message) for one URL; the URL itself is never put in a message."""
    out: list[tuple[str, str, str]] = []
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return [("rpc-url-malformed", "minor", "the endpoint is not a parseable URL")]
    scheme = parts.scheme.lower()
    userinfo = [part for part in (parts.username, parts.password) if part is not None]
    if userinfo and not all(_placeholder(part) for part in userinfo):  # ${USER}:${PASS}@host is a template, not a credential
        out.append(("rpc-url-credentials", "blocker", "the endpoint embeds user:password credentials; move them to a secret store and reference an environment variable"))
    if scheme in ("http", "ws"):
        if host in LOOPBACK:
            out.append(("rpc-url-local-plaintext", "info", f"plaintext {scheme} to a loopback host is acceptable for a local node"))
        else:
            out.append(("rpc-url-insecure-scheme", "major", f"the endpoint uses plaintext {scheme}: use {'https' if scheme == 'http' else 'wss'}"))
    elif scheme not in ("https", "wss"):
        out.append(("rpc-url-scheme", "minor", f"the endpoint scheme {scheme or '(none)'} is not https or wss"))
    leaked = [name for name, val in parse_qsl(parts.query, keep_blank_values=True) if name.lower() in SECRET_PARAMS and val and not _placeholder(val)]
    segments = [s for s in parts.path.split("/") if SEGMENT_KEY_RE.fullmatch(s) and s.count("-") <= 1 and re.search(r"\d", s) and re.search(r"[A-Za-z]", s)]
    if leaked or segments:
        where = f"query parameter {leaked[0].lower()}" if leaked else "a long token-like path segment"
        out.append(("rpc-url-embedded-key", "blocker", f"the endpoint carries an API key in {where}; reference it from an environment variable instead"))
    return out


def _host_tokens(url: str) -> str:
    try:
        parts = urlsplit(url)
        return ((parts.hostname or "") + parts.path).lower()
    except ValueError:
        return ""


class _Scan:
    def __init__(self) -> None:
        self.findings: list[dict] = []
        self.truncated = False
        self.dropped = {sev: 0 for sev in SEVERITY_RANK}  # findings past MAX_STORED still count toward the status
        self.suppressed = {sev: 0 for sev in SEVERITY_RANK}  # offenders of one name past MAX_NAME_LOCATIONS: counted, not listed
        self.names: dict[str, dict[int, str]] = {}
        self.budget = MAX_NODES
        self.exhausted = False
        self.too_deep = False
        self.reasons: list[str] = []  # one label and path per in-scope input or value the checks did not cover

    def add(self, rel: str, rule: str, severity: str, where: str, message: str) -> None:
        if len(self.findings) >= MAX_STORED:
            self.truncated = True
            self.dropped[severity] += 1
            return
        location = f"{rel}:{where}" if where else rel
        self.findings.append({"id": f"{rule}:{location}", "severity": severity, "rule": rule, "file": rel, "location": location, "summary": message, "message": message})

    def walk(self, rel: str, node, path: str, ctx: dict, depth: int, in_rpc: bool = False, key_dirs: frozenset = frozenset(), slot: str = "", hold=None) -> None:
        if depth > MAX_DEPTH:
            self.too_deep = True
            return
        if self.budget <= 0:
            self.exhausted = True
            return
        self.budget -= 1
        if isinstance(node, dict):
            keys = {_norm_key(k): k for k in node}
            opt_key = next((k for k in keys if k in OPT_IN_KEYS and _truthy(node[keys[k]])), None)
            here = dict(ctx)
            if opt_key is not None:
                here["opt_in"] = True
            chain_keys = _chain_keys_of(node)
            own = self.own_entries(rel, node, path, chain_keys, key_dirs)
            legs = self.leg_entries(rel, node, path, key_dirs) if any(_direction(key) for key in node) else []
            scope = ctx.get("scope")
            if own or legs:
                frame: dict = {}
                for chain_id, dirs, where in own + legs:
                    frame.setdefault((chain_id, dirs), where)
                scope = _Scope(frame, scope)
            here["scope"] = scope
            # an object with chain ids of its own judges its endpoints by those ids alone, as check_names does for its names; an
            # inherited id must not stand in for them. An object without ids of its own inherits the nearest enclosing own-id scope,
            # so an endpoint in a child object of a network item is judged by that item's id, not by a mainnet id above it. An object
            # with legs and no ids of its own keeps the full scope (None here), which fails closed.
            own_scope = None
            if own:
                own_frame: dict = {}
                for chain_id, dirs, where in own:
                    own_frame.setdefault((chain_id, dirs), where)
                own_scope = _Scope(own_frame, None)
            elif not legs:
                own_scope = ctx.get("own_scope")
            here["own_scope"] = own_scope
            # the holding context of this object: the key that holds it. An unrecognised key inherits its enclosing holder's context only
            # when that holder has no chain id of its own, i.e. a keyed map (ND9-3). A list passes its own context to its items. The root declares networks.
            h = ROOT_HOLD if hold is None else hold
            container = ctx.get("container", False) or slot in ENDPOINT_CONTAINERS
            # shape keys: address, symbol or decimals. The root object is never a metadata record (R1): it declares its networks as the root does
            shape_keys = any(_norm_key(k) in METADATA_KEYS for k in node)
            shape = shape_keys and depth > 0
            token_r3 = _has_word(h.words, TOKEN_WORDS)
            token_r2 = _has_word(h.words, TOKEN_WORDS) or (_has_word(h.words, ASSET_WORDS) and not h.declares)
            # ND8-4, R2, ND12-3, ND13-2: an object with its own chain id is a token record when its holder carries a token word, asset included only when the holder declares no network, and it has shape keys.
            # Without a chain id, a token word or shape keys make an object metadata (ND5, ND6-1). Chain-bearing objects outside token words
            # are never metadata (ND6-2, ND7-2)
            token_owner = bool(chain_keys) and token_r2 and shape
            metadata = token_owner or (not chain_keys and (ctx.get("metadata", False) or token_r3 or shape))
            # ND9-2, R3: a metadata-shape record with no chain id of its own declares no network when its holder carries a token word, or
            # when its holder names no chain or network. A record with its own chain id is exempt only under a token word (asset: holder names no chain). Nothing
            # beneath a record that declares no network declares one
            shape_record = shape and (token_r2 if chain_keys else (token_r3 or not h.declares))
            no_declare = ctx.get("no_declare", False) or shape_record
            network = h.network
            declares = h.declares and not no_declare
            here["container"], here["metadata"], here["no_declare"] = container, metadata, no_declare
            # ND10-3, R5, ND12-2: an object with a URL-shaped key and no chain id of its own is an endpoint entry, whatever its key is called. Shape
            # keys (address, symbol, decimals) make it metadata only under a token word; otherwise a URL makes it an endpoint whatever its shape keys
            url = _endpoint_shape(node)
            endpoint = depth > 0 and not chain_keys and url and (not shape_keys or not token_r3)
            flags = {"container": container or endpoint, "metadata": metadata, "url": url, "chain": bool(chain_keys), "network": network, "declares": declares}
            self.check_object(rel, node, path, here, own, legs, key_dirs, flags)
            for key, value in node.items():
                child = f"{path}.{_safe_key(key)}" if path else _safe_key(key)
                norm = _norm_key(key)
                rpc_here = in_rpc or (any(part in norm for part in URL_KEY_PARTS) and not any(part in norm for part in NOT_RPC_URL_PARTS))
                if not rpc_here and chain_keys and "url" in norm and not any(part in norm for part in NOT_RPC_URL_PARTS):
                    rpc_here = True
                child_hold = _hold_of(key) if _is_holder_key(key) else (EMPTY_HOLD if hold is None or chain_keys else hold)
                self.walk(rel, value, child, here, depth + 1, rpc_here, key_dirs | _direction(key), norm, child_hold)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self.walk(rel, value, f"{path}[{index}]", ctx, depth + 1, in_rpc, key_dirs, slot, hold)
        elif isinstance(node, str) and in_rpc:
            text = node.strip()
            if URL_RE.match(text):
                self.check_url(rel, text, path, ctx, key_dirs)
            elif _url_slot(slot) and not _placeholder(text):
                self.add(rel, "rpc-url-not-a-url", "minor", path, "the endpoint is not a URL with a scheme, so its host was not checked against the chain id")

    @staticmethod
    def unique(entries: list) -> list:
        """One entry per (chain id, direction), first occurrence kept."""
        seen: set = set()
        out = []
        for entry in entries:
            if (entry[0], entry[1]) not in seen:
                seen.add((entry[0], entry[1]))
                out.append(entry)
        return out

    def own_entries(self, rel: str, node: dict, path: str, chain_keys: list, key_dirs: frozenset) -> list:
        """(chain id, direction, where) for the chain-id keys of one object; a direction is inherited from the keys above it."""
        out = []
        for key in sorted(chain_keys, key=str):
            where = f"{path}.{_safe_key(key)}" if path else _safe_key(key)
            chain_id = _parse_chain_id(node[key])
            if chain_id is None:
                self.add(rel, "chain-id-unparseable", "minor", where, "the chain id is not a positive integer, decimal text or 0x hex text")
                self.reasons.append(f"{rel}:{where}: the chain id is not a positive integer, decimal text or 0x hex text, so its network checks were skipped")
                continue
            out.append((chain_id, key_dirs | _direction(key), where))
        return out

    def leg_entries(self, rel: str, node: dict, path: str, key_dirs: frozenset) -> list:
        """The chain ids under the direction-keyed children of one object (its legs), each with the directions inherited on the way."""
        budget = [MAX_NODES]
        truncated = [False]

        def collect(value, dirs: frozenset, where: str, depth: int) -> list:
            if depth > MAX_DEPTH:
                return []
            if budget[0] <= 0:
                truncated[0] = True
                return []
            budget[0] -= 1
            out = []
            if isinstance(value, dict):
                chain_keys = set(_chain_keys_of(value))
                for key, child in value.items():
                    child_where = f"{where}.{_safe_key(key)}" if where else _safe_key(key)
                    child_dirs = dirs | _direction(key)
                    if key in chain_keys:
                        chain_id = _parse_chain_id(child)
                        if chain_id is not None:
                            out.append((chain_id, child_dirs, child_where))
                    out += collect(child, child_dirs, child_where, depth + 1)
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    out += collect(child, dirs, f"{where}[{index}]", depth + 1)
            return out

        found = []
        for key, value in node.items():
            if _direction(key):
                found += collect(value, key_dirs | _direction(key), f"{path}.{_safe_key(key)}" if path else _safe_key(key), 1)
        if truncated[0]:
            self.reasons.append(f"{rel}:{path or '.'}: more than {MAX_NODES} nodes under a direction key: not all of its chain ids were checked")
        return found

    def check_object(self, rel: str, node: dict, path: str, here: dict, own: list, legs: list, key_dirs: frozenset, flags: dict) -> None:
        """The checks of one object: its own chain ids, its ids and legs together (mixed networks, conflicts), and its name keys."""
        for chain_id, _dirs, where in self.unique(own):
            self.check_chain(rel, chain_id, where, here)
        entries = self.unique(own + legs)
        classes = {_chain_class(chain_id) for chain_id, _, _ in entries}
        if "testnet" in classes and classes & set(MAINNET_LIKE):
            self.add(rel, "chain-id-mixed-networks", "minor", path, "the object holds a test network chain id and a mainnet chain id: each is checked on its own leg, and the two are not compared")
        problems = []
        undirected = sorted({chain_id for chain_id, dirs, _ in entries if not dirs})
        if len(undirected) > 1:
            problems.append(f"one object names {len(undirected)} different chain ids ({', '.join(str(i) for i in undirected)}) without a direction: one network per object")
        groups: dict[str, set] = {}
        for chain_id, dirs, _ in entries:
            for word in dirs:
                groups.setdefault(word, set()).add(chain_id)
        for word in sorted(groups):
            if len(groups[word]) > 1:
                problems.append(f"the {word} direction names {len(groups[word])} different chain ids: one network per direction")
        if problems:
            self.add(rel, "chain-id-keys-disagree", "major", path, "; ".join(problems))
        self.check_names(rel, node, path, here, own, key_dirs, flags)

    def check_names(self, rel: str, node: dict, path: str, here: dict, own: list, key_dirs: frozenset, flags: dict) -> None:
        """Every name key is judged against the chain ids of its own object when it has one, and only against the legs in scope
        when it has none. An entry that _is_label names is a label: label_groups judges it, at most a minor note except a test-network
        name on a mainnet or unknown id of an entry that is not metadata. Any other name is a network name, judged by name_groups.
        Each name costs a lookup of its family and a count, and emits at most MAX_NAME_LOCATIONS findings."""
        scope = here.get("scope")
        index = None  # ND10-1: the own chain ids are indexed once per object, at the first name key that needs them
        for key, value in node.items():
            if not _is_name_key(key) or not isinstance(value, str):
                continue
            name_dirs = key_dirs | _direction(key)
            if own:
                if index is None:
                    index = _Index(own)
                chosen = index.view(name_dirs)
            else:
                chosen = scope.view(name_dirs) if scope is not None else _Index(()).view(name_dirs)
            if not chosen.count:
                continue
            norm = _norm_name(value)
            family = _family(norm)
            testnet = _says_testnet(norm)
            if _is_label(norm, flags, _direction(key)):
                # an endpoint container entry is an RPC label whatever it carries (ND6-3). Otherwise a label with no network context
                # (metadata, or an unknown holder: ND7-3) is at most a minor note, unless its holding key declares chains or networks (ND8-5)
                capped = not flags["container"] and not flags["declares"] and (flags["metadata"] or not flags["network"])
                if flags["container"]:
                    noun = "RPC label"
                elif flags["declares"]:
                    noun = "network name"
                elif flags["network"] and not flags["metadata"]:
                    noun = "RPC label"
                else:
                    noun = "name"
                self.emit_groups(rel, label_groups(chosen, value, norm, family, testnet, capped, noun))
                continue
            primary = NAME_TO_ID.get(norm)
            self.emit_groups(rel, name_groups(chosen, value, norm, family, primary, testnet, bool(name_dirs), chosen.count > 1))
            if chosen.count == 1:
                chain_id, where = chosen.only()
                self.record_name(rel, value, norm, chain_id, where)

    def emit_groups(self, rel: str, groups: list) -> None:
        """Emit at most MAX_NAME_LOCATIONS findings for one name, most severe group first. Every offender is counted: the offenders a
        listing does not show go to self.suppressed, so counts holds the true total (ND9-4). A listed finding says how many offenders of
        its kind there are in all. The first group with any offender always lists, so the most severe kind sets the verdict."""
        shown = 0
        for severity, total, locate, message in sorted(groups, key=lambda group: _SEVERITY_ORDER[group[0]]):
            if total <= 0:
                continue
            take = min(total, MAX_NAME_LOCATIONS - shown)
            if take > 0:
                for chain_id, where in locate(take):
                    text = message(chain_id)
                    if total > take:
                        text += f" ({total} chain ids of this kind in scope; the first {take} are listed)"
                    self.add(rel, "chain-id-name-mismatch", severity, where, text)
                shown += take
            self.suppressed[severity] += total - max(take, 0)

    def check_chain(self, rel: str, chain_id: int, where: str, ctx: dict) -> None:
        cls = _chain_class(chain_id)
        if cls in MAINNET_LIKE and not ctx.get("opt_in"):
            self.add(rel, "mainnet-without-opt-in", "major", where, f"chain id {chain_id} ({'unknown, treated as mainnet' if cls == 'unknown' else CHAINS[chain_id][0]}) is configured without an explicit mainnet opt-in key such as allowMainnet")
        if cls == "unknown":
            self.add(rel, "chain-id-unknown", "minor", where, f"chain id {chain_id} is not in the reference table of known networks")

    def record_name(self, rel: str, raw: str, norm: str, chain_id: int, where: str) -> None:
        """A name seen against one chain id only: a second id for the same name, in another object or file, is a conflict."""
        by_id = self.names.setdefault(norm, {})
        if chain_id not in by_id:
            if by_id:
                first_id, first_where = sorted(by_id.items())[0]
                self.add(rel, "chain-id-conflict", "major", where, f"the network name {_safe_name(raw)!r} maps to chain id {chain_id} here and to {first_id} at {first_where}")
            by_id[chain_id] = f"{rel}:{where}"


    def check_url(self, rel: str, url: str, where: str, ctx: dict, key_dirs: frozenset = frozenset()) -> None:
        for rule, severity, message in _url_problems(url):
            self.add(rel, rule, severity, where, message)
        scope = ctx.get("own_scope")
        if scope is None:
            scope = ctx.get("scope")
        if scope is None:
            return
        # a host under a directed key is compared with the ids of that direction; with none, with every id in scope (fail closed)
        ov_main, ov_test, all_main, all_test = scope.extremes(key_dirs)
        main, test = (ov_main, ov_test) if (ov_main or ov_test) else (all_main, all_test)
        host = _host_tokens(url)
        triggers = []
        if _says_testnet(host) and main:
            triggers.append(main)
        if "mainnet" in host and test:
            triggers.append(test)
        if not triggers:
            return
        chain_id = min(triggers)[0]  # the lowest chain id that triggers, as a scan in id order would find it
        cls = _chain_class(chain_id)
        if cls in MAINNET_LIKE:
            self.add(rel, "rpc-host-chain-mismatch", "blocker", where, f"the endpoint host names a test network but the chain id {chain_id} is {'a mainnet' if cls == 'mainnet' else 'unknown, treated as a mainnet'}")
        else:
            self.add(rel, "rpc-host-chain-mismatch", "major", where, f"the endpoint host names a mainnet but the chain id {chain_id} is a {cls} chain")


def _load_yaml_module():
    try:
        import yaml  # noqa: PLC0415 - optional dependency, probed on use
    except ImportError:
        return None
    return yaml


def _load_toml_module():
    try:
        import tomllib  # noqa: PLC0415 - stdlib on Python 3.11+, absent on older interpreters
    except ImportError:
        return None
    return tomllib


def _env_value(raw: str) -> str:
    """A dotenv value: up to the matching closing quote when quoted, else up to a ` #` inline comment."""
    raw = raw.strip()
    if raw[:1] in ("'", '"'):
        end = raw.find(raw[0], 1)
        return raw[1:end] if end > 0 else raw[1:]
    return re.split(r"\s+#", raw, maxsplit=1)[0].strip()


def _parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        out[key.strip()] = _env_value(value)
    return out


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name


def _candidates(base: Path, root: Path, reasons: list[str], flags: dict):
    """Yield (path, kind) for chain-config candidates, depth-first in sorted order, symlinks never followed.

    An explicit file the scan does not read (a symlink, or a suffix it does not parse), a symlinked directory, a directory that
    cannot be listed and the visited-file limit are inputs the scan did not read: each adds a reason; the visited limit also sets
    flags["visit_capped"]."""
    visited = 0
    if base.is_file():
        rel = _rel(base, root)
        kind = _kind(base)
        if base.is_symlink():
            reasons.append(f"{rel}: symlink, not followed")
        elif kind is None:
            reasons.append(f"{rel}: not a chain-config file type the scan reads, not checked")
        else:
            yield base, kind
        return

    def unlistable(err: OSError) -> None:
        reasons.append(f"{_rel(Path(err.filename or base), root)}: directory could not be listed, not scanned")

    for current, dirs, files in os.walk(base, followlinks=False, onerror=unlistable):
        keep = []
        for d in sorted(dirs):
            here = Path(current) / d
            if d in SKIP_DIRS:
                continue
            if here.is_symlink():
                reasons.append(f"{_rel(here, root)}: symlinked directory, not followed")
                continue
            keep.append(d)
        dirs[:] = keep
        for name in sorted(files):
            visited += 1
            if visited > MAX_VISITED:
                flags["visit_capped"] = True
                reasons.append(f"more than {MAX_VISITED} files under the scanned path: the rest were not listed")
                return
            path = Path(current) / name
            kind = _kind(path)
            if kind and name not in SKIP_FILES:
                yield path, kind


def _kind(path: Path) -> str | None:
    if path.name == ".env" or path.name.startswith(".env."):
        return "env"
    return SUFFIX_KIND.get(path.suffix.lower())


def scan(root: Path, base: Path) -> dict:
    yaml_mod, toml_mod = _load_yaml_module(), _load_toml_module()
    scanner = _Scan()
    seen = {"json": 0, "yaml": 0, "toml": 0, "env": 0}
    skipped = {"too_large": 0, "binary": 0, "symlink": 0, "unparsed": 0}
    reasons: list[str] = scanner.reasons  # one label and path per in-scope input that was not analysed; incomplete_findings turns them into a finding
    candidates, truncated_files = 0, False
    flags = {"visit_capped": False}
    for path, kind in _candidates(base, root, reasons, flags):
        rel = _rel(path, root)
        if path.is_symlink():
            skipped["symlink"] += 1
            reasons.append(f"{rel}: symlink, not followed")
            continue
        if candidates >= MAX_CANDIDATES:
            truncated_files = True
            break
        candidates += 1
        if kind == "yaml" and yaml_mod is None:
            seen["yaml"] += 1
            reasons.append(f"{rel}: not checked, PyYAML is not installed")
            continue
        if kind == "toml" and toml_mod is None:
            seen["toml"] += 1
            reasons.append(f"{rel}: not checked, no TOML parser is available")
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                skipped["too_large"] += 1
                reasons.append(f"{rel}: larger than 1 MiB, not checked")
                continue
            raw = path.read_bytes()
        except OSError:
            skipped["unparsed"] += 1
            reasons.append(f"{rel}: could not be read, not checked")
            continue
        if b"\0" in raw:
            skipped["binary"] += 1
            reasons.append(f"{rel}: binary, not checked")
            continue
        text = raw.decode("utf-8", errors="replace")
        try:
            if kind == "json":
                docs = [json.loads(text)]
            elif kind == "toml":
                docs = [toml_mod.loads(text)]
            elif kind == "yaml":
                docs = list(yaml_mod.safe_load_all(text))
            else:
                docs = [_parse_env(text)]
        except (ValueError, RecursionError, yaml_mod.YAMLError if yaml_mod else ValueError):
            skipped["unparsed"] += 1
            reasons.append(f"{rel}: could not be parsed as {kind}, not checked")
            continue
        seen[kind] += 1
        for doc in docs:
            scanner.budget, scanner.exhausted, scanner.too_deep = MAX_NODES, False, False
            scanner.walk(rel, doc, "", {}, 0)
            if scanner.exhausted:
                scanner.add(rel, "scan-truncated", "minor", "", f"the document has more than {MAX_NODES} nodes: only part of it was checked")
                reasons.append(f"{rel}: more than {MAX_NODES} nodes, only part checked")
            if scanner.too_deep:
                reasons.append(f"{rel}: nested deeper than {MAX_DEPTH} levels, that part not checked")
    if truncated_files:
        scanner.add(".", "scan-truncated", "minor", "", f"more than {MAX_CANDIDATES} candidate files: the rest were not scanned")
        reasons.append(f"more than {MAX_CANDIDATES} candidate files: the rest were not scanned")
    checks = {
        "json": "ran" if seen["json"] else "no-files",
        "toml": "skipped:tool-missing" if toml_mod is None and seen["toml"] else "ran" if seen["toml"] else "no-files",
        "yaml": "skipped:tool-missing" if yaml_mod is None and seen["yaml"] else "ran" if seen["yaml"] else "no-files",
        "env": "ran" if seen["env"] else "no-files",
    }
    files_scanned = sum(seen[k] for k in seen if checks[k] != "skipped:tool-missing")
    scanner.findings += [{**item, "file": ".", "location": ".", "message": item["summary"]} for item in incomplete_findings(reasons, files_scanned)]
    ordered = sorted(scanner.findings, key=lambda f: (-SEVERITY_RANK[f["severity"]], f["file"], f["rule"], f["location"]))
    findings, cut = ordered[:MAX_FINDINGS], ordered[MAX_FINDINGS:]
    dropped = dict(scanner.dropped)
    for finding in cut:
        dropped[finding["severity"]] += 1
    unlisted = sum(dropped.values())
    # a finding that did not fit in the list still counts: a blocker past the cap must not leave the status ok
    counts = {sev: sum(1 for f in findings if f["severity"] == sev) + dropped[sev] + scanner.suppressed[sev] for sev in SEVERITY_RANK}
    status = "fail" if scan_status(ordered) == "fail" or scanner.dropped["major"] or scanner.dropped["blocker"] else "ok"
    tail = f" ({unlisted} not listed)" if unlisted else ""
    return {
        "status": status,
        "files_scanned": files_scanned,
        "skipped": skipped,
        "checks": checks,
        "truncated": truncated_files or flags["visit_capped"] or scanner.truncated or unlisted > 0 or sum(scanner.suppressed.values()) > 0,
        "counts": counts,
        "findings": findings,
        "summary": f"{sum(counts.values())} finding(s): {counts['blocker']} blocker, {counts['major']} major, {counts['minor']} minor, {counts['info']} info{tail}",
    }


def run(args, ctx) -> dict:
    if ctx.dry_run:
        return {
            "status": "ok", "dry_run": True, "files_scanned": 0, "skipped": {"too_large": 0, "binary": 0, "symlink": 0, "unparsed": 0},
            "checks": {"json": "no-files", "toml": "no-files", "yaml": "no-files", "env": "no-files"}, "truncated": False,
            "counts": {"info": 0, "minor": 0, "major": 0, "blocker": 0}, "findings": [],
            "summary": "dry-run: chain_config_check would scan --path under --root for chain and RPC configuration",
        }
    target = (ctx.root / args.path).resolve()
    if not target.is_relative_to(ctx.root):
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} leaves the repository root")
    if not target.exists():
        raise SwarmError(ErrorCode.E_INPUT, f"--path {args.path!r} does not exist under {ctx.root}")
    return scan(ctx.root, target)


def add_args(p) -> None:
    p.add_argument("--path", default=".", help="file or directory under --root to scan")


if __name__ == "__main__":
    sys.exit(AgentScript("A28", "chain_config_check", run, description=__doc__, add_args=add_args).main())
