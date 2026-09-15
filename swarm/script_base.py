"""Common scaffolding for every per-agent script in scripts/.

Usage pattern
-------------
    from swarm.script_base import AgentScript

    def run(args, ctx) -> dict:      # return a JSON-serialisable result
        ...

    if __name__ == "__main__":
        AgentScript("A08", "qa_gate", run, add_args=lambda p: p.add_argument("--path")).main()

Every script therefore gets: --task-id, --correlation-id, --json, --dry-run, exit codes
(0 ok / 1 finding-fail / 2 taxonomy error), taxonomy error mapping and an events.jsonl entry.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .errors import SwarmError, classify
from .runlog import emit

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Ctx:
    agent_id: str
    script: str
    task_id: str | None
    correlation_id: str | None
    dry_run: bool
    root: Path = field(default_factory=lambda: Path.cwd())

    def emit(self, event_type: str, payload: dict, *, task_id: str | None = None) -> dict:
        return emit(event_type, payload, source=f"{self.agent_id}@{self.script}",
                    correlation_id=self.correlation_id, task_id=task_id or self.task_id or payload.get("task_id"))


class AgentScript:
    EXIT_OK, EXIT_FAIL, EXIT_ERROR = 0, 1, 2

    def __init__(self, agent_id: str, name: str, run: Callable[[argparse.Namespace, Ctx], dict],
                 *, description: str = "", add_args: Callable[[argparse.ArgumentParser], None] | None = None):
        self.agent_id, self.name, self.run, self.description, self.add_args = agent_id, name, run, description, add_args

    def parser(self) -> argparse.ArgumentParser:
        p = argparse.ArgumentParser(prog=self.name, description=self.description)
        p.add_argument("--task-id", default=os.environ.get("SWARM_TASK_ID"))
        p.add_argument("--correlation-id", default=os.environ.get("SWARM_CORRELATION_ID"))
        p.add_argument("--json", action="store_true", help="print machine-readable JSON only")
        p.add_argument("--dry-run", action="store_true", help="deterministic canned output, no side effects")
        p.add_argument("--root", default=".", help="repository root to operate on")
        if self.add_args:
            self.add_args(p)
        return p

    def main(self, argv: list[str] | None = None) -> int:
        args = self.parser().parse_args(argv)
        ctx = Ctx(self.agent_id, self.name, args.task_id, args.correlation_id, args.dry_run, Path(args.root).resolve())
        try:
            result = self.run(args, ctx)
            result.setdefault("agent", self.agent_id)
            result.setdefault("script", self.name)
            result.setdefault("status", "ok")
            ctx.emit(f"script.{self.name}", result)
            self._print(result, args.json)
            return self.EXIT_FAIL if result.get("status") == "fail" else self.EXIT_OK
        except SwarmError as e:
            err = {"agent": self.agent_id, "script": self.name, "status": "error", "error": e.to_dict()}
            ctx.emit(f"script.{self.name}.error", err)
            self._print(err, args.json)
            return self.EXIT_ERROR
        except Exception as e:  # noqa: BLE001 - must map to taxonomy
            code = classify(e)
            err = {"agent": self.agent_id, "script": self.name, "status": "error",
                   "error": {"code": code.value, "message": f"{type(e).__name__}: {e}",
                             "handling": code.standard_handling, "trace": traceback.format_exc(limit=3)}}
            ctx.emit(f"script.{self.name}.error", err)
            self._print(err, args.json)
            return self.EXIT_ERROR

    @staticmethod
    def _print(obj: dict, as_json: bool) -> None:
        if as_json:
            print(json.dumps(obj, indent=2, default=str))
            return
        status = obj.get("status", "ok").upper()
        print(f"[{obj.get('agent')}/{obj.get('script')}] {status}")
        summary = obj.get("summary")
        if summary:
            print(summary)
        for f in obj.get("findings", []) or []:
            print(f"  - [{f.get('severity','?')}] {f.get('id','')} {f.get('summary','')}"
                  + (f"  ({f['location']})" if f.get("location") else ""))
        if obj.get("error"):
            print("  error:", json.dumps(obj["error"], indent=2, default=str))


def sh(cmd: list[str] | str, *, cwd: Path | None = None, timeout: int = 600, check: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capturing output; never raises unless check=True."""
    shell = isinstance(cmd, str)
    return subprocess.run(cmd, cwd=cwd, shell=shell, capture_output=True, text=True, timeout=timeout, check=check)


def which(binary: str) -> bool:
    from shutil import which as _w
    return _w(binary) is not None


def iter_files(root: Path, *, exts: tuple[str, ...] = (), skip_dirs=(".git", "node_modules", ".venv", "venv",
                                                                     "dist", "build", "__pycache__", ".swarm")):
    for p in root.rglob("*"):
        if any(part in skip_dirs for part in p.parts):
            continue
        if p.is_file() and (not exts or p.suffix in exts):
            yield p
