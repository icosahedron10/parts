"""Offline New Holland routing and staged collection; fixtures are namespace adaptations."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cnh_parts_agent.agent.lookup_tool import PartsLookup
from cnh_parts_agent.catalog import navigation, operations, parse
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.models import expand_references
from cnh_parts_agent.catalog.storefront import NEWHOLLANDAG, NEWHOLLANDCE, get_storefront
from cnh_parts_agent.catalog.transport import SourceResponse
from cnh_parts_agent.discovery.index import validate_index
from cnh_parts_agent.discovery.match import prepare
from scripts import prepare_navigation as prep
from tests.conftest import FIXTURES


def adapted_index(indexes, brand):
    index = json.loads(json.dumps(indexes["casece"], default=str).replace("casece", brand))
    index.pop("prepared", None)
    index["records"] = [
        r
        for r in index["records"]
        if r["machine"]["machine_ref"]["$ref"]["id"] == "6EDA6496-EFBE-E111-9FCE-005056875BD6"
    ]
    validate_index(index)
    index["prepared"] = prepare(index["records"])
    return index


@pytest.mark.parametrize("storefront", [NEWHOLLANDAG, NEWHOLLANDCE])
async def test_new_holland_source_scope_and_discovery(catalog, indexes, storefront):
    index = adapted_index(indexes, storefront.brand)
    catalog.indexes = {**catalog.indexes, storefront.brand: index}
    label = index["records"][0]["machine"]["label"]
    model = label.split(" - ")[0]
    assert catalog.candidates("New Holland " + model)
    assert all(brand == storefront.brand for brand, _ in catalog.candidates("New Holland " + model))
    assert catalog.candidates(model, storefront.brand)
    assert all(brand in {"casece", "caseih"} for brand, _ in catalog.candidates("CASE " + model))
    with pytest.raises(PartsError, match="conflicts"):
        catalog.candidates("CASE " + model, storefront.brand)
    PartsLookup(machine_query=model, part_query="radiator", brand=storefront.brand)
    machine = expand_references(index["records"][0]["machine"], storefront.source_id)["machine_ref"]
    operations.validate_reference(machine, storefront=storefront)
    with pytest.raises(PartsError):
        operations.validate_reference(machine, storefront=get_storefront("casece"))
    with pytest.raises(PartsError, match="conflicts"):
        catalog.candidates("New Holland " + model, "casece")
    body = (FIXTURES / "model.html").read_bytes().replace(b"casece", storefront.brand.encode())
    assert parse.groups(body, machine["id"])["groups"]
    assert catalog.transport.request_count == 0


async def test_new_holland_missing_index_does_not_return_case_candidates(catalog):
    with pytest.raises(PartsError, match="not installed"):
        catalog.candidates("New Holland CX350B")


async def test_new_holland_waits_for_caseih_and_shares_batch_transport(
    catalog, indexes, tmp_path, monkeypatch
):
    catalog.indexes = {}
    monkeypatch.setattr(prep, "Catalog", lambda settings: catalog)
    complete = set()
    events = []
    stop_caseih = [True]

    async def fetch(client, url, limit):
        assert client is catalog
        events.append(("fetch", url.split("/")[5]))
        client.transport.request_count += 1
        return SourceResponse(url, b"", datetime.now(UTC), "sha256:" + "0" * 64)

    def series(body, url, storefront):
        record = adapted_index(indexes, storefront.brand)["records"][0]
        return 1, [record["machine"]], []

    async def prepare_catalog(client, machine, limit):
        brand = machine["source_id"].removesuffix("-us-en")
        events.append(("prepare", brand))
        if brand == "caseih" and stop_caseih[0]:
            raise prep.BatchComplete
        complete.add(machine["source_id"])

    monkeypatch.setattr(prep, "checkpoint_fetch", fetch)
    monkeypatch.setattr(prep, "sitemap_series", lambda body, storefront: [storefront.home + "/series"])
    monkeypatch.setattr(prep, "series_page", series)
    monkeypatch.setattr(prep, "prepare_catalog", prepare_catalog)
    monkeypatch.setattr(
        navigation, "read", lambda connection, machine: {"complete": machine["source_id"] in complete}
    )
    args = SimpleNamespace(
        max_requests=250, snapshots=None, index_dir=tmp_path / "indexes", report=tmp_path / "first.json"
    )
    result = await prep.run(args)
    assert not result["complete"]
    assert not any(brand.startswith("newholland") for _, brand in events)
    # The fixture catalog has closed; reopen for the resumed batch, retaining completion state.
    from cnh_parts_agent.catalog.client import Catalog
    from cnh_parts_agent.settings import Settings

    resumed = Catalog(Settings(cache_path=tmp_path / "resumed.sqlite"), indexes={})
    catalog = resumed
    stop_caseih[0] = False
    events.clear()
    args.report = tmp_path / "second.json"
    result = await prep.run(args)
    assert result["complete"]
    assert events.index(("prepare", "caseih")) < events.index(("fetch", "newhollandag"))
    assert events.index(("prepare", "newhollandag")) < events.index(("fetch", "newhollandce"))
    assert result["request_interval_seconds"] == [1, 1.25]
    assert result["http_attempts"] == 8


def test_new_holland_decimal_model_identity_and_missing_variant(indexes):
    from copy import deepcopy

    from cnh_parts_agent.discovery.match import match

    record = adapted_index(indexes, "newhollandag")["records"][0]
    records = []
    for model in ["T7.270", "T7.230", "T4.75"]:
        row = deepcopy(record)
        row["machine"]["label"] = model + " - TRACTOR"
        row["machine"]["production_dates"] = None
        records.append(row)
    prepared = prepare(records)
    for model in ["T7.270", "T7.230", "T4.75"]:
        assert [r["machine"]["label"] for r in match(prepared, "New Holland " + model)] == [
            model + " - TRACTOR"
        ]
    assert match(prepared, "New Holland T7.271") == []


@pytest.mark.parametrize("storefront", [NEWHOLLANDAG, NEWHOLLANDCE])
async def test_new_holland_parts_lookup_and_warm_cache_use_scoped_transport(indexes, tmp_path, storefront):
    import httpx

    from cnh_parts_agent.catalog.client import Catalog
    from cnh_parts_agent.catalog.transport import SourceTransport
    from cnh_parts_agent.settings import Settings
    from tests.conftest import Clock

    def respond(request):
        assert request.url.path.startswith(storefront.home_path + "/")
        name = "figure.json" if "searchInModel" in request.url.path else "assembly-selected.html"
        body = (FIXTURES / name).read_bytes().replace(b"casece", storefront.brand.encode())
        return httpx.Response(200, content=body)

    clock = Clock()
    transport = SourceTransport(
        "offline-new-holland",
        http_transport=httpx.MockTransport(respond),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0,
    )
    catalog = Catalog(
        Settings(cache_path=tmp_path / "nh.sqlite"),
        transport=transport,
        indexes={storefront.brand: adapted_index(indexes, storefront.brand)},
    )
    try:
        machine = (await catalog.execute("search_machines", {"query": "New Holland CX350B"}))["data"][
            "machines"
        ][0]["machine_ref"]
        matches = await catalog.execute(
            "search_catalog", {"machine_ref": machine, "query": "radiator", "mode": "assembly_title"}
        )
        assembly = matches["data"]["matches"][0]["assembly_ref"]
        args = {"machine_ref": machine, "assembly_ref": assembly}
        first = await catalog.execute("get_assembly", args)
        assert first["data"]["occurrences"]
        assert all(row["assembly_url"].startswith(storefront.home) for row in first["data"]["occurrences"])
        count = transport.request_count
        assert await catalog.execute("get_assembly", args) == first
        assert transport.request_count == count == 2
    finally:
        await catalog.aclose()
