"""PDCA loop guard/router: the deterministic decision core.

This is the one piece that does NOT live in GitHub's machinery. It reads the
persisted loop state and decides the next route. It is a *pure* function over
state so the same input always yields the same decision -- that is where the
loop's reproducibility comes from.

Control-vs-advice invariant
---------------------------
LLM workers (maker, judge) never decide the route. A judge may emit a
`quality_vector` (normalized metrics, higher = better) and/or set
`replan_requested`. Those are recorded as *inputs*; guard.evaluate() makes the
final routing call. If you find an LLM choosing continue/completed/stopped_*
directly, the invariant is broken.

Other design notes
-------------------
- failure_signature is a STABLE hash of the *failure mode*, not the raw log.
  Hashing raw logs would make every cycle unique and oscillation never trigger.
- Oscillation uses a sliding WINDOW (not strict consecutiveness) so A,B,A,B,A
  flapping is caught alongside A,A,A.
- No-progress uses the quality_vector aggregate over a window; only evaluated
  when quality data is present.
- evaluate() never mutates state. record_outcome() is the only mutator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2


class Decision(str, Enum):
    CONTINUE = "continue"                     # gates red, budget + progress remain -> next cycle
    COMPLETED = "completed"                   # gates green -> done
    REPLAN = "replan"                         # contract/oracles look wrong -> human plan gate
    STOP_MAX = "stopped_max"                  # cycle budget exhausted -> escalate
    STOP_OSCILLATION = "stopped_oscillation"  # same failure recurring -> escalate
    STOP_NO_PROGRESS = "stopped_no_progress"  # quality stalled across cycles -> escalate
    ALREADY_TERMINAL = "already_terminal"     # idempotency: state already finished


# Every route that ends the current loop. REPLAN is terminal for THIS loop: a
# revised plan must be human-approved before a new loop starts.
TERMINAL_STATUSES = {
    Decision.COMPLETED.value,
    Decision.REPLAN.value,
    Decision.STOP_MAX.value,
    Decision.STOP_OSCILLATION.value,
    Decision.STOP_NO_PROGRESS.value,
}


@dataclass
class CycleRecord:
    cycle: int
    outcome: str                              # "pass" | "fail"
    failure_signature: str | None
    summary: str = ""
    quality_vector: dict[str, float] | None = None  # judge input, normalized higher=better
    replan_requested: bool = False                  # judge input, advisory only
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "outcome": self.outcome,
            "failure_signature": self.failure_signature,
            "summary": self.summary,
            "quality_vector": self.quality_vector,
            "replan_requested": self.replan_requested,
            "ts": self.ts,
        }


@dataclass
class LoopState:
    task_id: str
    status: str = "running"
    cycle: int = 0
    max_cycles: int = 5
    oscillation_threshold: int = 3
    oscillation_window: int = 5
    no_progress_threshold: int = 2            # consecutive quality-bearing cycles w/o gain
    min_quality_delta: float = 0.01           # min aggregate gain to count as progress
    history: list[CycleRecord] = field(default_factory=list)
    version: int = SCHEMA_VERSION

    @classmethod
    def load(cls, path: str | Path) -> "LoopState":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        history = [CycleRecord(**rec) for rec in data.pop("history", [])]
        return cls(history=history, **data)

    def save(self, path: str | Path) -> None:
        payload = {
            "version": self.version,
            "task_id": self.task_id,
            "status": self.status,
            "cycle": self.cycle,
            "max_cycles": self.max_cycles,
            "oscillation_threshold": self.oscillation_threshold,
            "oscillation_window": self.oscillation_window,
            "no_progress_threshold": self.no_progress_threshold,
            "min_quality_delta": self.min_quality_delta,
            "history": [r.to_dict() for r in self.history],
        }
        Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def compute_failure_signature(failing_checks: list[str], error_kinds: list[str] | None = None) -> str:
    """Stable signature of a failure mode. Pass failing check ids, NOT log text."""
    parts = sorted(set(failing_checks))
    if error_kinds:
        parts += [f"!{k}" for k in sorted(set(error_kinds))]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def aggregate_quality(qv: dict[str, float] | None) -> float | None:
    """Single normalized score from a quality vector (mean; higher = better)."""
    if not qv:
        return None
    return sum(qv.values()) / len(qv)


def record_outcome(
    state: LoopState,
    outcome: str,
    failure_signature: str | None = None,
    summary: str = "",
    quality_vector: dict[str, float] | None = None,
    replan_requested: bool = False,
) -> LoopState:
    """Append the just-finished cycle's result. The only mutator of history.

    quality_vector and replan_requested are advisory inputs from a judge; they
    are stored, never acted on directly. evaluate() owns the routing.
    """
    state.cycle += 1
    state.history.append(
        CycleRecord(
            cycle=state.cycle,
            outcome=outcome,
            failure_signature=failure_signature,
            summary=summary,
            quality_vector=quality_vector,
            replan_requested=bool(replan_requested),
        )
    )
    return state


def _is_stalled(state: LoopState) -> bool:
    """True if quality aggregate has not gained >= min_quality_delta across the
    last (no_progress_threshold + 1) cycles that carry a quality_vector."""
    aggs = [
        aggregate_quality(r.quality_vector)
        for r in state.history
        if r.quality_vector is not None
    ]
    aggs = [a for a in aggs if a is not None]
    span = state.no_progress_threshold + 1
    if len(aggs) < span:
        return False
    window = aggs[-span:]
    return (window[-1] - window[0]) < state.min_quality_delta


def evaluate(state: LoopState) -> tuple[Decision, str]:
    """Pure routing decision. Never mutates state.

    Order: terminal -> empty -> pass -> replan(explicit) -> oscillation ->
    no-progress -> budget -> continue.
    """
    if state.status in TERMINAL_STATUSES:
        return Decision.ALREADY_TERMINAL, f"state already terminal: {state.status}"

    if not state.history:
        return Decision.CONTINUE, "no cycles recorded yet"

    latest = state.history[-1]

    # 1. Success wins immediately.
    if latest.outcome == "pass":
        return Decision.COMPLETED, "all gates green"

    # 2. Explicit judge signal that the contract is wrong -> replan (terminal,
    #    human plan gate). Taken before oscillation so a wrong-contract loop is
    #    not silently relabeled as "stuck".
    if latest.replan_requested:
        return Decision.REPLAN, "judge requested replan (contract/oracles look wrong)"

    # 3. Oscillation: latest failure signature recurs within the window.
    sig = latest.failure_signature
    if sig is not None:
        window = state.history[-state.oscillation_window:]
        repeats = sum(1 for r in window if r.failure_signature == sig)
        if repeats >= state.oscillation_threshold:
            return (
                Decision.STOP_OSCILLATION,
                f"failure signature {sig} seen {repeats}x within last "
                f"{len(window)} cycles (threshold {state.oscillation_threshold})",
            )

    # 4. Quality stall (only if quality data is present).
    if _is_stalled(state):
        return (
            Decision.STOP_NO_PROGRESS,
            f"quality aggregate gained < {state.min_quality_delta} over last "
            f"{state.no_progress_threshold + 1} judged cycles",
        )

    # 5. Budget exhausted.
    if state.cycle >= state.max_cycles:
        return Decision.STOP_MAX, f"cycle budget exhausted ({state.cycle}/{state.max_cycles})"

    # 6. Otherwise keep going.
    return Decision.CONTINUE, f"gates red, budget remains ({state.cycle}/{state.max_cycles})"


def apply_decision(state: LoopState, decision: Decision) -> LoopState:
    """Persist terminal decisions into status (idempotency)."""
    if decision.value in TERMINAL_STATUSES:
        state.status = decision.value
    return state
