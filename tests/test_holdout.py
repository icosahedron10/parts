"""Holdout accounting must not hide failures or confuse citations with task success."""

import hashlib
import json
from pathlib import Path

import pytest

from evals.holdout.score import summarize
from evals.io import read_jsonl


def test_frozen_holdout_manifest_and_separate_criteria():
    root = Path(__file__).resolve().parents[1] / "evals" / "holdout"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected
    queries, _ = read_jsonl(root / "queries.jsonl")
    criteria, _ = read_jsonl(root / "rubric.jsonl")
    assert len(queries) == manifest["count"] == 50
    assert [row["id"] for row in queries] == [row["id"] for row in criteria]
    assert all(set(row) == {"id", "query", "lookup"} for row in queries)
    assert all("review_criterion" not in row["lookup"] for row in queries)


def test_summary_keeps_errors_and_requires_complete_schedule(tmp_path):
    cases = tmp_path / "cases.jsonl"
    rubric = tmp_path / "rubric.jsonl"
    report = tmp_path / "report.jsonl"
    queries = [
        {"id": str(i), "query": "question", "lookup": {"machine_query": "CASE 1840", "part_query": "filter"}}
        for i in range(2)
    ]
    cases.write_text("".join(json.dumps(row) + "\n" for row in queries), encoding="utf-8")
    rubric.write_text(
        "".join(json.dumps({"id": str(i), "category": "routine"}) + "\n" for i in range(2)), encoding="utf-8"
    )
    header = {
        "record_type": "run",
        "target": "specialist",
        "model": "test",
        "queries_sha256": hashlib.sha256(cases.read_bytes()).hexdigest(),
        "scheduled_cases": queries,
    }
    rows = [
        {
            "record_type": "case",
            "id": str(i),
            "status": "received" if i == 0 else "error",
            "latency_seconds": i + 1,
            "evidence": [],
            "diagnostics": {"requests": [], "model_usage": []},
            "response_text": json.dumps({"key_facts": ["Unresolved"], "sources": [], "store_url": ""}),
        }
        for i in range(2)
    ]

    def write_report(items):
        report.write_text("".join(json.dumps(row) + "\n" for row in [header, *items]), encoding="utf-8")

    write_report(rows)
    result = summarize(report, cases, rubric)
    assert result["count"] == 2
    assert result["statuses"] == {"received": 1, "error": 1}
    assert result["mechanical_evidence_passes"] == 1
    assert result["review_verdicts"] is None
    assert result["cases"][0]["evidence_gate"]["semantic_correctness"] == "not_assessed"
    write_report(rows[:1])
    with pytest.raises(ValueError, match="every scheduled case"):
        summarize(report, cases, rubric)
    write_report([rows[0], rows[0]])
    with pytest.raises(ValueError, match="every scheduled case"):
        summarize(report, cases, rubric)
