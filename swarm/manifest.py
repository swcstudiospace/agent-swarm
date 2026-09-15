"""Agent manifest registry — loads agents.json (07-scalability.md §1 schema)."""
from __future__ import annotations
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AGENTS_FILE = Path(os.environ.get("SWARM_AGENTS_FILE", ROOT / "agents.json"))
REQUIRED = ("id", "code", "name", "slug", "class", "lane", "capabilities", "consumes",
            "produces", "max_parallel", "autonomy_ceiling", "scripts", "description")


def load_manifest(path: Path | None = None) -> list[dict]:
    data = json.loads(Path(path or AGENTS_FILE).read_text(encoding="utf-8"))
    agents = data["agents"] if isinstance(data, dict) else data
    for a in agents:
        missing = [k for k in REQUIRED if k not in a]
        if missing:
            raise ValueError(f"agent {a.get('id')} manifest missing {missing}")
    return agents


def get_agent(ident: str, path: Path | None = None) -> dict:
    ident_l = ident.lower()
    for a in load_manifest(path):
        if ident_l in (a["id"].lower(), a["code"].lower(), a["slug"].lower()):
            return a
    raise KeyError(f"no agent matches {ident!r}")


def by_capability(capability: str, path: Path | None = None) -> list[dict]:
    return [a for a in load_manifest(path) if capability in a["capabilities"]]
