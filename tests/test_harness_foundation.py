import json

import pytest

from stock_etf.harness.evidence import validate_evidence_references
from stock_etf.harness.orchestrator import HarnessConfig, run_bounded_steps
from stock_etf.harness.state import RunStore
from stock_etf.harness.tracing import JsonlTrace


def test_run_store_persists_run_and_stages(tmp_path):
    store = RunStore(tmp_path / "runs.sqlite3")
    store.create_run("r1", {"horizon": "LONG_TERM"})
    store.start_stage("r1", "retrieval", details={"cache": "miss"})
    store.finish_stage("r1", "retrieval", "completed", details={"items": 3})
    store.set_run_status("r1", "completed", {"quality_gate": "pass"})

    run = store.get_run("r1")
    assert run["status"] == "completed"
    assert run["config"]["horizon"] == "LONG_TERM"
    assert run["stages"][0]["details"]["items"] == 3


def test_evidence_reference_validation():
    evidence = [{"evidence_id": "E1", "source": "test"}]
    valid = {"claims": [{"text": "Claim", "evidence_ids": ["E1"]}]}
    invalid = {"claims": [{"text": "Claim", "evidence_ids": ["MISSING"]}]}
    assert validate_evidence_references(valid, evidence) == []
    assert "unknown evidence_id" in validate_evidence_references(invalid, evidence)[0]


def test_bounded_orchestrator_stops_when_done():
    def step(state):
        state["count"] = state.get("count", 0) + 1
        state["done"] = state["count"] == 2
        return state

    result = run_bounded_steps(step, {}, HarnessConfig(max_steps=5))
    assert result["count"] == 2
    assert result["stop_reason"] == "completed"


def test_bounded_orchestrator_stops_at_step_budget():
    result = run_bounded_steps(
        lambda state: {**state, "count": state.get("count", 0) + 1},
        {},
        HarnessConfig(max_steps=2),
    )
    assert result["stop_reason"] == "step_budget_exceeded"
    assert result["count"] == 2


def test_jsonl_trace_writes_valid_event(tmp_path):
    path = tmp_path / "trace.jsonl"
    JsonlTrace(path).emit("stage.completed", "r1", duration_ms=25)
    event = json.loads(path.read_text().strip())
    assert event["event"] == "stage.completed"
    assert event["run_id"] == "r1"
    assert event["duration_ms"] == 25
