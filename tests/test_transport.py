"""Pacing, denial backoff, redirects, byte bounds and cancellation without real time or network."""

import asyncio
import gzip

import httpx
import pytest

from cnh_parts_agent.catalog.errors import PartsError
from cnh_parts_agent.catalog.transport import SourceTransport
from cnh_parts_agent.settings import Settings
from tests.conftest import Clock

URL = "https://www.mycnhstore.com/us/en/casece/sitemap-equipment"


def transport(handler, clock):
    return SourceTransport(
        "offline-tests",
        http_transport=httpx.MockTransport(handler),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0,
    )


async def test_pacing_retries_and_single_inflight():
    clock = Clock()
    starts, active = [], [0]

    async def respond(request):
        assert active[0] == 0
        active[0] += 1
        starts.append(clock.now())
        await asyncio.sleep(0)
        active[0] -= 1
        return httpx.Response(500 if len(starts) == 1 else 200, content=b"ok")

    client = transport(respond, clock)
    try:
        await asyncio.gather(client.fetch(URL), client.fetch(URL))
        assert len(starts) == 3
        assert all(b - a >= 1 for a, b in zip(starts, starts[1:]))
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "operation_timeout,call_timeout,succeeds", [(60, None, True), (12, None, False), (60, 8, False)]
)
async def test_longer_retry_delay_respects_configured_and_call_deadlines(
    operation_timeout, call_timeout, succeeds
):
    clock = Clock()
    initial = clock.now()
    starts = []

    def respond(request):
        starts.append(clock.now())
        if len(starts) == 1:
            return httpx.Response(503, headers={"Retry-After": "15"})
        return httpx.Response(200, content=b"available after cooldown")

    client = SourceTransport(
        "offline-tests",
        settings=Settings(source_operation_timeout_seconds=operation_timeout),
        http_transport=httpx.MockTransport(respond),
        clock=clock.now,
        sleep=clock.sleep,
        jitter=lambda: 0,
    )
    try:
        if succeeds:
            result = await client.fetch(URL, timeout=call_timeout)
            assert result.body == b"available after cooldown"
            assert starts == [initial, initial + 15]
        else:
            with pytest.raises(PartsError) as error:
                await client.fetch(URL, timeout=call_timeout)
            assert error.value.code == "source_cooldown"
            assert starts == [initial] and clock.now() == initial
        assert not client.lock.locked()
    finally:
        await client.aclose()


async def test_environment_resource_limits_reach_http_and_model_clients(monkeypatch, tmp_path):
    from cnh_parts_agent.agent import model as model_module
    from cnh_parts_agent.catalog.client import Catalog

    for name, value in (
        ("SOURCE_CONNECT_TIMEOUT_SECONDS", "11"),
        ("SOURCE_READ_TIMEOUT_SECONDS", "35"),
        ("SOURCE_OPERATION_TIMEOUT_SECONDS", "70"),
        ("TIMEOUT_SECONDS", "200"),
        ("MODEL_TIMEOUT_SECONDS", "65"),
        ("SOURCE_MAX_RESPONSE_BYTES", "16777216"),
        ("MODEL_MAX_OUTPUT_TOKENS", "12288"),
    ):
        monkeypatch.setenv("PARTS_" + name, value)
    settings = Settings(cache_path=tmp_path / "configured.sqlite")
    catalog = Catalog(settings)
    model_options = {}

    def model_factory(**kwargs):
        model_options.update(kwargs)
        return object()

    monkeypatch.setattr(model_module, "ChatOpenAI", model_factory)
    try:
        assert catalog.transport.client.timeout.connect == 11
        assert catalog.transport.client.timeout.read == 35
        assert catalog.transport.operation_timeout_seconds == 70
        assert catalog.transport.max_response_bytes == 16777216
        assert settings.timeout_seconds == 200
        model_module.create_parts_model()
        assert model_options["timeout"] == 65
        assert model_options["max_tokens"] == 12288
    finally:
        await catalog.aclose()


async def test_retry_after_and_three_denial_circuit():
    clock = Clock()
    client = transport(lambda request: httpx.Response(429, headers={"Retry-After": "60"}), clock)
    try:
        for _ in range(3):
            with pytest.raises(PartsError) as error:
                await client.fetch(URL)
            assert error.value.code == "source_cooldown"
            clock.time += 60
        assert client.cooldown_until - clock.now() == 840
        with pytest.raises(PartsError, match="backoff"):
            await client.fetch(URL)
        assert client.request_count == 3
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "http://www.mycnhstore.com/us/en/casece",
        "https://evil.test/",
        "https://www.mycnhstore.com.evil.test/",
        "https://user@www.mycnhstore.com/",
        "https://www.mycnhstore.com:444/",
        "https://www.mycnhstore.com/a/../b",
    ],
)
async def test_invalid_urls_never_reach_http(url):
    client = transport(lambda request: pytest.fail("unexpected HTTP"), Clock())
    try:
        with pytest.raises(PartsError):
            await client.fetch(url)
        assert client.request_count == 0
    finally:
        await client.aclose()


async def test_foreign_redirect_is_rejected_before_following():
    client = transport(
        lambda request: httpx.Response(302, headers={"Location": "https://evil.test/"}), Clock()
    )
    try:
        with pytest.raises(PartsError):
            await client.fetch(URL)
        assert client.request_count == 1
    finally:
        await client.aclose()


@pytest.mark.parametrize("extra", [-1, 0, 1])
async def test_configured_limit_checks_decoded_bytes_and_closes_response(extra):
    limit = 256 * 1024
    body = b"x" * (limit + extra)
    response = httpx.Response(200, content=gzip.compress(body), headers={"Content-Encoding": "gzip"})
    client = SourceTransport(
        "offline-tests",
        settings=Settings(source_max_response_bytes=limit),
        http_transport=httpx.MockTransport(lambda request: response),
    )
    try:
        if extra > 0:
            with pytest.raises(PartsError) as error:
                await client.fetch(URL)
            assert error.value.code == "source_response_too_large"
            assert f"{limit:,}" in str(error.value)
        else:
            assert (await client.fetch(URL)).body == body
        assert response.is_closed and not client.lock.locked()
    finally:
        await client.aclose()


async def test_default_limit_accepts_large_decoded_response_and_records_actual_bytes():
    from cnh_parts_agent.diagnostics import Diagnostics, current

    body = b"catalog content " * (400 * 1024)
    assert len(body) > 4 * 1024 * 1024
    client = transport(
        lambda request: httpx.Response(
            200, content=gzip.compress(body), headers={"Content-Encoding": "gzip"}
        ),
        Clock(),
    )
    diagnostics = Diagnostics()
    token = current.set(diagnostics)
    try:
        result = await client.fetch(URL)
        assert result.body == body
        assert diagnostics.requests[0]["decoded_bytes"] == len(body)
    finally:
        current.reset(token)
        await client.aclose()


async def test_connect_retry_is_bounded_but_read_errors_are_not_retried():
    for exception, expected in ((httpx.ConnectError, 3), (httpx.ReadError, 1)):

        def respond(request):
            raise exception("offline failure")

        client = transport(respond, Clock())
        try:
            with pytest.raises(PartsError):
                await client.fetch(URL)
            assert client.request_count == expected
        finally:
            await client.aclose()


async def test_operation_deadline_and_cancellation_release_lock():
    entered = asyncio.Event()

    async def respond(request):
        entered.set()
        await asyncio.sleep(100)

    client = transport(respond, Clock())
    try:
        task = asyncio.create_task(client.fetch(URL))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not client.lock.locked()
        with pytest.raises(PartsError) as error:
            await client.fetch(URL, timeout=0.02)
        assert error.value.code in {"source_timeout", "source_cooldown"}
        assert not client.lock.locked()
    finally:
        await client.aclose()
