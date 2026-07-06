"""Cache contract: hit replay / miss store / corruption fail-closed / key sensitivity."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".pdca"))
import act  # noqa: E402
import cache  # noqa: E402

READY = {
    "task_id": "T1",
    "domain": "rac-hyperv-design",
    "goal": "g",
    "constraints": [],
    "artifact_target": "design/rac.yaml",
    "rubric_domain": "rac-hyperv-design",
    "rubric_version": 1,
    "enrichment_log": [],
}
FEEDBACK = {"cycle": 1, "still_failing": ["net-01"], "error_kinds": ["rubric:A"],
            "summary": "1 gate group(s) red", "prior_signatures": []}


# ------------------------------------------------------------------ cache.py core
def test_put_get_roundtrip_and_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "STORE", tmp_path / "store")
    assert cache.get("decisions", "k1") is None
    cache.put("decisions", "k1", {"a": 1})
    assert cache.get("decisions", "k1") == {"a": 1}


def test_corrupt_record_raises_not_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "STORE", tmp_path / "store")
    cache.put("verdicts", "k1", {"a": 1})
    (tmp_path / "store" / "verdicts" / "k1.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(cache.CacheCorruption):
        cache.get("verdicts", "k1")


def test_unknown_kind_and_unsafe_key_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "STORE", tmp_path / "store")
    with pytest.raises(ValueError):
        cache.get("nope", "k1")
    with pytest.raises(ValueError):
        cache.put("decisions", "../escape", {})


# --------------------------------------------------------- call_maker integration
@pytest.fixture
def wired(tmp_path, monkeypatch):
    """call_maker environment: tmp repo root, tmp store, fake maker + git."""
    (tmp_path / ".pdca").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cache, "STORE", tmp_path / "store")
    monkeypatch.setattr(act, "READY_PATH", tmp_path / ".pdca" / "ready.json")
    monkeypatch.setattr(act, "RUN_META_PATH", tmp_path / ".pdca" / "run_meta.json")
    monkeypatch.setattr(act, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(act, "working_tree_dirty", lambda: True)
    (tmp_path / ".pdca" / "ready.json").write_text(json.dumps(READY), encoding="utf-8")

    calls = {"maker": 0}

    def fake_maker(prompt):
        calls["maker"] += 1
        (tmp_path / "src").mkdir(exist_ok=True)
        (tmp_path / "src" / "fix.py").write_text("PATCHED = 1\n", encoding="utf-8")
        return {"session_id": "s1", "total_cost_usd": 0.5, "num_turns": 3}

    monkeypatch.setattr(act, "run_claude_maker", fake_maker)

    snapshots = iter([{}, {"src/fix.py": "digest-after"}, {}, {}])
    monkeypatch.setattr(act, "_porcelain_snapshot", lambda: next(snapshots))
    return tmp_path, calls


def test_miss_runs_maker_and_stores_decision(wired):
    tmp_path, calls = wired
    telemetry = act.call_maker(FEEDBACK)
    assert calls["maker"] == 1
    assert telemetry["session_id"] == "s1"

    key = act._maker_cache_key(READY, FEEDBACK)
    record = cache.get("decisions", key)
    assert record["files"] == {"src/fix.py": "PATCHED = 1\n"}
    assert record["telemetry"]["session_id"] == "s1"

    meta = json.loads((tmp_path / ".pdca" / "run_meta.json").read_text())
    assert meta["model_id"] == act.MAKER_MODEL
    assert meta["rubric_version"] == 1
    assert len(meta["input_hash"]) == 64


def test_hit_replays_files_without_calling_maker(wired):
    tmp_path, calls = wired
    act.call_maker(FEEDBACK)
    assert calls["maker"] == 1

    # wipe the maker's output; a hit must restore it without another LLM call
    (tmp_path / "src" / "fix.py").unlink()
    telemetry = act.call_maker(FEEDBACK)
    assert calls["maker"] == 1, "cache hit must not invoke the maker"
    assert telemetry["replayed"] is True
    assert (tmp_path / "src" / "fix.py").read_text() == "PATCHED = 1\n"
    maker_last = json.loads((tmp_path / ".pdca" / "maker_last.json").read_text())
    assert maker_last["replayed"] is True


def test_corrupt_decision_record_fails_closed(wired):
    tmp_path, calls = wired
    act.call_maker(FEEDBACK)
    key = act._maker_cache_key(READY, FEEDBACK)
    (tmp_path / "store" / "decisions" / f"{key}.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(cache.CacheCorruption):
        act.call_maker(FEEDBACK)
    assert calls["maker"] == 1, "corruption must halt, not silently recompute"


def test_no_ready_json_bypasses_cache_entirely(wired, monkeypatch):
    tmp_path, calls = wired
    (tmp_path / ".pdca" / "ready.json").unlink()
    monkeypatch.setattr(
        cache, "get",
        lambda *a: (_ for _ in ()).throw(AssertionError("cache must not be consulted")),
    )
    act.call_maker(FEEDBACK)
    assert calls["maker"] == 1
    assert not (tmp_path / ".pdca" / "run_meta.json").exists()


def test_key_sensitive_to_every_material_element(monkeypatch):
    base = act._maker_cache_key(READY, FEEDBACK)
    assert act._maker_cache_key({**READY, "goal": "other"}, FEEDBACK) != base
    assert act._maker_cache_key(READY, {**FEEDBACK, "cycle": 2}) != base
    assert act._maker_cache_key({**READY, "rubric_version": 2}, FEEDBACK) != base
    monkeypatch.setattr(act, "MAKER_MODEL", "claude-other-model")
    assert act._maker_cache_key(READY, FEEDBACK) != base
    monkeypatch.setattr(act, "MAKER_PROMPT_VERSION", "2")
    assert act._maker_cache_key(READY, FEEDBACK) != base
