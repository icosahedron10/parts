"""Actual Deep Agents integration and durable query-only run accounting."""

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from cnh_parts_agent.diagnostics import Diagnostics, current
from evals.benchmark.score import assess_case
from evals.harness import run_query
from evals.run import run
from tests.scripted_model import ScriptedCatalogModel, ScriptedSupervisorModel


def test_terminated_run_recovery_keeps_every_scheduled_case(tmp_path):
    from evals.recover import recover

    report = tmp_path / "interrupted.jsonl"
    scheduled = [{"id": str(i), "query": "question"} for i in range(3)]
    initial = json.dumps({"record_type": "run", "scheduled_cases": scheduled}) + "\n"
    initial += json.dumps({"record_type": "case", **scheduled[0], "status": "received"}) + "\n"
    report.write_text(initial, encoding="utf-8")
    assert recover(report) == 2
    assert recover(report) == 0
    rows = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    assert report.read_text(encoding="utf-8").startswith(initial)
    assert [row["status"] for row in rows[1:]] == ["received", "not_run", "not_run"]


class ObservingParent(ScriptedSupervisorModel):
    offered: list[list[str]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        result = super().bind_tools(tools, **kwargs)
        self.offered.append(list(self.bound_tool_names))
        return result


async def test_real_deep_agent_exposes_only_lookup_and_records_evidence(catalog):
    parent = ObservingParent()
    diagnostics = Diagnostics()
    token = current.set(diagnostics)
    try:
        response = await run_query(
            "I need a radiator for my 2010 CASE CX350B.",
            catalog=catalog,
            model=ScriptedCatalogModel(),
            orchestrator_model=parent,
        )
    finally:
        current.reset(token)
    assert "KSH1028" in response
    assert parent.offered[0] == ["lookup_parts"]
    assert all(set(names) <= {"lookup_parts"} for names in parent.offered)
    assert any(o["operation"] == "get_assembly" for o in diagnostics.observations)


class EscapingParent(ObservingParent):
    def _generate(self, *args, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "escape"}]
                    )
                )
            ]
        )


class ContentBlockParent(ObservingParent):
    def _generate(self, *args, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content=[{"type": "text", "text": "A valid provider text block."}])
                )
            ]
        )


async def test_provider_content_blocks_are_valid_final_answers(catalog):
    assert (
        await run_query(
            "hello", catalog=catalog, model=ScriptedCatalogModel(), orchestrator_model=ContentBlockParent()
        )
        == "A valid provider text block."
    )


async def test_built_in_tool_execution_is_rejected(catalog):
    with pytest.raises(ValueError, match="only lookup_parts"):
        await run_query(
            "List files", catalog=catalog, model=ScriptedCatalogModel(), orchestrator_model=EscapingParent()
        )


async def test_runner_preserves_failure_denominator_and_refuses_overwrite(tmp_path):
    cases = tmp_path / "questions.jsonl"
    cases.write_text(
        "".join(json.dumps({"id": str(i), "query": "question " + str(i)}) + "\n" for i in range(3))
    )
    seen = []

    async def execute(query):
        seen.append(query)
        if query == "question 1":
            raise ValueError("deliberate failure")
        return "answer"

    output = tmp_path / "run.jsonl"
    assert await run(cases, output, execute=execute) == 1
    rows = [json.loads(s) for s in output.read_text().splitlines()]
    assert len(rows) == 4
    assert [r["status"] for r in rows[1:]] == ["received", "error", "received"]
    assert seen == ["question 0", "question 1", "question 2"]
    with pytest.raises(FileExistsError):
        await run(cases, output, execute=execute)


async def test_runner_rejects_answer_fields_before_inference(tmp_path):
    cases = tmp_path / "bad.jsonl"
    cases.write_text(json.dumps({"id": "x", "query": "question", "answer": "secret gold"}))
    with pytest.raises(ValueError, match="only id"):
        await run(cases, tmp_path / "run.jsonl")


def test_scorer_requires_inspected_assembly_not_just_product_url():
    gold = {
        "answer": {"parts": [{"part_number": "KSH1028", "assembly_urls": ["https://example.test/assembly"]}]}
    }
    row = {
        "id": "x",
        "status": "received",
        "response_status": "completed",
        "response_text": "KSH1028",
        "evidence": [],
    }
    result = assess_case(row, gold)
    assert result["mentioned_reference_numbers"] == ["KSH1028"]
    assert result["reference_numbers_with_cited_gold_assembly"] == []
    assert result["accuracy_pass"] is None
