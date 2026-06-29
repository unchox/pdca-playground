# PDCA autonomous loop — project constitution

This repo runs an autonomous PDCA loop. A human supplies a goal; the loop builds,
checks, and self-corrects until every acceptance criterion is machine-verified
green, the contract must be replanned, or the loop stops and escalates.

## The split

- **GitHub is the control plane.** Branches = isolation (Do). CI = deterministic
  gates (Check). `required status checks` + `required review` = completion +
  human gate. Issues/PRs = state + audit. Actions = event-driven execution.
- **The only self-owned control logic is the guard/router** in `.pdca/guard.py`.
  It is a pure function over persisted state, so the same state always yields
  the same decision — that is where reproducibility lives. It is itself covered
  by `tests/test_guard.py`.
- **LLMs are stateless workers, not controllers.** They may plan, implement,
  review, or judge, but they do not decide whether the loop continues. Their
  outputs must be written as structured artifacts that deterministic code reads.

## Roles

- **Plan** (`.claude/agents/planner.md`) — turns the goal into a contract:
  tasks, acceptance criteria, and an *oracle* per criterion. Highest-leverage
  human moment is approving this contract.
- **Do (maker)** — implements one task on a `pdca/<task>` branch. Wired in
  `act.py::call_maker`.
- **Check** — `ci.yml` runs deterministic gates and emits `ci_result.json`.
- **Judge** — optional evaluator that emits a schema-validated `quality_vector`
  and/or `replan_requested` signal. Judge never pushes code and never controls
  the loop directly.
- **Act** — `act.py` records the outcome, calls `guard.evaluate()`, and routes:
  green → done; red with progress → feedback to maker + next cycle; replan →
  planner/human gate; budget gone, oscillating, or stalled → escalation Issue.

## Loop shape

```
push pdca/<task>  ──►  ci.yml (Check)  ──►  pdca-act.yml (Act)
                                               │ guard.evaluate()
              ┌──────── continue ◄─────────────┤
              │  maker patch + PAT push         ├─ completed → PR comment
              │                                  ├─ replan → Issue + human plan gate
              └──────────────────────────────────┴─ stopped_* → escalation Issue
```

## Decision model

`guard.evaluate()` returns only these route decisions:

- `continue`: deterministic gates are red, but budget remains and progress is acceptable.
- `completed`: all deterministic gates are green.
- `replan`: the current contract/oracles appear wrong or unsafe to patch around.
- `stopped_max`: cycle budget exhausted.
- `stopped_oscillation`: same failure mode is recurring within the configured window.
- `stopped_no_progress`: quality/progress has stalled across consecutive cycles.
- `already_terminal`: idempotency guard; do not restart work.

## Quality Vector rule

A Quality Vector is optional but, when present, every metric must be normalized so
higher is better. Examples: `coverage`, `security`, `maintainability`,
`complexity_inverse`, `architecture`. The guard treats a lack of improvement as
a stop condition after `no_progress_threshold` consecutive cycles. Quality Vector
values support the route decision; they do not replace deterministic oracles.

## Operating rules

- Acceptance is defined by oracles (pytest / schema / type / lint), never by an
  LLM's opinion. If you can't write an oracle, it's Tier C → human gate.
- New failures become permanent regression tests. The gate set only ratchets up.
- Never relax a gate to make the loop pass. Escalate or replan instead.
- Replan is terminal for the current loop. A revised Plan requires human approval
  before a new loop starts.
- `PDCA_PAT` may be used only in the `decision == 'continue'` step. Replan,
  completion, and stopped states must never re-fire CI.
- Secrets: `PDCA_PAT` (re-fire CI between cycles) and one maker credential —
  `ANTHROPIC_API_KEY` (metered API) or `CLAUDE_CODE_OAUTH_TOKEN` (Max/Team seat).
