"""Shared helpers of the omp workspace-install tests: the installer module, env keys and file utilities."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("_install_omp", ROOT / "scripts" / "_install_omp.py")
inst = importlib.util.module_from_spec(_spec)
sys.modules["_install_omp"] = inst  # dataclasses resolve their module through sys.modules
_spec.loader.exec_module(inst)
PKG = str(inst.PKG)
_ENV_KEYS = ("OMP_PROFILE", "PI_PROFILE", "PI_CODING_AGENT_DIR", "SWARM_AGENTS_FILE")
try:
    import yaml as _yaml  # bound before any test blocks the import, so round-trips work in stdlib mode too
except ImportError:
    _yaml = None


def _cfg(ws):
    return ws / ".omp" / "config.yml"


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _snapshot(*dirs):
    return {
        p: p.read_bytes()
        for d in dirs
        for p in sorted(Path(d).rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
