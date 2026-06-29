"""Tests for the Act controller's maker wiring. Claude Code and git are mocked
so these run offline and deterministically."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".pdca"))

import act  # noqa: E402


def test_build_maker_prompt_lists_failing_checks():
    p = act.build_maker_prompt({"cycle": 3, "summary": "2 red", "still_failing": ["tests/x.py::test_a", "lint:ruff"]})
    assert "tests/x.py::test_a" in p
    assert "lint:ruff" in p
    # must instruct edit-only and forbid weakening tests
    assert "Do NOT commit" in p
    assert "Never weaken" in p


def test_build_maker_prompt_handles_no_failing():
    p = act.build_maker_prompt({"cycle": 1, "summary": "", "still_failing": []})
    assert "(none reported)" in p


def test_call_maker_raises_when_no_changes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    monkeypatch.setattr(act, "run_claude_maker", lambda prompt: {"session_id": "s", "total_cost_usd": 0.0, "num_turns": 1})
    monkeypatch.setattr(act, "working_tree_dirty", lambda: False)
    with pytest.raises(RuntimeError, match="no file changes"):
        act.call_maker({"cycle": 1, "still_failing": ["t"], "summary": ""})


def test_call_maker_succeeds_and_writes_telemetry(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    monkeypatch.setattr(act, "run_claude_maker", lambda prompt: {"session_id": "abc", "total_cost_usd": 0.12, "num_turns": 4})
    monkeypatch.setattr(act, "working_tree_dirty", lambda: True)
    tel = act.call_maker({"cycle": 2, "still_failing": ["t"], "summary": ""})
    assert tel["session_id"] == "abc"
    saved = json.loads(Path(".pdca/maker_last.json").read_text())
    assert saved["total_cost_usd"] == 0.12


def test_run_claude_maker_raises_on_nonzero(monkeypatch):
    class FakeProc:
        returncode = 2
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(act.subprocess, "run", lambda *a, **k: FakeProc())
    with pytest.raises(RuntimeError, match="exit 2"):
        act.run_claude_maker("prompt")


def test_read_judge_result_drops_malformed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    Path(".pdca/judge_result.json").write_text(json.dumps({"quality_vector": {"x": "high"}, "replan_requested": 1}))
    monkeypatch.setattr(act, "JUDGE_RESULT_PATH", Path(".pdca/judge_result.json"))
    out = act.read_judge_result()
    assert out["quality_vector"] is None          # non-numeric dropped
    assert out["replan_requested"] is True         # coerced to bool
