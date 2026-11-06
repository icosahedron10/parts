"""Failures exposed by the full live exam, reproduced without network or answer keys."""

import hashlib
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from cnh_parts_agent.agent.handoff import PartsHandoff, encode_handoff_urls, resolve_handoff_urls
from cnh_parts_agent.agent.specialist import PartsToolBoundary
from cnh_parts_agent.agent.tools import PartsTools
from cnh_parts_agent.catalog import parse
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.storefront import CASECE, CASEIH
from cnh_parts_agent.catalog.transport import SourceResponse
from cnh_parts_agent.diagnostics import Diagnostics, current
from evals.harness import run_query
from scripts.capture_fixture import sanitize
from tests.scripted_model import ScriptedCatalogModel, ScriptedSupervisorModel


def test_fixture_sanitization_preserves_unicode_after_charset_removal():
    body = (
        '<html><head><meta charset="UTF-8"></head><body>'
        '<p data-note="Ø 58 mm">Hose Ø 58 mm — café\u00a0mount</p>'
        '<input value="private-token"><script>private-token</script></body></html>'
    ).encode()
    clean = sanitize(body)
    assert clean.isascii()
    assert b"private-token" not in clean and b"<meta" not in clean
    original, sanitized = parse.document(body).xpath("//p")[0], parse.document(clean).xpath("//p")[0]
    assert parse.plain(original) == parse.plain(sanitized)
    assert original.attrib["data-note"] == sanitized.attrib["data-note"]


def test_prose_only_citations_round_trip_without_manufacturing_links():
    assembly = "https://www.mycnhstore.com/us/en/casece/assembly/(NA)"
    product = "https://www.mycnhstore.com/us/en/casece/category/radiator/p/KSH1028"
    handoff = PartsHandoff(
        key_facts=[f"[Assembly]({assembly}). Product: {product}."], sources=[product], store_url=product
    )
    validated = resolve_handoff_urls(handoff, {"url_1": assembly, "url_2": product})
    parent_handles = {}
    encoded = encode_handoff_urls(validated, parent_handles)
    assert "https://" not in encoded.model_dump_json()
    assert set(parent_handles.values()) == {assembly, product}
    assert resolve_handoff_urls(encoded, parent_handles) == handoff
    with pytest.raises(ValueError, match="unobserved"):
        resolve_handoff_urls(
            PartsHandoff(key_facts=["url_2/invented"], sources=[], store_url=""), parent_handles
        )


def test_instruction_product_does_not_break_a_complete_harness_search(catalog):
    fixtures = Path(__file__).parent / "fixtures/casece-live"
    manifest = json.loads((fixtures / "manifest.json").read_bytes())
    body = (fixtures / "jumper.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == manifest["jumper.json"]["sha256"]
    assert len(body) == manifest["jumper.json"]["bytes"]
    _, record = catalog.candidates("2016 CASE SR210", brand="casece")[0]
    machine = record["machine"]["machine_ref"]["$ref"]
    result = parse.search(body, machine["id"], machine["path"], "part-description")
    rows = result["matches"]
    assert len(rows) == len(json.loads(body)["results"])
    instruction = next(row for row in rows if row["part_number"] == "47550199")
    assert instruction["product_url"] == CASECE.home + "/cn/instruction/p/47550199"
    assert all(row["assembly_ref"]["$ref"]["context"] == machine["id"] for row in rows)


def test_observed_sa_assembly_suffix_keeps_the_requested_na_catalog(catalog):
    fixtures = Path(__file__).parent / "fixtures/casece-live"
    metadata = json.loads((fixtures / "manifest.json").read_bytes())["frame.json"]
    body = (fixtures / "frame.json").read_bytes()
    assert hashlib.sha256(body).hexdigest() == metadata["sha256"]
    assert len(body) == metadata["bytes"]
    _, record = catalog.candidates("2018 CASE 850M LT", brand="casece")[0]
    machine = record["machine"]["machine_ref"]["$ref"]
    rows = parse.search(body, machine["id"], machine["path"], "part-description")["matches"]
    raw = json.loads(body)
    assert len(rows) == len(raw["results"])
    prefix = parse.ORIGIN + machine["path"].rsplit("/cn/", 1)[0] + "/"
    assert all(row["assembly_url"].startswith(prefix) for row in rows)
    assert all(row["assembly_url"].endswith("/" + machine["id"]) for row in rows)
    other_region = next(row for row in raw["results"] if row["assemblyUrl"].startswith("/sa/"))
    other_region["modelCode"] = "foreign_catalog"
    with pytest.raises(PartsError):
        parse.search(json.dumps(raw).encode(), machine["id"], machine["path"], "part-description")


@pytest.mark.parametrize("storefront", [CASECE, CASEIH])
def test_instruction_link_remains_bound_to_the_storefront_and_exact_part(storefront):
    path = "/cn/instruction/p/47550199"
    assert parse.product_link(path, "47550199", storefront=storefront) == storefront.home + path
    for invalid in (
        path.replace("47550199", "other"),
        "https://example.test" + path,
        (CASEIH if storefront == CASECE else CASECE).home + path,
        "/cn/instruction/../other/p/47550199",
    ):
        with pytest.raises(PartsError):
            parse.product_link(invalid, "47550199", storefront=storefront)


async def test_navigation_index_is_not_blocked_by_title_heuristics(catalog):
    client = PartsTools(catalog)
    boundary = PartsToolBoundary(client=client, part_query="track frame")
    boundary.machine_ref = "m1"
    boundary.assembly_labels = {"a1": "PICTORIAL INDEX TRACK ASSY", "a2": "LH, TRACK ASSY"}
    request = SimpleNamespace(
        tool_call={"name": "get_assembly", "args": {"machine_ref": "m1", "assembly_ref": "a1"}, "id": "index"}
    )
    rejected = boundary._rejection(request)
    assert rejected is None
    assert client.budget.calls == 0
    assert catalog.transport.request_count == 0
    request.tool_call["args"]["assembly_ref"] = "a2"
    assert boundary._rejection(request) is None


async def test_title_heuristic_cannot_block_an_observed_component_assembly(catalog, monkeypatch):
    fixtures = Path(__file__).parent / "fixtures/casece-live"
    metadata = json.loads((fixtures / "manifest.json").read_bytes())["jumper.json"]

    async def fetch(url):
        return SourceResponse(
            body=(fixtures / "jumper.json").read_bytes(),
            url=url,
            observed_at=datetime.fromisoformat(metadata["observed_at"]),
            content_hash=metadata["source_content_hash"],
        )

    monkeypatch.setattr(catalog.transport, "fetch", fetch)
    client = PartsTools(catalog)
    query = "2016 CASE SR210"
    boundary = PartsToolBoundary(
        machine_query=query, client=client, part_query="starter alternator jumper harness"
    )
    for operation, arguments in (
        ("search_machines", {"query": query}),
        ("search_catalog", {"query": "jumper", "mode": "part_description"}),
    ):
        if operation == "search_catalog":
            arguments["machine_ref"] = boundary.machine_ref
        payload = client.encode_references(await client.tool_call(operation, arguments))
        boundary._record(
            SimpleNamespace(tool_call={"name": operation, "args": arguments}),
            ToolMessage(content=json.dumps(payload), tool_call_id=operation),
        )
    requested = next(row for row in payload["data"]["matches"] if "STARTER ALTERNATOR" in row["description"])
    request = SimpleNamespace(
        tool_call={
            "name": "get_assembly",
            "id": "inspect",
            "args": {"machine_ref": boundary.machine_ref, "assembly_ref": requested["assembly_ref"]},
        }
    )
    assert boundary._rejection(request) is None
    request.tool_call["args"]["assembly_ref"] = "unobserved"
    rejected = await client.tool_call("get_assembly", request.tool_call["args"])
    assert rejected["error"]["code"] == "unknown_lookup_reference"
    assert catalog.transport.request_count == 0


class UsageThenFailureParent(ScriptedSupervisorModel):
    def _generate(self, messages, *args, **kwargs):
        if any(isinstance(message, ToolMessage) for message in messages):
            raise ValueError("Failure after a completed model response")
        result = super()._generate(messages, *args, **kwargs)
        result.generations[0].message.usage_metadata = {
            "input_tokens": 7,
            "output_tokens": 3,
            "total_tokens": 10,
        }
        return result


async def test_completed_model_usage_survives_a_later_graph_failure(catalog):
    diagnostics = Diagnostics()
    token = current.set(diagnostics)
    try:
        with pytest.raises(ValueError, match="Failure after"):
            await run_query(
                "I need a radiator for my 2010 CASE CX350B.",
                catalog=catalog,
                model=ScriptedCatalogModel(),
                orchestrator_model=UsageThenFailureParent(),
            )
    finally:
        current.reset(token)
    assert diagnostics.model_usage == [{"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}]
    assert any(row["operation"] == "get_assembly" for row in diagnostics.observations)
