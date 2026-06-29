"""Deterministic tests for the loop guard/router.

These ARE the kind of oracle the Plan agent is meant to produce: a machine
decides pass/fail with no judgement. The guard that stops the loop is itself
guarded. Run: pytest -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".pdca"))

import guard as g  # noqa: E402


def _state(**kw):
    base = dict(
        task_id="T1",
        max_cycles=5,
        oscillation_threshold=3,
        oscillation_window=5,
        no_progress_threshold=2,
        min_quality_delta=0.01,
    )
    base.update(kw)
    return g.LoopState(**base)


# ---- signature ----------------------------------------------------------------
def test_signature_is_order_independent():
    assert g.compute_failure_signature(["b", "a"]) == g.compute_failure_signature(["a", "b"])


def test_signature_distinguishes_different_failures():
    assert g.compute_failure_signature(["a"]) != g.compute_failure_signature(["b"])


# ---- core routing -------------------------------------------------------------
def test_pass_completes():
    s = _state()
    g.record_outcome(s, "pass")
    assert g.evaluate(s)[0] is g.Decision.COMPLETED


def test_single_failure_continues():
    s = _state()
    g.record_outcome(s, "fail", "sigA")
    assert g.evaluate(s)[0] is g.Decision.CONTINUE


def test_same_failure_three_times_is_oscillation():
    s = _state()
    for _ in range(3):
        g.record_outcome(s, "fail", "sigA")
    d, why = g.evaluate(s)
    assert d is g.Decision.STOP_OSCILLATION, why


def test_alternating_failures_flapping_is_oscillation():
    s = _state()
    for sig in ["A", "B", "A", "B", "A"]:
        g.record_outcome(s, "fail", sig)
    assert g.evaluate(s)[0] is g.Decision.STOP_OSCILLATION


def test_distinct_failures_hit_max_not_oscillation():
    s = _state()
    for sig in ["s1", "s2", "s3", "s4", "s5"]:
        g.record_outcome(s, "fail", sig)
    assert g.evaluate(s)[0] is g.Decision.STOP_MAX


def test_budget_exhaustion():
    s = _state(max_cycles=2)
    g.record_outcome(s, "fail", "s1")
    g.record_outcome(s, "fail", "s2")
    assert g.evaluate(s)[0] is g.Decision.STOP_MAX


# ---- replan -------------------------------------------------------------------
def test_replan_requested_routes_to_replan():
    s = _state()
    g.record_outcome(s, "fail", "sigA", replan_requested=True)
    assert g.evaluate(s)[0] is g.Decision.REPLAN


def test_replan_takes_priority_over_oscillation():
    # Same failure 3x would be oscillation, but a replan request on the latest
    # cycle wins -- a wrong contract must not be relabeled as "stuck".
    s = _state()
    g.record_outcome(s, "fail", "sigA")
    g.record_outcome(s, "fail", "sigA")
    g.record_outcome(s, "fail", "sigA", replan_requested=True)
    assert g.evaluate(s)[0] is g.Decision.REPLAN


def test_replan_is_terminal_and_idempotent():
    s = _state()
    g.record_outcome(s, "fail", "sigA", replan_requested=True)
    d = g.evaluate(s)[0]
    g.apply_decision(s, d)
    assert s.status == "replan"
    assert g.evaluate(s)[0] is g.Decision.ALREADY_TERMINAL


# ---- quality / no-progress ----------------------------------------------------
def test_improving_quality_continues():
    s = _state(max_cycles=10)
    g.record_outcome(s, "fail", "sigA", quality_vector={"coverage": 0.50})
    g.record_outcome(s, "fail", "sigB", quality_vector={"coverage": 0.70})
    g.record_outcome(s, "fail", "sigC", quality_vector={"coverage": 0.90})
    assert g.evaluate(s)[0] is g.Decision.CONTINUE


def test_stalled_quality_stops_no_progress():
    s = _state(max_cycles=10)
    g.record_outcome(s, "fail", "sigA", quality_vector={"coverage": 0.80})
    g.record_outcome(s, "fail", "sigB", quality_vector={"coverage": 0.80})
    g.record_outcome(s, "fail", "sigC", quality_vector={"coverage": 0.805})
    d, why = g.evaluate(s)
    assert d is g.Decision.STOP_NO_PROGRESS, why


def test_no_quality_data_does_not_trigger_no_progress():
    s = _state(max_cycles=10)
    for sig in ["s1", "s2", "s3"]:
        g.record_outcome(s, "fail", sig)
    assert g.evaluate(s)[0] is g.Decision.CONTINUE


# ---- idempotency / persistence ------------------------------------------------
def test_terminal_state_is_idempotent():
    s = _state()
    g.record_outcome(s, "pass")
    g.apply_decision(s, g.evaluate(s)[0])
    assert g.evaluate(s)[0] is g.Decision.ALREADY_TERMINAL


def test_roundtrip_persistence(tmp_path):
    s = _state()
    g.record_outcome(s, "fail", "sigA", summary="2 red", quality_vector={"coverage": 0.6}, replan_requested=False)
    p = tmp_path / "state.json"
    s.save(p)
    loaded = g.LoopState.load(p)
    assert loaded.cycle == 1
    assert loaded.history[-1].failure_signature == "sigA"
    assert loaded.history[-1].quality_vector == {"coverage": 0.6}
    assert loaded.history[-1].replan_requested is False
