---
name: pdca-init
description: >
  Scaffold the autonomous PDCA loop into the current project. Use when the user
  wants to set up multi-agent PDCA automation, "PDCA を組み込む", initialize the
  self-driving GitHub-Actions loop, or create a new project with the autonomous
  build/check/act machinery. Installs .pdca/ (guard + act + state), the CI and
  pdca-act workflows, the planner subagent, and a CLAUDE.md section.
---

# PDCA loop initializer

This skill installs an autonomous PDCA loop whose control plane is GitHub
(state, isolation, gates, audit) and whose only self-owned logic is the
stop-judgment guard. Run it at project creation time.

## What it installs

```
.pdca/
  guard.py            # stop-judgment logic (pure, tested) — the one piece not delegated to GitHub
  act.py              # Act controller: interpret CI -> guard -> route
  state.json          # seed loop state
  state.schema.json   # state contract
.github/workflows/
  ci.yml              # Check: deterministic gates, emits ci_result.json
  pdca-act.yml        # Act: workflow_run-triggered, multi-layer guarded
.claude/agents/
  planner.md          # Plan subagent (contract generator)
tests/test_guard.py   # the guard guarded by its own deterministic oracle
```

## Steps for Claude when this skill runs

1. Confirm the target repo has a remote on GitHub (the loop's control plane).
2. Copy the files above into the project. If any already exist, diff and ask
   before overwriting.
3. Run `pytest tests/test_guard.py -q` to confirm the guard is green before
   wiring anything else. Never proceed if the guard's own tests fail.
4. Tell the user the two secrets they must add in repo settings:
   - `PDCA_PAT` — a fine-scoped PAT or GitHub App token. **Required** to
     re-fire CI between cycles (the default `GITHUB_TOKEN` deliberately cannot,
     which is GitHub's anti-recursion protection). The loop only touches this
     token AFTER the guard says `continue`, so it cannot start a cycle without
     passing the stop-judgment.
   - `ANTHROPIC_API_KEY` **or** `CLAUDE_CODE_OAUTH_TOKEN` — for the maker. The
     maker is wired to Claude Code headless in `act.py::call_maker`; set the API
     key for metered billing, or the OAuth token to use a Max/Team seat.
5. Point the user to the single integration point: `act.py::call_maker()`. It
   must invoke the maker (Claude Code headless `claude -p ... --max-turns N`, or
   a FastAPI bridge that returns a structured patch) and commit on the branch.
6. Explain the one required human checkpoint: **plan approval**. Run the
   `planner` subagent on the goal, have a human approve the emitted contract,
   commit it, then start the loop by pushing a `pdca/<task>` branch.

## Tuning knobs (in .pdca/state.json)

- `max_cycles` — hard budget per task.
- `oscillation_threshold` / `oscillation_window` — how aggressively to detect a
  stuck loop (same failure recurring within a sliding window).

## Guardrail summary (defense in depth)

- Logical: `guard.evaluate()` — max-cycle + oscillation, gates every workflow step.
- Structural: workflow `concurrency` (no stacked loops) + `timeout-minutes`
  (wall-clock kill) + PAT-gated re-trigger reachable only past the guard.
