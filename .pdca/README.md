# .pdca — autonomous loop machinery

| File | Role |
|---|---|
| `guard.py` | Stop-judgment logic. Pure, deterministic, tested. The only piece not delegated to GitHub. |
| `act.py` | Act controller. Records CI outcome → `guard.evaluate()` → routes. Wire `call_maker()`. |
| `state.json` | Live loop state (cycle count, failure-signature history). Tune `max_cycles`, `oscillation_*`. |
| `state.schema.json` | Contract for `state.json`. |

## Run the guard's tests
```bash
pytest tests/test_guard.py -q
```

## How a cycle flows
1. Push to `pdca/<task>` → `ci.yml` runs deterministic gates, writes `ci_result.json`.
2. `pdca-act.yml` fires on CI completion → runs `act.py`.
3. `act.py` records the outcome, calls `guard.evaluate()`:
   - `completed` → comment on PR, stop.
   - `continue` → maker fixes only the red checks; patch pushed with `PDCA_PAT`, re-firing CI.
   - `stopped_max` / `stopped_oscillation` → open escalation Issue, stop.

## Why two tokens
- `GITHUB_TOKEN` (default) **cannot** re-fire workflows — GitHub's anti-recursion guard.
- `PDCA_PAT` re-fires CI for the next cycle, and is touched **only** in the
  `continue` branch, i.e. only after the guard passes. No cycle can begin
  without clearing the stop-judgment.

## Failure signature
`compute_failure_signature()` hashes the *sorted set of failing check ids*, not
raw logs. Same set failing → same signature → oscillation is detectable. Never
feed it log text.

## Routes (v2)
`guard.evaluate()` returns one of: `continue`, `completed`, `replan`,
`stopped_max`, `stopped_oscillation`, `stopped_no_progress`, `already_terminal`.

- **replan** — a judge set `replan_requested` (the contract/oracles look wrong).
  Terminal for this loop; opens a replan Issue for human plan approval. No CI re-fire.
- **stopped_no_progress** — optional `quality_vector` aggregate stalled across
  `no_progress_threshold` judged cycles. Only fires when quality data is present.

## Optional judge input
If `.pdca/judge_result.json` exists with `{"quality_vector": {...}, "replan_requested": bool}`,
the Act controller validates it and records it as advisory input. The judge never
chooses the route — `guard.evaluate()` always does. No judge file => quality
features are simply skipped.

## Maker wiring (call_maker)
`act.py::call_maker()` is now wired to **Claude Code headless**: on `continue`
it runs `claude -p <prompt> --permission-mode acceptEdits --max-turns N
--model <m> --output-format json` in the repo, the maker EDITS files only, and
the **workflow** stages + commits + pushes (with `PDCA_PAT`) so CI re-fires.
If the maker makes no change, the run fails loudly (no empty cycle).

Auth (read from env by the CLI, never in code): set **`ANTHROPIC_API_KEY`** for
metered API billing, or **`CLAUDE_CODE_OAUTH_TOKEN`** to use a Max/Team seat.

Tunables (env, with defaults): `PDCA_MAKER_MODEL` (`claude-sonnet-5` — explicit
version pin to avoid silent alias drift when Claude Code CLI updates), `PDCA_MAKER_MAX_TURNS`
(30), `PDCA_MAKER_ALLOWED_TOOLS` (Edit,Write,Read,Grep,Glob).
