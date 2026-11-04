"""Cache identity, coalescing, pagination, and local discovery behavior."""

import asyncio
import json

import httpx
import pytest

from cnh_parts_agent.catalog.cache import Cache, cache_key
from cnh_parts_agent.catalog.client import Catalog
from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.diagnostics import Diagnostics, current
from cnh_parts_agent.settings import Settings
from tests.conftest import FIXTURES


async def test_discovery_never_uses_network_and_preserves_dates(catalog):
    result = await catalog.execute("search_machines", {"query": "my 2010 CASE CX350B"})
    assert result["total"] == 1
    assert result["coverage"]["status"] == "partial"
    assert result["evidence"][0]["observed_at"].startswith("2026-09-")
    assert catalog.transport.request_count == 0


async def test_coalescing_and_pagination_share_full_parsed_cache(catalog):
    machine = (await catalog.execute("search_machines", {"query": "CX350B"}))["data"]["machines"][0][
        "machine_ref"
    ]
    args = {"machine_ref": machine, "query": "radiator", "mode": "part_description", "limit": 1}
    diagnostics = Diagnostics()
    token = current.set(diagnostics)
    try:
        a, b = await asyncio.gather(
            catalog.execute("search_catalog", args), catalog.execute("search_catalog", args)
        )
        second = await catalog.execute("search_catalog", {**args, "offset": 1})
    finally:
        current.reset(token)
    assert a == b and a["truncated"] and a["total"] > 1
    assert second["data"]["matches"] != a["data"]["matches"]
    assert catalog.transport.request_count == 1
    assert diagnostics.cache.count("miss") == 1
    assert len(catalog.cache.locks) == 0


async def test_large_search_is_fully_parsed_then_cached_and_paginated(tmp_path):
    raw = json.loads((FIXTURES / "figure.json").read_bytes())
    raw["source_display_metadata"] = "x" * (5 * 1024 * 1024)
    body = json.dumps(raw).encode()
    settings = Settings(cache_path=tmp_path / "large.sqlite")
    transport = SourceTransport(
        settings.user_agent,
        settings=settings,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body)),
    )
    catalog = Catalog(settings, transport=transport)
    try:
        discovery = await catalog.execute("search_machines", {"query": "CX350B"})
        machine = discovery["data"]["machines"][0]["machine_ref"]
        args = {"machine_ref": machine, "query": "radiator", "mode": "assembly_title", "limit": 1}
        first = await catalog.execute("search_catalog", args)
        second = await catalog.execute("search_catalog", {**args, "offset": 1})
        assert first["total"] == second["total"] == 2
        assert first["truncated"] and not second["truncated"]
        assert first["data"]["matches"][0] != second["data"]["matches"][0]
        assert transport.request_count == 1
    finally:
        await catalog.aclose()


def test_ttl_and_parser_version(tmp_path):
    now = [10]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    try:
        key = cache_key("casece-us-en", "search_catalog", {"query": "radiator"}, "v1")
        cache.put(key, {"data": "parsed"}, 5)
        assert cache.get(key) == {"data": "parsed"}
        assert cache.get(cache_key("casece-us-en", "search_catalog", {"query": "radiator"}, "v2")) is None
        now[0] = 15
        assert cache.get(key) is None
    finally:
        cache.close()


async def test_conflicting_brand_is_an_error(catalog):
    with pytest.raises(PartsError, match="conflicts"):
        await catalog.execute("search_machines", {"query": "Case IH Magnum 250", "brand": "casece"})


async def test_cancelled_cache_waiter_does_not_leak_lock(tmp_path):
    cache = Cache(tmp_path / "cache.sqlite")
    try:
        async with cache.filling("same"):

            async def wait():
                async with cache.filling("same"):
                    pytest.fail("cancelled waiter acquired lock")

            task = asyncio.create_task(wait())
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cache.locks["same"][1] == 1
        assert cache.locks == {}
    finally:
        cache.close()


async def test_public_catalog_contexts_share_one_runtime(tmp_path):
    from cnh_parts_agent import open_catalog
    from cnh_parts_agent.settings import Settings

    settings = Settings(cache_path=tmp_path / "shared.sqlite")
    async with open_catalog(settings) as first:
        async with open_catalog(settings) as second:
            assert first is second
        assert not first.transport.client.is_closed
    assert first.transport.client.is_closed
