"""Runner contract: ci_result-compatible output, fail-closed dispatch, judge handling."""

import json
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".pdca"))
sys.path.insert(0, str(REPO_ROOT / "checks"))
import ci_report  # noqa: E402
import runner  # noqa: E402
import rubric_loader as rl  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"
SAMPLE_RUBRIC = REPO_ROOT / "rubric" / "rac-hyperv-design.yaml"


def _load(name: str) -> dict:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def rubric() -> dict:
    return rl.load_rubric(SAMPLE_RUBRIC, checks_dir=REPO_ROOT / "checks")


def _valid_verdict_stdout(addressed: bool = True) -> str:
    verdict = {
        "criterion_id": "avl-05",
        "items": [
            {"id": "fs-node-loss", "addressed": addressed, "gap": "" if addressed else "silent"},
            {"id": "fs-net-partition", "addressed": True, "gap": "covered"},
            {"id": "fs-storage-loss", "addressed": True, "gap": "covered"},
        ],
    }
    return json.dumps({"result": json.dumps(verdict)})


def test_all_class_a_pass_on_green_fixture(rubric):
    result = runner.run_rubric(rubric, _load("design_green.yaml"), skip_judge=True)
    assert result["outcome"] == "pass"
    assert result["failing_checks"] == []
    assert result["error_kinds"] == []
    assert "1 class-B skipped" in result["summary"]


def test_partial_fail_emits_ci_result_compatible_shape(rubric):
    result = runner.run_rubric(rubric, _load("design_fail.yaml"), skip_judge=True)
    assert result["outcome"] == "fail"
    assert set(result["failing_checks"]) == {"net-01", "sto-02", "avl-03", "ops-04"}
    assert result["error_kinds"] == ["rubric:A"]
    assert set(result) == {"outcome", "failing_checks", "error_kinds", "summary"}


def test_unknown_check_fails_closed(rubric):
    bad = json.loads(json.dumps(rubric))  # deep copy
    bad["criteria"][0]["check"] = "no_such_plugin --path networks"
    with pytest.raises(runner.RunnerError, match="unknown check"):
        runner.run_rubric(bad, _load("design_green.yaml"), skip_judge=True)


def test_judge_pass_via_mock(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(runner, "_invoke_judge_cli", lambda prompt: _valid_verdict_stdout())
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert result["outcome"] == "pass"


def test_judge_unaddressed_item_fails_criterion(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        runner, "_invoke_judge_cli", lambda prompt: _valid_verdict_stdout(addressed=False)
    )
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert "avl-05" in result["failing_checks"]
    assert "rubric:B" in result["error_kinds"]
    assert "fs-node-loss" in result["summary"]


def test_judge_invalid_then_valid_retries_once(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    outputs = iter(["not json at all", _valid_verdict_stdout()])
    monkeypatch.setattr(runner, "_invoke_judge_cli", lambda prompt: next(outputs))
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert result["outcome"] == "pass"


def test_judge_invalid_twice_fails_closed(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(runner, "_invoke_judge_cli", lambda prompt: "garbage")
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert "avl-05" in result["failing_checks"]
    assert "judge output invalid" in result["summary"]


def test_judge_without_credentials_fails_closed(rubric, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    called = []
    monkeypatch.setattr(runner, "_invoke_judge_cli", lambda prompt: called.append(1))
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert called == []
    assert "avl-05" in result["failing_checks"]
    assert "judge unavailable" in result["summary"]


def test_verdict_cache_seam_replays_without_judge_call(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    store: dict = {}
    monkeypatch.setattr(
        runner, "_invoke_judge_cli",
        lambda prompt: (_ for _ in ()).throw(AssertionError("judge must not run on hit")),
    )
    store[("verdicts", "prefill")] = None  # placeholder so dict is non-empty

    def cache_get(kind, key):
        return store.get((kind, key))

    hits = {"key": None}

    def prefill(kind, key, value):
        store[(kind, key)] = value
        hits["key"] = key

    # First run with a working judge fills the cache...
    monkeypatch.setattr(runner, "_invoke_judge_cli", lambda prompt: _valid_verdict_stdout())
    first = runner.run_rubric(
        rubric, _load("design_green.yaml"), cache_get=cache_get, cache_put=prefill
    )
    assert first["outcome"] == "pass" and hits["key"]

    # ...second run must replay from cache without any judge invocation.
    monkeypatch.setattr(
        runner, "_invoke_judge_cli",
        lambda prompt: (_ for _ in ()).throw(AssertionError("judge must not run on hit")),
    )
    second = runner.run_rubric(
        rubric, _load("design_green.yaml"), cache_get=cache_get, cache_put=prefill
    )
    assert second["outcome"] == "pass"


def test_main_end_to_end_green_and_fail(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # ensure skip-judge path is explicit
    out = tmp_path / "rubric_result.json"
    rc_green = runner.main(
        ["--rubric", str(SAMPLE_RUBRIC), "--artifact", str(FIXTURES / "design_green.yaml"),
         "--out", str(out), "--skip-judge"]
    )
    assert rc_green == 0
    assert json.loads(out.read_text())["outcome"] == "pass"

    rc_fail = runner.main(
        ["--rubric", str(SAMPLE_RUBRIC), "--artifact", str(FIXTURES / "design_fail.yaml"),
         "--out", str(out), "--skip-judge"]
    )
    assert rc_fail == 1
    result = json.loads(out.read_text())
    assert result["outcome"] == "fail"
    assert "net-01" in result["failing_checks"]


def test_main_missing_artifact_is_infra_failure(tmp_path):
    out = tmp_path / "rubric_result.json"
    rc = runner.main(
        ["--rubric", str(SAMPLE_RUBRIC), "--artifact", str(tmp_path / "nope.yaml"),
         "--out", str(out), "--skip-judge"]
    )
    assert rc == 1
    result = json.loads(out.read_text())
    assert result["failing_checks"] == ["__rubric_infra__"]
    assert result["error_kinds"] == ["infra"]


def test_ci_report_merges_rubric_result(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdca = tmp_path / ".pdca"
    pdca.mkdir()
    (pdca / "ruff_exit.txt").write_text("0")
    (pdca / "pytest_exit.txt").write_text("0")
    (pdca / "rubric_result.json").write_text(
        json.dumps(
            {
                "outcome": "fail",
                "failing_checks": ["net-01"],
                "error_kinds": ["rubric:A"],
                "summary": "1 criteria red",
            }
        )
    )
    result = ci_report.build()
    assert result["outcome"] == "fail"
    assert result["failing_checks"] == ["net-01"]
    assert result["error_kinds"] == ["rubric:A"]


def test_ci_report_corrupt_rubric_result_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    pdca = tmp_path / ".pdca"
    pdca.mkdir()
    (pdca / "ruff_exit.txt").write_text("0")
    (pdca / "pytest_exit.txt").write_text("0")
    (pdca / "rubric_result.json").write_text("{corrupt")
    result = ci_report.build()
    assert result["outcome"] == "fail"
    assert "__rubric_infra__" in result["failing_checks"]
    assert "infra" in result["error_kinds"]


def test_ci_report_missing_rubric_result_with_ready_fails_closed(tmp_path, monkeypatch):
    """ready.json committed = rubric REQUIRED; a crashed runner must not pass silently."""
    monkeypatch.chdir(tmp_path)
    pdca = tmp_path / ".pdca"
    pdca.mkdir()
    (pdca / "ruff_exit.txt").write_text("0")
    (pdca / "pytest_exit.txt").write_text("0")
    (pdca / "ready.json").write_text("{}")
    result = ci_report.build()
    assert result["outcome"] == "fail"
    assert "__rubric_infra__" in result["failing_checks"]


def test_judge_cli_missing_binary_fails_closed(rubric, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def raise_oserror(prompt):
        raise FileNotFoundError("claude: command not found")

    monkeypatch.setattr(runner, "_invoke_judge_cli", raise_oserror)
    result = runner.run_rubric(rubric, _load("design_green.yaml"))
    assert "avl-05" in result["failing_checks"]
    assert "judge output invalid" in result["summary"]


def test_ci_report_propagates_rubric_gaps_into_summary(tmp_path, monkeypatch):
    """The maker's feedback must say WHY a criterion failed, not just its id."""
    monkeypatch.chdir(tmp_path)
    pdca = tmp_path / ".pdca"
    pdca.mkdir()
    (pdca / "ruff_exit.txt").write_text("0")
    (pdca / "pytest_exit.txt").write_text("0")
    (pdca / "rubric_result.json").write_text(json.dumps({
        "outcome": "fail",
        "failing_checks": ["net-01"],
        "error_kinds": ["rubric:A"],
        "summary": "rubric d v1: 1 criteria red | gaps: net-01: vlans are not distinct",
    }))
    result = ci_report.build()
    assert "gaps: net-01: vlans are not distinct" in result["summary"]
