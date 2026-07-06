"""Rubric check runner (Phase 2, L3) — evaluates class A + class B criteria.

Emits .pdca/rubric_result.json in the exact 4-key shape of ci_result.json
({outcome, failing_checks, error_kinds, summary}); ci_report.py merges it into
the single ci_result.json that act.py/guard.py read unchanged. Failing
criterion ids (net-01, ...) go into failing_checks so the guard's
failure_signature works as-is; group ids are "rubric:A" / "rubric:B".

Class B criteria are judged by a separate read-only LLM context
(.claude/agents/judge.md) invoked headless. Fail-closed everywhere:
unknown check → RunnerError; invalid judge output → one retry then the
criterion fails; missing credentials → the criterion fails with an explicit
gap. Never guess a pass.

Verdict caching (spec §6.3): cache_get/cache_put are injectable seams; Phase D
wires them to .pdca/cache.py. Default None = judge is called directly.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import jsonschema
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / ".pdca"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import cache  # noqa: E402
import rubric_loader as rl  # noqa: E402
import rollback_coverage  # noqa: E402
import section_present  # noqa: E402
from canonical import canonical_hash  # noqa: E402

# Explicit registry — the only class-A dispatch surface. A rubric referencing
# anything else is a contract violation, not a plugin-discovery problem.
CHECKS = {
    "section_present": section_present.run,
    "rollback_coverage": rollback_coverage.run,
}

# Judge model is pinned to an explicit version (never an alias), same policy
# as MAKER_MODEL in act.py. Bump JUDGE_PROMPT_VERSION whenever judge.md's
# instructions change — it is part of the verdict cache key.
JUDGE_CLI = os.environ.get("PDCA_JUDGE_CLI", "claude")
JUDGE_MODEL = os.environ.get("PDCA_JUDGE_MODEL", "claude-sonnet-5")
JUDGE_PROMPT_VERSION = "1"
JUDGE_MAX_TURNS = os.environ.get("PDCA_JUDGE_MAX_TURNS", "5")
JUDGE_AGENT_MD = REPO_ROOT / ".claude" / "agents" / "judge.md"
VERDICT_SCHEMA_PATH = REPO_ROOT / "schemas" / "verdict.schema.json"

DEFAULT_OUT = ".pdca/rubric_result.json"


class RunnerError(Exception):
    """Infrastructure-level failure (unloadable rubric/artifact, unknown check)."""


def _judge_instructions() -> str:
    """judge.md body (frontmatter stripped) — single source of the judge prompt."""
    text = JUDGE_AGENT_MD.read_text(encoding="utf-8")
    if text.startswith("---"):
        _, _, rest = text.partition("---")
        _, _, body = rest.partition("---")
        return body.strip()
    return text.strip()


def _invoke_judge_cli(prompt: str) -> str:
    """Run the judge headless; returns raw stdout. Isolated for test monkeypatching."""
    cmd = [
        JUDGE_CLI,
        "-p", prompt,
        "--output-format", "json",
        "--model", JUDGE_MODEL,
        "--allowedTools", "Read",
        "--max-turns", JUDGE_MAX_TURNS,
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"judge CLI exited {proc.returncode}: {proc.stderr[:500]}")
    return proc.stdout


def _parse_verdict(stdout: str) -> dict:
    """Outer CLI JSON → inner verdict JSON → schema-validated dict. Raises on any step."""
    outer = json.loads(stdout)
    text = outer.get("result", stdout) if isinstance(outer, dict) else stdout
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`\n")
        if text.startswith("json"):
            text = text[4:]
    verdict = json.loads(text)
    schema = json.loads(VERDICT_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.validate(verdict, schema)
    return verdict


def _judge_credentials_present() -> bool:
    return bool(
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    )


def judge_criterion(criterion: dict, rubric: dict, artifact: dict) -> tuple[bool, str, dict]:
    """One class-B judgement: build prompt, call judge, validate; one retry then
    fail-closed (spec §5.3). Returns (passed, gap, verdict-record)."""
    prompt = (
        f"{_judge_instructions()}\n\n"
        f"## criterion_id\n{criterion['id']}\n\n"
        f"## judge_question\n{criterion['judge_question']}\n\n"
        f"## reference_lists\n{json.dumps(rubric.get('reference_lists', {}), ensure_ascii=False)}\n\n"
        f"## artifact\n{json.dumps(artifact, ensure_ascii=False)}\n"
    )
    last_error = ""
    for _attempt in range(2):
        try:
            verdict = _parse_verdict(_invoke_judge_cli(prompt))
        except (RuntimeError, ValueError, json.JSONDecodeError, jsonschema.ValidationError) as exc:
            last_error = str(exc)
            continue
        unaddressed = [i for i in verdict["items"] if not i["addressed"]]
        gap = "; ".join(f"{i['id']}: {i['gap']}" for i in unaddressed)
        verdict["model_id"] = JUDGE_MODEL
        verdict["judge_prompt_version"] = JUDGE_PROMPT_VERSION
        return (not unaddressed), gap, verdict
    return False, f"judge output invalid ({last_error[:200]})", {}


def run_rubric(
    rubric: dict,
    artifact: dict,
    *,
    skip_judge: bool = False,
    cache_get=None,
    cache_put=None,
) -> dict:
    """Evaluate all criteria; return the 4-key ci_result-compatible dict."""
    failing: list[str] = []
    kinds: set[str] = set()
    gaps: dict[str, str] = {}
    skipped_b = 0

    for crit in rubric["criteria"]:
        if crit["class"] == "A":
            tokens = shlex.split(crit["check"])
            fn = CHECKS.get(tokens[0])
            if fn is None:
                raise RunnerError(f"criterion '{crit['id']}': unknown check '{tokens[0]}'")
            passed, gap = fn(artifact, tokens[1:])
            if not passed:
                failing.append(crit["id"])
                kinds.add("rubric:A")
                gaps[crit["id"]] = gap
            continue

        # class B
        if skip_judge:
            skipped_b += 1
            continue
        if not _judge_credentials_present():
            failing.append(crit["id"])
            kinds.add("rubric:B")
            gaps[crit["id"]] = "judge unavailable (no credentials)"
            continue

        verdict = None
        cache_key = canonical_hash(
            {
                "artifact": artifact,
                "rubric_domain": rubric["domain"],
                "rubric_version": rubric["version"],
                "model_id": JUDGE_MODEL,
                "judge_prompt_version": JUDGE_PROMPT_VERSION,
                "criterion_id": crit["id"],
            }
        )
        if cache_get is not None:
            cached = cache_get("verdicts", cache_key)
            if cached is not None:
                verdict = cached

        if verdict is None:
            passed, gap, verdict_rec = judge_criterion(crit, rubric, artifact)
            if verdict_rec and cache_put is not None:
                cache_put("verdicts", cache_key, {"passed": passed, "gap": gap,
                                                  "verdict": verdict_rec})
        else:
            passed, gap = verdict["passed"], verdict["gap"]

        if not passed:
            failing.append(crit["id"])
            kinds.add("rubric:B")
            gaps[crit["id"]] = gap

    summary = (
        f"rubric {rubric['domain']} v{rubric['version']}: "
        + (f"{len(failing)} criteria red" if failing else "all criteria green")
        + (f" ({skipped_b} class-B skipped)" if skipped_b else "")
    )
    if gaps:
        summary += " | gaps: " + "; ".join(f"{k}: {v}" for k, v in gaps.items())
    return {
        "outcome": "fail" if failing else "pass",
        "failing_checks": failing,
        "error_kinds": sorted(kinds),
        "summary": summary,
    }


def _infra_result(reason: str) -> dict:
    return {
        "outcome": "fail",
        "failing_checks": ["__rubric_infra__"],
        "error_kinds": ["infra"],
        "summary": f"rubric runner infra failure: {reason}",
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PDCA rubric runner")
    ap.add_argument("--rubric", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--skip-judge", action="store_true")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        rubric = rl.load_rubric(args.rubric, checks_dir=Path(__file__).resolve().parent)
        artifact_path = Path(args.artifact)
        if not artifact_path.is_file():
            raise RunnerError(f"artifact not found: {artifact_path}")
        artifact = yaml.safe_load(artifact_path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            raise RunnerError("artifact is not a mapping")
        # Verdict caching is mandatory in the real pipeline (spec §5.3/§6.3);
        # the injectable seams stay for tests. CacheCorruption is deliberately
        # NOT caught: a broken audit record must stop the run, not degrade it.
        result = run_rubric(
            rubric,
            artifact,
            skip_judge=args.skip_judge,
            cache_get=cache.get,
            cache_put=cache.put,
        )
    except (rl.RubricError, RunnerError, yaml.YAMLError) as exc:
        result = _infra_result(str(exc))

    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(result["summary"])
    return 0 if result["outcome"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
