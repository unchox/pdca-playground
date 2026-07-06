"""Rubric loader contract: valid load, fail-closed on every violation class."""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".pdca"))
import rubric_loader as rl  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "rubric" / "rac-hyperv-design.yaml"


@pytest.fixture
def stub_checks(tmp_path: Path) -> Path:
    """checks/ dir containing the scripts the sample rubric references."""
    d = tmp_path / "checks"
    d.mkdir()
    (d / "section_present.py").write_text("# stub\n")
    (d / "rollback_coverage.py").write_text("# stub\n")
    return d


def _write_rubric(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "rubric.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def test_sample_rubric_loads_and_resolves_identity(stub_checks):
    rubric = rl.load_rubric(SAMPLE, checks_dir=stub_checks)
    assert rl.rubric_id(rubric) == ("rac-hyperv-design", 1)
    assert len(rubric["criteria"]) == 5
    classes = {c["id"]: c["class"] for c in rubric["criteria"]}
    assert classes["avl-05"] == "B"


def test_schema_violation_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(
        tmp_path,
        """
        domain: bad
        version: 1
        criteria:
          - id: x-01
            statement: has no class
        """,
    )
    with pytest.raises(rl.RubricError, match="schema violation"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_empty_criteria_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(tmp_path, "domain: bad\nversion: 1\ncriteria: []\n")
    with pytest.raises(rl.RubricError, match="schema violation"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_class_a_without_check_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(
        tmp_path,
        """
        domain: bad
        version: 1
        criteria:
          - id: x-01
            class: A
            statement: class A but no check
        """,
    )
    with pytest.raises(rl.RubricError, match="schema violation"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_unknown_check_reference_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(
        tmp_path,
        """
        domain: bad
        version: 1
        criteria:
          - id: x-01
            class: A
            statement: references a check that does not exist
            check: "no_such_check --path a"
        """,
    )
    with pytest.raises(rl.RubricError, match="unknown check 'no_such_check'"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_duplicate_criterion_id_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(
        tmp_path,
        """
        domain: bad
        version: 1
        criteria:
          - id: x-01
            class: A
            statement: first
            check: "section_present --path a"
          - id: x-01
            class: A
            statement: second (same id)
            check: "section_present --path b"
        """,
    )
    with pytest.raises(rl.RubricError, match="duplicate criterion id 'x-01'"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_invalid_yaml_fails_closed(tmp_path, stub_checks):
    path = _write_rubric(tmp_path, "domain: [unclosed\n")
    with pytest.raises(rl.RubricError, match="not valid YAML"):
        rl.load_rubric(path, checks_dir=stub_checks)


def test_missing_file_fails_closed(tmp_path, stub_checks):
    with pytest.raises(rl.RubricError, match="not readable"):
        rl.load_rubric(tmp_path / "nope.yaml", checks_dir=stub_checks)
