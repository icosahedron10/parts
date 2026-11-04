"""A failed initial fetch is resumable and unobserved records keep their dates."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.transport import SourceResponse
from scripts import census
from tests.test_caseih import FIXTURES


async def test_resume_and_preserved_observation_dates(tmp_path, indexes, monkeypatch):
    previous = {k: v for k, v in indexes["caseih"].items() if k not in {"prepared", "sha256"}}
    output, checkpoint = tmp_path / "index.json", tmp_path / "checkpoint.json"
    census.atomic_json(output, previous)
    args = SimpleNamespace(
        brand="caseih",
        resume=False,
        output=output,
        checkpoint=checkpoint,
        series=["rbx"],
        max_requests=10,
        fixture_dir=None,
    )
    now = datetime.now(UTC)

    class Transport:
        fail = True
        request_count = 0

        def __init__(self, *args):
            pass

        async def aclose(self):
            pass

        async def fetch(self, url):
            self.request_count += 1
            if self.fail:
                raise PartsError("source_unavailable", "offline synthetic interruption")
            name = "sitemap.html" if url.endswith("sitemap-equipment") else "series.html"
            return SourceResponse(url, (FIXTURES / name).read_bytes(), now, "sha256:" + "a" * 64)

    monkeypatch.setattr(census, "SourceTransport", Transport)
    with pytest.raises(PartsError):
        await census.run(args)
    assert not json.loads(checkpoint.read_bytes()).get("initialized")
    Transport.fail = False
    args.resume = True
    await census.run(args)
    refreshed = json.loads(output.read_bytes())
    assert len(refreshed["records"]) == 23
    for old, new in zip(previous["records"], refreshed["records"], strict=True):
        if old["machine"]["label"].startswith("RBX"):
            assert new["evidence"]["observed_at"] == now.isoformat()
        else:
            assert new == old
