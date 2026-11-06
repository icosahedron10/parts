"""Prepared navigation request counts, atomic publication, expiry and recovery."""

import json
from copy import deepcopy

import pytest

from cnh_parts_agent.agent.tools import PartsTools
from cnh_parts_agent.catalog import navigation, parse
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.storefront import CASECE, CASEIH
from scripts.prepare_navigation import BatchComplete, checkpoint_fetch, import_snapshots, prepare_catalog
from tests.conftest import FIXTURES
from tests.scripted_model import ScriptedCatalogModel


async def prepared(catalog):
    machine = (await catalog.execute("search_machines", {"query": "CX350B"}))["data"]["machines"][0][
        "machine_ref"
    ]
    parsed = parse.search((FIXTURES / "figure.json").read_bytes(), machine["id"], machine["path"], "figure")
    matches = expand_references(parsed["matches"], CASECE.source_id)
    entries = [
        {
            "assembly_ref": row["assembly_ref"],
            "title": row["assembly_label"],
            "group": "cooling",
            "figure": None,
            "observed_path": None,
            "assembly_url": row["assembly_url"],
            "stale": False,
        }
        for row in matches
    ]
    navigation.publish(catalog.cache.connection, machine, entries, [], [], complete=True)
    return machine, entries


async def test_prepared_lookup_cold_one_warm_zero_and_local_pages(catalog):
    machine, entries = await prepared(catalog)
    for offset in range(len(entries)):
        page = await catalog.execute(
            "list_assemblies", {"machine_ref": machine, "limit": 1, "offset": offset}
        )
        assert page["data"]["assemblies"][0] == entries[offset]
    await catalog.execute("list_groups", {"machine_ref": machine})
    assert catalog.transport.request_count == 0
    args = {"machine_ref": machine, "assembly_ref": entries[0]["assembly_ref"]}
    first = await catalog.execute("get_assembly", args)
    assert catalog.transport.request_count == 1
    second = await catalog.execute("get_assembly", args)
    assert first == second and catalog.transport.request_count == 1


async def test_navigation_survives_parts_expiry_and_preserves_old_expiration(catalog):
    machine, entries = await prepared(catalog)
    now = [1000]
    catalog.cache.clock = lambda: now[0]
    catalog.settings.evidence_ttl_seconds = 10
    args = {"machine_ref": machine, "assembly_ref": entries[0]["assembly_ref"]}
    await catalog.execute("get_assembly", args)
    catalog.settings.evidence_ttl_seconds = 30 * 86400
    now[0] += 11
    assert navigation.read(catalog.cache.connection, machine)["complete"]
    await catalog.execute("get_assembly", args)
    assert catalog.transport.request_count == 2


async def test_failed_publication_keeps_complete_directory(catalog):
    machine, entries = await prepared(catalog)
    previous = navigation.read(catalog.cache.connection, machine)
    conflict = {**entries[0], "title": "conflicting title"}
    with pytest.raises(ValueError, match="Conflicting"):
        navigation.publish(catalog.cache.connection, machine, entries + [conflict], [], [], complete=True)
    assert navigation.read(catalog.cache.connection, machine) == previous
    foreign = deepcopy(entries)
    foreign[0]["assembly_ref"]["context"] = "foreign"
    with pytest.raises((ValueError, PartsError)):
        navigation.publish(catalog.cache.connection, machine, foreign, [], [], complete=True)
    assert navigation.read(catalog.cache.connection, machine) == previous


async def test_broken_route_is_stale_and_not_retried_within_lookup(catalog, monkeypatch):
    machine, entries = await prepared(catalog)
    attempts = []

    async def broken(url):
        attempts.append(url)
        raise PartsError("source_not_found", "Broken observed route")

    monkeypatch.setattr(catalog.transport, "fetch", broken)
    client = PartsTools(catalog)
    args = client.encode_references({"machine_ref": machine, "assembly_ref": entries[0]["assembly_ref"]})
    assert "error" in await client.tool_call("get_assembly", args)
    assert "error" in await client.tool_call("get_assembly", args)
    assert len(attempts) == 1
    directory = navigation.read(catalog.cache.connection, machine)
    assert not directory["complete"] and directory["entries"][0]["stale"]
    match = {
        "assembly_ref": entries[0]["assembly_ref"],
        "assembly_label": entries[0]["title"],
        "assembly_url": entries[0]["assembly_url"],
    }
    navigation.retain_matches(catalog.cache.connection, machine, [match], [])
    assert not navigation.read(catalog.cache.connection, machine)["entries"][0]["stale"]


async def test_checkpoint_resume_does_not_refetch_completed_requests(catalog):
    url = CASECE.home + "/test"
    first = await checkpoint_fetch(catalog, url, 1)
    second = await checkpoint_fetch(catalog, url, 1)
    assert first == second and catalog.transport.request_count == 1
    with pytest.raises(BatchComplete):
        await checkpoint_fetch(catalog, url + "2", 1)


async def test_saved_contents_import_and_interrupted_catalog(catalog, monkeypatch, tmp_path):
    import hashlib
    from datetime import datetime
    from urllib.parse import quote

    from cnh_parts_agent.catalog.transport import SourceResponse

    machine, entries = await prepared(catalog)
    rows = json.loads((FIXTURES / "figure.json").read_text())["results"]
    groups = list(dict.fromkeys(row["functionalGroupCode"] for row in rows))
    folder = tmp_path / "snapshots" / machine["id"]
    folder.mkdir(parents=True)
    body = (
        '<div id="assembly-model-page-content"><h1>Fixture catalog</h1>'
        + "".join(f'<span data-functionalgroupcode="{g}">Group</span>' for g in groups)
        + '</div><div id="bread-in-model-page" data-serie-code="NA_TEST"></div>'
    ).encode()
    source = {
        "url": parse.ORIGIN + machine["path"],
        "observed_at": "2026-01-01T00:00:00+00:00",
        "content_hash": "sha256:" + hashlib.sha256(body).hexdigest(),
    }
    (folder / "machine.html").write_bytes(body)
    (folder / "machine-source.json").write_text(json.dumps(source))
    expected = []
    for i, group in enumerate(groups):
        group_rows = [row for row in rows if row["functionalGroupCode"] == group]
        capture = {
            **source,
            "url": CASECE.home + "/cn/findAssembly/functionalGroupCode/" + quote(group, safe="") + "/NA_TEST",
            "rows": group_rows,
        }
        (folder / f"group-{i:02d}.json").write_text(json.dumps(capture))
        for row in group_rows:
            entry = next(e for e in entries if e["assembly_ref"]["id"] == row["assemblyCode"])
            expected.append({"ref": entry["assembly_ref"], "title": entry["title"], "group": group})
    (folder / "contents.json").write_text(json.dumps({"machine": machine, "entries": expected}))
    assert import_snapshots(catalog, folder.parent) == 1
    previous = navigation.read(catalog.cache.connection, machine)

    async def page(url):
        catalog.transport.request_count += 1
        return SourceResponse(
            url, body, datetime.fromisoformat(source["observed_at"]), source["content_hash"]
        )

    monkeypatch.setattr(catalog.transport, "fetch", page)
    with pytest.raises(BatchComplete):
        await prepare_catalog(catalog, machine, 1)
    assert navigation.read(catalog.cache.connection, machine) == previous
    (folder / "machine.html").write_bytes(body + b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        import_snapshots(catalog, folder.parent)
    assert navigation.read(catalog.cache.connection, machine) == previous


async def test_ih_alias_preserves_existing_catalog(catalog):
    assert catalog.candidates("IH 1420") == catalog.candidates("Case IH 1420")
    assert catalog.candidates("IH 1470") == catalog.candidates("Case IH 1470")
    assert catalog.candidates("IH 1420")


async def test_observed_empty_assembly_slug_is_preserved_and_scoped(catalog):
    machine, entries = await prepared(catalog)
    row = json.loads((FIXTURES / "figure.json").read_text())["results"][0]
    segments = row["assemblyUrl"].split("/")
    segments[7] = ""
    row["assemblyUrl"] = "/".join(segments)
    group = row["functionalGroupCode"]
    observed = navigation.from_rows(machine, [row], group, CASECE)
    path = observed[0]["assembly_ref"]["path"]
    assert "//cn/" in path
    assert observed[0]["observed_path"] == row["assemblyUrl"]
    navigation.publish(catalog.cache.connection, machine, observed, [], [], complete=True)
    assert navigation.read(catalog.cache.connection, machine)["entries"] == observed
    assert catalog.transport.request_count == 0
    for invalid in (
        path.replace("/na/", "/na//"),
        path.replace("//cn/", "/../cn/"),
        path.replace("//cn/", "/%2e%2e/cn/"),
        path.replace("//cn/", "///cn/"),
        path.rsplit("/", 1)[0],
    ):
        with pytest.raises(PartsError):
            parse.source_link(invalid)
    foreign = deepcopy(observed)
    foreign[0]["assembly_ref"]["context"] = "another-catalog"
    with pytest.raises((PartsError, ValueError)):
        navigation.publish(catalog.cache.connection, machine, foreign, [], [], complete=True)


def test_observed_leading_hyphen_catalog_slug():
    path = (
        CASEIH.home_path + "/na/crop-production-equipment/tillage/naab85til041seedbedprep"
        "/-dmi/cn/A2D96496-EFBE-E111-9FCE-005056875BD6"
    )
    assert parse.source_link(path, storefront=CASEIH) == parse.ORIGIN + path
    for unsafe in ("../dmi", "%2e%2e", "//evil.test"):
        with pytest.raises(PartsError):
            parse.source_link(CASEIH.home_path + "/" + unsafe, storefront=CASEIH)


class DirectoryModel(ScriptedCatalogModel):
    def _generate(self, messages, *args, **kwargs):
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        inspected = [m for m in messages if isinstance(m, ToolMessage) and m.name == "get_assembly"]
        if inspected:
            payload = json.loads(inspected[-1].content)
            row = payload["data"]["occurrences"][0]
            content = json.dumps(
                {"key_facts": [row["part_number"]], "sources": [row["assembly_url"]], "store_url": ""}
            )
            message = AIMessage(content=content)
        else:
            nav = next(
                m.content
                for m in messages
                if isinstance(m.content, str) and m.content.startswith("Host local navigation")
            )
            directory = json.loads(nav.split(": ", 1)[1])
            message = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_assembly",
                        "id": "inspect",
                        "type": "tool_call",
                        "args": {
                            "machine_ref": directory["machine_ref"],
                            "assembly_ref": directory["assemblies"][0]["assembly_ref"],
                            "limit": 200,
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=message)])


async def test_real_graph_uses_directory_and_keeps_host_actions_free(catalog, monkeypatch):
    from cnh_parts_agent.agent.specialist import retrieve_parts

    _, entries = await prepared(catalog)
    followup = deepcopy(entries[0])
    followup["assembly_ref"]["id"] = "followup"
    monkeypatch.setattr(navigation, "figure_routes", lambda *_: [followup])
    for expected in (1, 1):
        client = PartsTools(catalog)
        handoff = await retrieve_parts(
            client, DirectoryModel(), machine_query="CX350B", part_query="radiator"
        )
        assert handoff.sources and client.budget.calls == 1
        assert catalog.transport.request_count == expected


async def test_large_directory_local_filter_pagination_and_empty_group(catalog):
    machine, entries = await prepared(catalog)
    rows = []
    for i in range(401):
        entry = deepcopy(entries[0])
        ref = entry["assembly_ref"]
        ref["path"] = ref["path"].replace("/cn/" + ref["id"] + "/", f"/cn/fixture-{i}/")
        ref["id"] = f"fixture-{i}"
        entry.update(title=f"Figure {i}", group="engine", assembly_url=parse.ORIGIN + ref["path"])
        rows.append(entry)
    navigation.publish(catalog.cache.connection, machine, rows, [], [], complete=True)
    first = await catalog.execute("list_assemblies", {"machine_ref": machine, "limit": 200})
    last = await catalog.execute("list_assemblies", {"machine_ref": machine, "limit": 200, "offset": 400})
    empty = await catalog.execute("list_assemblies", {"machine_ref": machine, "group": "empty"})
    assert first["total"] == 401 and first["truncated"]
    assert len(last["data"]["assemblies"]) == 1 and not last["truncated"]
    assert empty["total"] == 0 and empty["coverage"]["status"] == "complete_for_scope"
    assert catalog.transport.request_count == 0


async def test_empty_evidence_still_expires_after_one_hour(catalog, monkeypatch):
    now = [0]
    catalog.cache.clock = lambda: now[0]
    machine, _ = await prepared(catalog)
    original = catalog.transport.fetch

    async def empty(url):
        from dataclasses import replace

        return replace(await original(url), body=(FIXTURES / "negative.json").read_bytes())

    monkeypatch.setattr(catalog.transport, "fetch", empty)
    args = {"machine_ref": machine, "query": "missing", "mode": "part_description"}
    assert (await catalog.execute("search_catalog", args))["total"] == 0
    now[0] = 3599
    await catalog.execute("search_catalog", args)
    assert catalog.transport.request_count == 1
    now[0] = 3600
    await catalog.execute("search_catalog", args)
    assert catalog.transport.request_count == 2


async def test_lookup_pagination_reuses_observation_even_when_cache_expires(catalog):
    machine, entries = await prepared(catalog)
    now = [0]
    catalog.cache.clock = lambda: now[0]
    catalog.settings.evidence_ttl_seconds = 1
    client = PartsTools(catalog)
    args = client.encode_references({"machine_ref": machine, "assembly_ref": entries[0]["assembly_ref"]})
    first = await client.tool_call("get_assembly", {**args, "limit": 1})
    assert first["truncated"]
    now[0] += 2
    second = await client.tool_call("get_assembly", {**args, "limit": 1, "offset": 1})
    assert second["data"]["occurrences"] != first["data"]["occurrences"]
    assert catalog.transport.request_count == 1


async def test_incomplete_catalog_resumes_and_publishes_empty_group(catalog, monkeypatch):
    import hashlib
    from datetime import UTC, datetime

    from cnh_parts_agent.catalog.transport import SourceResponse

    machine = (await catalog.execute("search_machines", {"query": "CX350B"}))["data"]["machines"][0][
        "machine_ref"
    ]
    group = machine["id"] + "_01_ENGINE"
    body = (
        f'<div id="assembly-model-page-content"><h1>Model</h1>'
        f'<span data-functionalgroupcode="{group}">Engine</span></div>'
        '<div id="bread-in-model-page" data-serie-code="NA_TEST"></div>'
    ).encode()

    async def fetch(url):
        catalog.transport.request_count += 1
        raw = b"[]" if "/findAssembly/" in url else body
        return SourceResponse(url, raw, datetime.now(UTC), "sha256:" + hashlib.sha256(raw).hexdigest())

    monkeypatch.setattr(catalog.transport, "fetch", fetch)
    with pytest.raises(BatchComplete):
        await prepare_catalog(catalog, machine, 1)
    assert navigation.read(catalog.cache.connection, machine) is None
    await prepare_catalog(catalog, machine, 2)
    directory = navigation.read(catalog.cache.connection, machine)
    assert directory["complete"] and directory["entries"] == [] and len(directory["groups"]) == 1
    assert catalog.transport.request_count == 2


class UninspectedProductModel(ScriptedCatalogModel):
    sources_only: bool = False

    def _generate(self, messages, *args, **kwargs):
        from langchain_core.messages import AIMessage, ToolMessage
        from langchain_core.outputs import ChatGeneration, ChatResult

        for message in reversed(messages):
            if isinstance(message, ToolMessage) and message.name == "search_catalog":
                row = next(r for r in json.loads(message.content)["data"]["matches"] if r["product_url"])
                handoff = {
                    "key_facts": [row["part_number"]],
                    "sources": [row["assembly_url"]],
                    "store_url": row["product_url"],
                }
                if self.sources_only:
                    handoff["sources"].append(handoff["store_url"])
                    handoff["store_url"] = ""
                return ChatResult(
                    generations=[ChatGeneration(message=AIMessage(content=json.dumps(handoff)))]
                )
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.parametrize("sources_only", [False, True])
async def test_product_selection_requires_cited_inspection(catalog, monkeypatch, sources_only):
    from cnh_parts_agent.agent.specialist import retrieve_parts

    execute = catalog.execute

    async def search_with_product(operation, arguments, **kwargs):
        result = await execute(operation, arguments, **kwargs)
        if operation == "search_catalog":
            row = result["data"]["matches"][0]
            row["product_url"] = CASECE.home + "/category/fixture/p/" + row["part_number"]
        return result

    monkeypatch.setattr(catalog, "execute", search_with_product)
    with pytest.raises(ValueError, match="cited inspected assembly"):
        await retrieve_parts(
            PartsTools(catalog),
            UninspectedProductModel(sources_only=sources_only),
            machine_query="CX350B",
            part_query="radiator",
        )


def test_figure_references_resolve_observed_ranges_without_component_heuristics():
    directory = {
        "entries": [
            {"title": title}
            for title in (
                "7-54 - VALVE",
                "7-58 - ACTUATOR",
                "7-68 - ACTUATOR",
                "8-58 - OTHER",
                "7-54.2 - OTHER",
            )
        ]
    }
    rows = [{"description": "See Figures 7-58 through 7-68", "notes": ["parts list Figure 7-54"]}]
    assert [e["title"] for e in navigation.figure_routes(directory, rows)] == [
        "7-54 - VALVE",
        "7-58 - ACTUATOR",
        "7-68 - ACTUATOR",
    ]
    assert navigation.figure_routes(directory, [{"description": "Part 7-54, quantity 58", "notes": []}]) == []
    assert navigation.figure_routes(directory, [{"description": "Figure 7-54.2", "notes": []}]) == []


async def test_group_handles_browse_compact_titles_without_network(catalog):
    from cnh_parts_agent.agent.schemas import TOOL_RESPONSE_MODELS
    from cnh_parts_agent.agent.tools import create_langchain_tools

    machine, entries = await prepared(catalog)
    group = {
        "source_id": machine["source_id"],
        "kind": "group",
        "id": machine["id"] + "_02_ENGINE",
        "context": machine["id"],
        "path": None,
    }
    for entry in entries:
        entry["group"] = group["id"]
    navigation.publish(
        catalog.cache.connection,
        machine,
        entries,
        [{"group_ref": group, "label": "Engine"}],
        [],
        complete=True,
    )
    client = PartsTools(catalog)
    handles = client.encode_references({"machine_ref": machine, "group_ref": group})
    tool = next(t for t in create_langchain_tools(client, url_handles={}) if t.name == "list_assemblies")
    result = await tool.ainvoke(handles)
    TOOL_RESPONSE_MODELS["list_assemblies"].model_validate(result)
    assert result["total"] == len(entries)
    assert set(result["data"]["assemblies"][0]) == {"assembly_ref", "title"}
    assert client.observations[-1]["response"]["data"]["assemblies"][0]["assembly_url"]
    invalid = await tool.ainvoke({**handles, "group_ref": handles["machine_ref"]})
    assert invalid["error"]["code"] == "invalid_reference"
    assert catalog.transport.request_count == 0
