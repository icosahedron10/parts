"""Real graph execution with scripted models and fixture-backed catalog operations."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from cnh_parts_agent import make_lookup_parts_tool
from cnh_parts_agent.agent.specialist import PartsToolBoundary
from cnh_parts_agent.agent.tools import PartsTools, WorkflowBudget
from cnh_parts_agent.diagnostics import Diagnostics, current
from tests.scripted_model import ScriptedCatalogModel


class MustNotRun(ScriptedCatalogModel):
    def _generate(self, *args, **kwargs):
        raise AssertionError("Clarification must not call a model")


async def test_catalog_clarification_without_model(catalog):
    result = json.loads(
        await make_lookup_parts_tool(catalog=catalog, model=MustNotRun()).ainvoke(
            {"machine_query": "580N", "part_query": "radiator"}
        )
    )
    assert "Which exact catalog" in " ".join(result["key_facts"])
    assert result["store_url"] == ""
    assert catalog.transport.request_count == 0


async def test_invalid_selection_does_not_silently_choose(catalog):
    with pytest.raises(ValueError, match="Selected catalog"):
        await make_lookup_parts_tool(catalog=catalog, model=MustNotRun()).ainvoke(
            {"machine_query": "CX350B", "part_query": "radiator", "selected_machine_label": "invented"}
        )


async def test_actual_specialist_handoff_and_private_observations(catalog):
    diagnostics = Diagnostics()
    token = current.set(diagnostics)
    try:
        result = json.loads(
            await make_lookup_parts_tool(catalog=catalog, model=ScriptedCatalogModel()).ainvoke(
                {"machine_query": "CX350B", "part_query": "radiator"}
            )
        )
    finally:
        current.reset(token)
    assert set(result) == {"key_facts", "sources", "store_url"}
    assert "KSH1028" in " ".join(result["key_facts"])
    # The captured assembly contains two radiator numbers; the script preserves that ambiguity.
    assert "LN002080" in " ".join(result["key_facts"])
    assert result["store_url"] == ""
    assert any(o["operation"] == "get_assembly" for o in diagnostics.observations)
    assert all(url.startswith("https://") for url in result["sources"])


class SlowModel(ScriptedCatalogModel):
    async def _agenerate(self, *args, **kwargs):
        await asyncio.sleep(10)
        raise AssertionError("Outer deadline should cancel inference")


async def test_deadline_includes_inference(catalog):
    catalog.settings.timeout_seconds = 0.15
    with pytest.raises(TimeoutError):
        await make_lookup_parts_tool(catalog=catalog, model=SlowModel()).ainvoke(
            {"machine_query": "CX350B", "part_query": "radiator"}
        )


async def test_budget_counts_cached_and_rejected_calls(catalog):
    client = PartsTools(catalog, budget=WorkflowBudget(max_calls=2))
    await client.tool_call("search_machines", {"query": "CX350B"})
    result = await client.tool_call("get_assembly", {"machine_ref": "m999", "assembly_ref": "a999"})
    assert result["error"]["code"] == "unknown_lookup_reference"
    result = await client.tool_call("search_machines", {"query": "CX350B"})
    assert result["error"]["code"] == "workflow_budget_exhausted"
    assert catalog.transport.request_count == 0


class FabricatingModel(ScriptedCatalogModel):
    def _generate(self, *args, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=json.dumps(
                            {"key_facts": [], "sources": ["https://example.com/invented"], "store_url": ""}
                        )
                    )
                )
            ]
        )


async def test_fabricated_link_fails_completion(catalog):
    with pytest.raises(ValueError, match="unobserved"):
        await make_lookup_parts_tool(catalog=catalog, model=FabricatingModel()).ainvoke(
            {"machine_query": "CX350B", "part_query": "radiator"}
        )


async def test_handles_do_not_cross_lookups(catalog):
    first, second = PartsTools(catalog), PartsTools(catalog)
    result = first.encode_references(await first.tool_call("search_machines", {"query": "CX350B"}))
    handle = result["data"]["machines"][0]["machine_ref"]
    rejected = await second.tool_call("list_groups", {"machine_ref": handle})
    assert rejected["error"]["code"] == "unknown_lookup_reference"
    assert catalog.transport.request_count == 0


@pytest.mark.parametrize(
    "name,args,code",
    [
        (
            "search_catalog",
            {"machine_ref": "m2", "query": "radiator", "mode": "assembly_title"},
            "machine_selection_required",
        ),
        ("get_part_relationships", {"part_ref": "p1"}, "observed_part_required"),
        ("search_machines", {"query": "CX350B"}, "machine_selection_locked"),
        ("get_assembly", {"machine_ref": "m1", "assembly_ref": "a2"}, "assembly_inspection_required"),
    ],
)
async def test_scope_boundary_rejects_before_catalog(catalog, name, args, code):
    client = PartsTools(catalog)
    boundary = PartsToolBoundary(machine_query="CX350B", client=client, part_query="radiator")
    boundary.machine_ref = "m1"
    boundary.pending_assemblies = {"a1"}
    response = boundary._rejection(SimpleNamespace(tool_call={"name": name, "args": args, "id": "x"}))
    assert json.loads(response.content)["error"]["code"] == code
    assert client.budget.calls == 1
    assert catalog.transport.request_count == 0


async def test_native_provider_receives_strict_schema(catalog, monkeypatch):
    from langchain_openai import ChatOpenAI

    from cnh_parts_agent.agent import model as model_module

    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert payload["max_output_tokens"] == 8192
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["text"]["format"]["strict"] is True
        schema = payload["text"]["format"]["schema"]
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {"key_facts", "sources", "store_url"}
        return httpx.Response(
            200,
            json={
                "id": "resp_offline",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.6-luna",
                "output": [
                    {
                        "type": "message",
                        "id": "msg_offline",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "annotations": [],
                                "text": json.dumps(
                                    {"key_facts": ["No assembly inspected."], "sources": [], "store_url": ""}
                                ),
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        monkeypatch.setenv("OPENAI_API_KEY", "offline-only")
        monkeypatch.setenv("PARTS_MODEL_MAX_OUTPUT_TOKENS", "8192")
        monkeypatch.setattr(
            model_module, "ChatOpenAI", lambda **kwargs: ChatOpenAI(http_async_client=http, **kwargs)
        )
        model = model_module.create_parts_model()
        result = json.loads(
            await make_lookup_parts_tool(catalog=catalog, model=model).ainvoke(
                {"machine_query": "CX350B", "part_query": "radiator"}
            )
        )
    assert len(requests) == 1 and result["store_url"] == ""
