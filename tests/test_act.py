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


def test_maker_env_drops_api_key_when_oauth_present(monkeypatch):
    """Billing separation: the judge's ANTHROPIC_API_KEY must never reach the maker
    CLI when the Max-seat OAuth token is configured."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "judge-key")
    captured = {}

    class FakeProc:
        returncode = 0
        stdout = '{"session_id": "s"}'
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return FakeProc()

    monkeypatch.setattr(act.subprocess, "run", fake_run)
    act.run_claude_maker("prompt")
    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert captured["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token"


def test_one_line_collapses_and_truncates():
    assert act._one_line("a\nb\t c") == "a b c"
    assert act._one_line("x" * 400, limit=10) == "x" * 10 + "..."


def test_noop_marker_written_when_maker_makes_no_changes(monkeypatch, tmp_path):
    """A deliberate no-edit by the maker must leave a committed marker, not crash."""
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    Path(".pdca/ready.json").write_text(
        json.dumps({"task_id": "T", "domain": "d", "goal": "g", "constraints": [],
                    "artifact_target": "a.yaml", "rubric_domain": "d",
                    "rubric_version": 1, "enrichment_log": []})
    )
    monkeypatch.setattr(act, "READY_PATH", Path(".pdca/ready.json"))
    monkeypatch.setattr(act, "RUN_META_PATH", Path(".pdca/run_meta.json"))
    monkeypatch.setattr(act, "NOOP_MARKER_PATH", Path(".pdca/maker_noop.json"))
    monkeypatch.setattr(act, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(act, "_porcelain_snapshot", lambda: {})
    monkeypatch.setattr(act, "working_tree_dirty", lambda: True)
    monkeypatch.setattr(
        act, "run_claude_maker",
        lambda prompt: {"session_id": "s", "result": "CP-114 contradicts sto-02; cannot fix"},
    )

    act.call_maker({"cycle": 1, "still_failing": ["t"], "summary": ""})

    marker = json.loads(Path(".pdca/maker_noop.json").read_text())
    assert "contradicts" in marker["diagnosis"]
    assert marker["cycle"] == 1
    assert not (tmp_path / "store" / "decisions").exists()  # empty delta is never cached


def test_consume_maker_noop_reads_and_deletes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    monkeypatch.setattr(act, "NOOP_MARKER_PATH", Path(".pdca/maker_noop.json"))
    Path(".pdca/maker_noop.json").write_text(json.dumps({"cycle": 1, "diagnosis": "why"}))
    marker = act._consume_maker_noop()
    assert marker["diagnosis"] == "why"
    assert not Path(".pdca/maker_noop.json").exists()
    assert act._consume_maker_noop() is None


def test_main_routes_noop_to_replan_with_diagnosis(monkeypatch, tmp_path, capsys):
    """cycle N maker no-edit -> cycle N+1 act must emit replan (human plan gate)."""
    monkeypatch.chdir(tmp_path)
    Path(".pdca").mkdir()
    Path(".pdca/ci_result.json").write_text(json.dumps({
        "outcome": "fail",
        "failing_checks": ["tests/test_x.py::test_policy"],
        "error_kinds": ["tests"],
        "summary": "1 gate group(s) red",
    }))
    Path(".pdca/maker_noop.json").write_text(json.dumps({
        "cycle": 1, "diagnosis": "cost policy CP-114 and rubric sto-02\nare mutually exclusive",
    }))
    monkeypatch.setattr(act, "STATE_PATH", Path(".pdca/state.json"))
    monkeypatch.setattr(act, "CI_RESULT_PATH", Path(".pdca/ci_result.json"))
    monkeypatch.setattr(act, "JUDGE_RESULT_PATH", Path(".pdca/judge_result.json"))
    monkeypatch.setattr(act, "NOOP_MARKER_PATH", Path(".pdca/maker_noop.json"))
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.setattr(
        act, "call_maker",
        lambda fb: (_ for _ in ()).throw(AssertionError("maker must not run on replan")),
    )

    assert act.main() == 0

    out = capsys.readouterr().out
    assert "decision=replan" in out
    assert "maker diagnosis: cost policy CP-114 and rubric sto-02 are mutually exclusive" in out
    state = json.loads(Path(".pdca/state.json").read_text())
    assert state["status"] == "replan"
    assert state["history"][-1]["replan_requested"] is True
