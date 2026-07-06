"""Admit gate contract: pass / deterministic enrich / REJECTED / hash stability / drift."""

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".pdca"))
import admit  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

GOOD_INTAKE = {
    "task_id": "T1",
    "domain": "rac-hyperv-design",
    "goal": "design a 2-node RAC on Hyper-V",
    "constraints": ["no shared nothing"],
    "artifact_target": "design/rac.yaml",
}


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Minimal repo root: real generic schema + real sample rubric + empty .pdca."""
    (tmp_path / "schemas").mkdir()
    shutil.copy(REPO_ROOT / "schemas" / "ready.schema.json", tmp_path / "schemas")
    (tmp_path / "rubric").mkdir()
    shutil.copy(REPO_ROOT / "rubric" / "rac-hyperv-design.yaml", tmp_path / "rubric")
    (tmp_path / ".pdca").mkdir()
    return tmp_path


def _run(repo: Path, intake: dict | None, extra: list[str] | None = None) -> int:
    if intake is not None:
        (repo / ".pdca" / "intake.json").write_text(json.dumps(intake), encoding="utf-8")
    argv = [
        "--intake", str(repo / ".pdca" / "intake.json"),
        "--out", str(repo / ".pdca" / "ready.json"),
        "--repo-root", str(repo),
    ]
    return admit.main(argv + (extra or []))


def test_pass_writes_ready_with_rubric_identity(repo, capsys):
    assert _run(repo, GOOD_INTAKE) == 0
    ready = json.loads((repo / ".pdca" / "ready.json").read_text())
    assert ready["rubric_domain"] == "rac-hyperv-design"
    assert ready["rubric_version"] == 1
    assert ready["enrichment_log"] == []
    out = capsys.readouterr().out
    assert "input_hash=" in out
    assert len(out.split("input_hash=")[1].strip()) == 64


def test_enrich_fills_from_plan_yaml_and_logs_sources(repo):
    (repo / ".pdca" / "plan.yaml").write_text(
        "plan:\n  goal: enrich me\n  tasks:\n    - id: T9\n", encoding="utf-8"
    )
    intake = {k: v for k, v in GOOD_INTAKE.items() if k not in ("goal", "task_id")}
    assert _run(repo, intake) == 0
    ready = json.loads((repo / ".pdca" / "ready.json").read_text())
    assert ready["goal"] == "enrich me"
    assert ready["task_id"] == "T9"
    sources = {e["key"]: e["source"] for e in ready["enrichment_log"]}
    assert sources == {
        "goal": ".pdca/plan.yaml:plan.goal",
        "task_id": ".pdca/plan.yaml:plan.tasks[0].id",
    }


def test_rejected_lists_missing_keys(repo):
    intake = {k: v for k, v in GOOD_INTAKE.items() if k != "artifact_target"}
    assert _run(repo, intake) == 1
    report = (repo / ".pdca" / "admit_rejected.txt").read_text()
    assert "artifact_target" in report
    assert not (repo / ".pdca" / "ready.json").exists()


def test_missing_intake_is_rejected(repo):
    assert _run(repo, None) == 1
    assert "intake file not found" in (repo / ".pdca" / "admit_rejected.txt").read_text()


def test_unknown_domain_is_rejected(repo):
    assert _run(repo, {**GOOD_INTAKE, "domain": "no-such-domain"}) == 1
    assert "rubric unusable" in (repo / ".pdca" / "admit_rejected.txt").read_text()


def test_hash_is_stable_across_key_order(repo, tmp_path):
    assert _run(repo, GOOD_INTAKE) == 0
    first = json.loads((repo / ".pdca" / "ready.json").read_text())

    reordered = dict(reversed(list(GOOD_INTAKE.items())))
    (repo / ".pdca" / "intake.json").write_text(json.dumps(reordered), encoding="utf-8")
    assert _run(repo, None, extra=["--force"]) == 0
    second = json.loads((repo / ".pdca" / "ready.json").read_text())

    assert admit.canonical_hash(first) == admit.canonical_hash(second)


def test_drift_between_committed_ready_and_intake_is_rejected(repo):
    assert _run(repo, GOOD_INTAKE) == 0
    assert _run(repo, {**GOOD_INTAKE, "goal": "silently changed"}) == 1
    assert "drift" in (repo / ".pdca" / "admit_rejected.txt").read_text()
    # --force is the explicit local regeneration escape hatch
    assert _run(repo, None, extra=["--force"]) == 0


def test_github_output_receives_input_hash(repo, monkeypatch, tmp_path):
    gh_out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(gh_out))
    assert _run(repo, GOOD_INTAKE) == 0
    assert gh_out.read_text().startswith("input_hash=")
