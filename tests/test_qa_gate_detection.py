"""qa_gate detects pytest with the interpreter that runs it.

importlib.util.find_spec("pytest") follows sys.executable. A PATH entry for the
pytest binary is not required, and the TypeScript twin stays a pass-through onto
that same script.
"""
import importlib.util
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_qa_gate():
    path = ROOT / "scripts" / "qa_gate.py"
    spec = importlib.util.spec_from_file_location("qa_gate_detection_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pytest_detected_when_importable_but_not_on_path(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_sample.py").write_text("def test_ok():\n    assert True\n")
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    assert shutil.which("pytest") is None
    assert importlib.util.find_spec("pytest") is not None

    runners = [r for r in _load_qa_gate().detect_runners(root) if r["name"] == "pytest"]
    assert len(runners) == 1
    assert runners[0]["cmd"][:3] == [sys.executable, "-m", "pytest"]


def test_ts_twin_documents_interpreter_detection():
    twin = (ROOT / "scripts" / "ts" / "qa_gate.ts").read_text(encoding="utf-8")
    source = (ROOT / "scripts" / "qa_gate.py").read_text(encoding="utf-8")
    assert 'passthrough("qa_gate")' in twin
    assert "find_spec" in twin
    assert "importlib.util.find_spec" in source
    assert 'which("pytest")' not in source
    assert "which('pytest')" not in source
