"""PDCA 'Act' controller — runs inside the GitHub Actions job after CI completes.

Responsibilities (and ONLY these):
  1. Read the CI result for the cycle that just finished.
  2. Read the OPTIONAL judge result (quality_vector / replan_requested).
  3. Record the outcome (+ judge inputs) into the persisted loop state.
  4. Ask guard.evaluate() what to do next (the deterministic decision).
  5. On CONTINUE, invoke the maker (Claude Code headless) to EDIT files.
  6. Emit a machine-readable decision for the workflow to act on.

Division of labour
------------------
- The LLM (maker) only EDITS files. It does not commit, push, or decide routing.
- git commit/push (and the token choice that gates CI re-fire) live in the
  workflow, not here -- so the "PDCA_PAT only on continue" invariant holds.
- guard.evaluate() owns every routing decision. The maker is a pure function:
  feedback in, file edits out.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cache  # noqa: E402
import guard as g  # noqa: E402
from canonical import canonical_hash  # noqa: E402

STATE_PATH = Path(os.environ.get("PDCA_STATE", ".pdca/state.json"))
CI_RESULT_PATH = Path(os.environ.get("PDCA_CI_RESULT", ".pdca/ci_result.json"))
JUDGE_RESULT_PATH = Path(os.environ.get("PDCA_JUDGE_RESULT", ".pdca/judge_result.json"))
READY_PATH = Path(os.environ.get("PDCA_READY", ".pdca/ready.json"))
RUN_META_PATH = Path(os.environ.get("PDCA_RUN_META", ".pdca/run_meta.json"))

# Maker (Claude Code headless) configuration -- all overridable via env.
# 明示 version 指定: production runtime での silent 変更防止 (2026-07-01 uchino 方針)
# alias `sonnet` を避け、 Claude Code CLI update 時の自動追従を防ぐ (reproducibility 確保)
# 移行時: 新 model release → CLAUDE.md 反映 + このデフォルト値更新 (git diff で変更追跡)
MAKER_MODEL = os.environ.get("PDCA_MAKER_MODEL", "claude-sonnet-5")
# Part of the decision-cache key: bump whenever build_maker_prompt's wording changes.
MAKER_PROMPT_VERSION = "1"
# Keep in sync with JUDGE_PROMPT_VERSION in checks/runner.py (act.py cannot
# import the runner — it needs PyYAML, which the act workflow never installs).
JUDGE_PROMPT_VERSION = os.environ.get("PDCA_JUDGE_PROMPT_VERSION", "1")
MAKER_MAX_TURNS = int(os.environ.get("PDCA_MAKER_MAX_TURNS", "30"))
MAKER_ALLOWED_TOOLS = os.environ.get("PDCA_MAKER_ALLOWED_TOOLS", "Edit,Write,Read,Grep,Glob")
MAKER_CLI = os.environ.get("PDCA_MAKER_CLI", "claude")
REPO_ROOT = Path(os.environ.get("PDCA_REPO_ROOT", "."))


# --------------------------------------------------------------------------- IO
def read_ci_result() -> dict:
    """{"outcome","failing_checks","error_kinds","summary"}. Missing => hard fail."""
    if not CI_RESULT_PATH.exists():
        return {
            "outcome": "fail",
            "failing_checks": ["__no_ci_result__"],
            "error_kinds": ["infra"],
            "summary": "CI produced no result artifact",
        }
    return json.loads(CI_RESULT_PATH.read_text(encoding="utf-8"))


def read_judge_result() -> dict:
    """Optional advisory input. Malformed fields are dropped, never trusted."""
    if not JUDGE_RESULT_PATH.exists():
        return {"quality_vector": None, "replan_requested": False}
    raw = json.loads(JUDGE_RESULT_PATH.read_text(encoding="utf-8"))
    qv = raw.get("quality_vector")
    if not (isinstance(qv, dict) and qv and all(isinstance(v, (int, float)) for v in qv.values())):
        qv = None
    return {"quality_vector": qv, "replan_requested": bool(raw.get("replan_requested", False))}


# ---------------------------------------------------------------------- feedback
def build_feedback(state: g.LoopState, ci: dict) -> dict:
    """Structured packet for the maker. The maker never sees its own prior
    reasoning -- only the contract and what is still red."""
    return {
        "cycle": state.cycle,
        "still_failing": ci.get("failing_checks", []),
        "error_kinds": ci.get("error_kinds", []),
        "summary": ci.get("summary", ""),
        "prior_signatures": [r.failure_signature for r in state.history[-state.oscillation_window:]],
    }


def build_maker_prompt(feedback: dict) -> str:
    """Pure: assemble the headless prompt. Tested directly."""
    still = "\n".join(f"  - {c}" for c in feedback.get("still_failing", [])) or "  (none reported)"
    return (
        "You are the maker in an autonomous PDCA loop. The deterministic CI gates "
        "are red. Fix ONLY what is needed to make them green.\n\n"
        f"Cycle: {feedback.get('cycle')}\n"
        f"Summary: {feedback.get('summary', '')}\n"
        "Still failing:\n"
        f"{still}\n\n"
        "Rules:\n"
        "  - Obey CLAUDE.md (the project constitution). Never weaken or delete tests "
        "or gates to make them pass.\n"
        "  - Change only what is required for the failing checks above. Do not refactor "
        "unrelated code.\n"
        "  - Edit files only. Do NOT commit or push; the harness handles git.\n"
        "  - If you cannot fix it without changing the contract/tests, make no edit and "
        "say so plainly so the loop can replan.\n"
    )


# ------------------------------------------------------------------------- maker
def run_claude_maker(prompt: str) -> dict:
    """Invoke Claude Code headless to edit files. Returns parsed JSON result.

    Credentials are read by the CLI from the environment (ANTHROPIC_API_KEY or
    CLAUDE_CODE_OAUTH_TOKEN for a Max/Team seat); never passed in code.
    """
    cmd = [
        MAKER_CLI, "-p", prompt,
        "--permission-mode", "acceptEdits",   # auto-approve edits; non-interactive
        "--max-turns", str(MAKER_MAX_TURNS),  # turn budget guardrail
        "--model", MAKER_MODEL,
        "--allowedTools", MAKER_ALLOWED_TOOLS,
        "--output-format", "json",
    ]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"claude headless failed (exit {proc.returncode}): {proc.stderr[:2000]}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout[:2000]}


def working_tree_dirty() -> bool:
    """True if the maker actually changed tracked/untracked (non-ignored) files."""
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    return bool(out.stdout.strip())


# -------------------------------------------------- decision cache (Phase 2, L4)
def _read_ready() -> dict | None:
    """ready.json (written by the admit gate, committed on the branch).
    Absent = pre-Phase-2 task: the cache layer bypasses entirely."""
    if not READY_PATH.exists():
        return None
    return json.loads(READY_PATH.read_text(encoding="utf-8"))


def _maker_cache_key(ready: dict, feedback: dict) -> str:
    """Spec §6.2 key + canonical(feedback) (2026-07-06 uchino-approved deviation:
    the spec key is cycle-invariant, so cycle >= 2 would always replay cycle 1's
    patch and livelock; feedback carries cycle/still_failing/prior_signatures)."""
    return canonical_hash(
        {
            "input_hash": canonical_hash(ready),
            "feedback": feedback,
            "model_id": MAKER_MODEL,
            "prompt_version": MAKER_PROMPT_VERSION,
            "rubric_domain": ready.get("rubric_domain"),
            "rubric_version": ready.get("rubric_version"),
        }
    )


def _porcelain_snapshot() -> dict[str, str | None]:
    """{path: content sha256 | None if gone} for every tracked/untracked
    (non-ignored) path git reports as changed."""
    out = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(REPO_ROOT), capture_output=True, text=True
    )
    snap: dict[str, str | None] = {}
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:  # rename: capture the destination
            path = path.split(" -> ")[-1]
        path = path.strip('"')
        f = REPO_ROOT / path
        snap[path] = hashlib.sha256(f.read_bytes()).hexdigest() if f.is_file() else None
    return snap


def _snapshot_delta(before: dict, after: dict) -> dict[str, str | None] | None:
    """{path: full content | None(deleted)} for what the maker changed.
    Returns None (= do not cache) if any changed file is not utf-8 text —
    a partial record would replay a partial patch, which is worse than a miss."""
    files: dict[str, str | None] = {}
    for path, digest in after.items():
        if path in before and before[path] == digest:
            continue
        if digest is None:
            files[path] = None
            continue
        try:
            files[path] = (REPO_ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return None
    return files


def _replay_files(files: dict[str, str | None]) -> None:
    for path, content in files.items():
        f = REPO_ROOT / path
        if content is None:
            f.unlink(missing_ok=True)
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content, encoding="utf-8")


def _write_run_meta(ready: dict) -> None:
    """Sidecar for spec §6.4's keys — state.json cannot take them (guard.py's
    loader TypeErrors on unknown keys and is INV-1-protected). Not gitignored:
    the continue step's `git add -A` turns it into a free audit trail."""
    meta = {
        "model_id": MAKER_MODEL,
        "prompt_version": MAKER_PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
        "rubric_domain": ready.get("rubric_domain"),
        "rubric_version": ready.get("rubric_version"),
        "input_hash": canonical_hash(ready),
    }
    RUN_META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_maker_uncached(feedback: dict) -> dict:
    """Original maker path — invoke the CLI, record telemetry, demand a change."""
    result = run_claude_maker(build_maker_prompt(feedback))
    telemetry = {
        "session_id": result.get("session_id"),
        "total_cost_usd": result.get("total_cost_usd"),
        "num_turns": result.get("num_turns"),
    }
    Path(".pdca/maker_last.json").write_text(json.dumps(telemetry, ensure_ascii=False, indent=2), encoding="utf-8")
    if not working_tree_dirty():
        raise RuntimeError("maker produced no file changes; halting to avoid an empty cycle")
    return telemetry


def call_maker(feedback: dict) -> dict:
    """Edit files to satisfy the failing checks. Edits only -- no git ops here.

    Raises if the maker produced no change, so a stuck maker fails loudly rather
    than silently spinning the loop.

    Entry-point decision cache (spec §6.2): with a ready.json present, a cache
    hit replays the stored patch without calling the LLM; a miss runs the maker
    and stores {files, telemetry}. Without ready.json the original path runs
    untouched. CacheCorruption propagates — fail-closed, never recompute over
    a broken audit record.
    """
    Path(".pdca/last_feedback.json").write_text(json.dumps(feedback, ensure_ascii=False, indent=2), encoding="utf-8")

    ready = _read_ready()
    if ready is None:
        return _run_maker_uncached(feedback)

    key = _maker_cache_key(ready, feedback)
    record = cache.get("decisions", key)
    if record is not None:
        _replay_files(record.get("files", {}))
        telemetry = dict(record.get("telemetry") or {})
        telemetry["replayed"] = True
        Path(".pdca/maker_last.json").write_text(
            json.dumps(telemetry, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _write_run_meta(ready)
        if not working_tree_dirty():
            raise RuntimeError("maker produced no file changes; halting to avoid an empty cycle")
        return telemetry

    before = _porcelain_snapshot()
    telemetry = _run_maker_uncached(feedback)
    after = _porcelain_snapshot()
    files = _snapshot_delta(before, after)
    if files:  # None (uncacheable) or {} (nothing attributable) both skip the put
        cache.put(
            "decisions",
            key,
            {
                "key_material": {
                    "input_hash": canonical_hash(ready),
                    "feedback": feedback,
                    "model_id": MAKER_MODEL,
                    "prompt_version": MAKER_PROMPT_VERSION,
                    "rubric_domain": ready.get("rubric_domain"),
                    "rubric_version": ready.get("rubric_version"),
                },
                "telemetry": telemetry,
                "files": files,
                "created_ts": datetime.now(timezone.utc).isoformat(),
            },
        )
    _write_run_meta(ready)
    return telemetry


# -------------------------------------------------------------------------- emit
def emit(decision: g.Decision, reason: str) -> None:
    line = f"decision={decision.value}\nreason={reason}\n"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(line)
    print(line, end="")


def main() -> int:
    ci = read_ci_result()
    judge = read_judge_result()
    state = g.LoopState.load(STATE_PATH) if STATE_PATH.exists() else g.LoopState(task_id=os.environ.get("PDCA_TASK", "T1"))

    sig = None
    if ci.get("outcome") != "pass":
        sig = g.compute_failure_signature(ci.get("failing_checks", []), ci.get("error_kinds"))

    g.record_outcome(
        state,
        ci.get("outcome", "fail"),
        sig,
        ci.get("summary", ""),
        quality_vector=judge["quality_vector"],
        replan_requested=judge["replan_requested"],
    )

    decision, reason = g.evaluate(state)
    g.apply_decision(state, decision)
    state.save(STATE_PATH)
    emit(decision, reason)

    if decision is g.Decision.CONTINUE:
        # Maker edits files in place. The workflow stages + commits + pushes
        # (with PDCA_PAT) so CI re-fires for the next cycle.
        call_maker(build_feedback(state, ci))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
