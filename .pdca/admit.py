"""Admit gate (Phase 2, L2) — enforce the input floor before a task enters the loop.

Three stages (spec §4.2), inference-based completion forbidden:
  1. validate  — intake JSON against schemas/ready.schema.json
                 (+ schemas/ready.<domain>.schema.json overlay when present)
  2. enrich    — deterministic lookups from canonical repo sources only
                 (.pdca/plan.yaml); every fill is recorded in enrichment_log
  3. canonicalize — write ready.json (with rubric_domain/rubric_version embedded
                 so act.py can read them with stdlib json) and return the
                 sha256 of its canonical bytes as input_hash

Exit 0 = PASS. Exit 1 = REJECTED; the reasons are written to
.pdca/admit_rejected.txt, which the workflow turns into a human-gated issue.

Fail-closed: missing intake, unknown domain, invalid rubric, and drift between
a committed ready.json and the recomputed one are all REJECTED.

Runs only where the dev extra is installed (ci.yml / local Plan time).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rubric_loader as rl  # noqa: E402
from canonical import canonical_bytes, canonical_hash  # noqa: E402

REJECT_REPORT = "admit_rejected.txt"


def validate_intake(intake: dict, schema_dir: Path) -> list[str]:
    """Return human-readable problems ('' path = whole document); empty list = valid."""
    problems: list[str] = []
    generic = json.loads((schema_dir / "ready.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(generic)
    for err in sorted(validator.iter_errors(intake), key=lambda e: list(e.absolute_path)):
        loc = ".".join(str(p) for p in err.absolute_path) or "(root)"
        problems.append(f"{loc}: {err.message}")

    domain = intake.get("domain")
    if isinstance(domain, str) and domain:
        overlay_path = schema_dir / f"ready.{domain}.schema.json"
        if overlay_path.is_file():
            overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
            for err in jsonschema.Draft202012Validator(overlay).iter_errors(intake):
                loc = ".".join(str(p) for p in err.absolute_path) or "(root)"
                problems.append(f"{loc}: {err.message} [overlay ready.{domain}]")
    return problems


def enrich(intake: dict, repo_root: Path) -> tuple[dict, list[dict]]:
    """Fill missing required keys from canonical repo sources. Deterministic only —
    every value is a verbatim fetch, recorded in the returned enrichment_log."""
    log: list[dict] = []
    enriched = dict(intake)

    plan_path = repo_root / ".pdca" / "plan.yaml"
    plan = None
    if plan_path.is_file():
        try:
            plan = (yaml.safe_load(plan_path.read_text(encoding="utf-8")) or {}).get("plan")
        except yaml.YAMLError:
            plan = None  # unreadable source is simply not a source; no guessing

    if isinstance(plan, dict):
        if "goal" not in enriched and isinstance(plan.get("goal"), str):
            enriched["goal"] = plan["goal"].strip()
            log.append({"key": "goal", "source": ".pdca/plan.yaml:plan.goal"})
        tasks = plan.get("tasks")
        if "task_id" not in enriched and isinstance(tasks, list) and tasks:
            first_id = tasks[0].get("id")
            if isinstance(first_id, str):
                enriched["task_id"] = first_id
                log.append({"key": "task_id", "source": ".pdca/plan.yaml:plan.tasks[0].id"})

    return enriched, log


def canonicalize(intake: dict, rubric: dict, enrichment_log: list[dict]) -> tuple[dict, str]:
    """Build the ready document and its input_hash (sha256 of canonical bytes)."""
    ready = dict(intake)
    ready["rubric_domain"], ready["rubric_version"] = rl.rubric_id(rubric)
    ready["enrichment_log"] = enrichment_log
    return ready, canonical_hash(ready)


def _reject(out_dir: Path, reasons: list[str]) -> int:
    report = out_dir / REJECT_REPORT
    body = "Admit gate REJECTED this intake (fail-closed).\n\nProblems:\n" + "".join(
        f"- {r}\n" for r in reasons
    )
    report.write_text(body, encoding="utf-8")
    print(f"admit: REJECTED\n{body}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDCA admit gate")
    ap.add_argument("--intake", default=".pdca/intake.json")
    ap.add_argument("--out", default=".pdca/ready.json")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing ready.json that differs (local Plan-time regeneration)",
    )
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root)
    intake_path = Path(args.intake)
    out_path = Path(args.out)
    out_dir = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    if not intake_path.is_file():
        return _reject(out_dir, [f"intake file not found: {intake_path}"])
    try:
        intake = json.loads(intake_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return _reject(out_dir, [f"intake is not valid JSON: {exc}"])
    if not isinstance(intake, dict):
        return _reject(out_dir, ["intake must be a JSON object"])

    schema_dir = repo_root / "schemas"
    problems = validate_intake(intake, schema_dir)
    enrichment_log: list[dict] = []
    if problems:
        intake, enrichment_log = enrich(intake, repo_root)
        problems = validate_intake(intake, schema_dir)
        if problems:
            return _reject(out_dir, problems)

    rubric_path = repo_root / "rubric" / f"{intake['domain']}.yaml"
    try:
        rubric = rl.load_rubric(rubric_path, checks_dir=None)
    except rl.RubricError as exc:
        return _reject(out_dir, [f"domain rubric unusable: {exc}"])

    ready, input_hash = canonicalize(intake, rubric, enrichment_log)

    if out_path.is_file() and not args.force:
        try:
            committed = json.loads(out_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return _reject(out_dir, [f"committed {out_path} is corrupt: {exc}"])
        if canonical_bytes(committed) != canonical_bytes(ready):
            return _reject(
                out_dir,
                [
                    f"drift: committed {out_path} does not match the ready document "
                    "recomputed from intake (rerun admit locally with --force and commit)"
                ],
            )

    out_path.write_text(json.dumps(ready, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"admit: PASS input_hash={input_hash}")
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"input_hash={input_hash}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
