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


def test_evidence_urls_preserve_markdown_context_without_accepting_changed_targets():
    from evals.evidence import answer_urls

    url = "https://www.mycnhstore.com/us/en/casece/p/156602A1"
    assert answer_urls(f"**[{156602}A1]({url})**", {url}) == {url}
    assert answer_urls(f"**[part]({url}/invented)**", {url}) == {url + "/invented"}


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


async def test_parent_deadline_accommodates_configured_lookup_and_model_calls(catalog, monkeypatch):
    import asyncio

    from evals import harness

    catalog.settings.timeout_seconds = 120
    catalog.settings.model_timeout_seconds = 45
    original = asyncio.timeout
    deadlines = []

    def observe_timeout(delay):
        deadlines.append(delay)
        return original(delay)

    monkeypatch.setattr(harness.asyncio, "timeout", observe_timeout)
    response = await run_query(
        "I need a radiator for my 2010 CASE CX350B.",
        catalog=catalog,
        model=ScriptedCatalogModel(),
        orchestrator_model=ScriptedSupervisorModel(),
    )
    assert "KSH1028" in response
    assert deadlines[0] == 215
    assert deadlines[1:] and all(0 < deadline <= 120 for deadline in deadlines[1:])


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


@pytest.mark.parametrize(
    "message,stops",
    [
        ("You have no credits remaining.", True),
        ("Error code: insufficient_quota", True),
        ("Rate limit reached; retry later", False),
    ],
)
async def test_runner_stops_on_exhausted_credits_but_not_transient_throttling(tmp_path, message, stops):
    class OpenAIRateLimitError(Exception):
        pass

    cases = tmp_path / "questions.jsonl"
    cases.write_text("".join(json.dumps({"id": str(i), "query": str(i)}) + "\n" for i in range(3)))
    calls = []

    async def execute(query):
        calls.append(query)
        if query == "0":
            raise OpenAIRateLimitError(message)
        return "answer"

    output = tmp_path / "run.jsonl"
    assert await run(cases, output, execute=execute) == 1
    rows = [json.loads(line) for line in output.read_text().splitlines()][1:]
    assert [row["status"] for row in rows] == ["error", *(["not_run"] * 2 if stops else ["received"] * 2)]
    assert len(calls) == (1 if stops else 3)


async def test_direct_runner_bypasses_parent_and_preserves_lookup_arguments(tmp_path, monkeypatch):
    import importlib

    runner = importlib.import_module("evals.run")
    lookup = {
        "machine_query": "CASE 580K",
        "part_query": "water pump",
        "brand": "casece",
        "selected_machine_label": "explicit user choice",
    }
    seen = []

    class RecordingTool:
        async def ainvoke(self, args):
            seen.append(args)
            return '{"key_facts": ["recorded"], "sources": [], "store_url": ""}'

    async def forbidden_parent(*args, **kwargs):
        raise AssertionError("Direct evaluation must never invoke the parent")

    monkeypatch.setattr(runner, "run_query", forbidden_parent)
    monkeypatch.setattr(runner, "create_parts_model", lambda *args: object())
    monkeypatch.setattr(runner, "make_lookup_parts_tool", lambda **kwargs: RecordingTool())
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({"id": "H001", "query": "user question", "lookup": lookup}))
    output = tmp_path / "direct.jsonl"
    assert await run(cases, output, direct=True, live=True) == 0
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert rows[0]["target"] == "specialist"
    assert seen == [lookup]
    assert rows[1]["lookup"] == lookup
    assert rows[1]["status"] == "received"


async def test_direct_runner_rejects_hidden_gold_before_opening_report(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        json.dumps(
            {
                "id": "H001",
                "query": "question",
                "lookup": {"machine_query": "CASE 1840", "part_query": "filter", "answer": "secret gold"},
            }
        )
    )
    output = tmp_path / "direct.jsonl"
    with pytest.raises(ValueError, match="Extra inputs"):
        await run(cases, output, direct=True)
    assert not output.exists()


async def test_model_constructor_failure_is_durable(tmp_path, monkeypatch):
    import importlib

    runner = importlib.import_module("evals.run")

    def unavailable(*args):
        raise ValueError("Missing provider configuration")

    monkeypatch.setattr(runner, "create_parts_model", unavailable)
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps({"id": str(i), "query": "question"}) + "\n" for i in range(2)))
    output = tmp_path / "report.jsonl"
    assert await runner.run(cases, output, live=True) == 1
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert [row["status"] for row in rows[1:]] == ["error", "not_run"]


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


async def test_sample_schedule_is_reproducible_and_paced(tmp_path, monkeypatch):
    from evals import run as runner

    pool = [{"id": str(i), "query": f"question {i}"} for i in range(30)]
    source = tmp_path / "queries.jsonl"
    source.write_text("\n".join(json.dumps(q) for q in pool), encoding="utf-8")
    pauses = []

    async def sleep(seconds):
        pauses.append(seconds)

    async def execute(query):
        return "No identification."

    monkeypatch.setattr(runner.asyncio, "sleep", sleep)
    monkeypatch.setenv("PARTS_CACHE_PATH", str(tmp_path / "cache.sqlite"))
    schedules = []
    for number in range(2):
        output = tmp_path / f"run{number}.jsonl"
        assert (
            await run(
                source, output, execute=execute, sample_size=20, seed=77, interval_min=30, interval_max=90
            )
            == 0
        )
        rows = [json.loads(line) for line in output.read_text().splitlines()]
        schedules.append(rows[0]["scheduled_cases"])
        assert len(rows[1:]) == 20
        assert len({row["id"] for row in rows[1:]}) == 20
    assert schedules[0] == schedules[1]
    assert len(pauses) == 38 and all(30 <= value <= 90 for value in pauses)
    with pytest.raises(ValueError, match="Sample"):
        await run(source, tmp_path / "invalid.jsonl", sample_size=31)
