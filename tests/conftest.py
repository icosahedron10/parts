"""Offline tests cannot accidentally contact the catalog or a model provider."""

import socket
from pathlib import Path

import httpx
import pytest

from cnh_parts_agent.catalog.client import Catalog
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.discovery.index import load_indexes
from cnh_parts_agent.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures/casece"


class Clock:
    def __init__(self):
        self.time = 1000.0

    def now(self):
        return self.time

    async def sleep(self, seconds):
        self.time += seconds


@pytest.fixture(autouse=True)
def offline(request, monkeypatch, tmp_path):
    if request.node.get_closest_marker("live"):
        return
    # Tests must never open a live runner's cache, including through WSL's filesystem.
    monkeypatch.setenv("PARTS_CACHE_PATH", str(tmp_path / "default-catalog.sqlite"))
    for name in ("connect", "connect_ex"):
        original = getattr(socket.socket, name)

        def denied(sock, address, _original=original):
            # Windows asyncio creates its wakeup socket pair over loopback.
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return _original(sock, address)
            raise AssertionError("External network is forbidden in offline tests")

        monkeypatch.setattr(socket.socket, name, denied)


@pytest.fixture(scope="session")
def indexes():
    return load_indexes()


@pytest.fixture
async def catalog(tmp_path, indexes):
    def respond(request):
        path = request.url.path
        if "searchInModel" in path:
            name = {
                "figure": "figure.json",
                "part-description": "description.json",
                "part-number": "number.json",
            }[path.rsplit("/", 1)[-1]]
        elif "assemblyReman" in path:
            name = "sub-to.html"
        elif "/cn/" in path and path.rsplit("/cn/", 1)[1].count("/") == 1:
            name = "assembly-selected.html"
        else:
            name = "model.html"
        return httpx.Response(200, content=(FIXTURES / name).read_bytes())

    clock = Clock()
    transport = SourceTransport(
        "parts-offline-test",
        http_transport=httpx.MockTransport(respond),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0,
    )
    client = Catalog(Settings(cache_path=tmp_path / "cache.sqlite"), transport=transport, indexes=indexes)
    try:
        yield client
    finally:
        await client.aclose()
