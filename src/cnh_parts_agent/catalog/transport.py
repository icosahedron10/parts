"""One bounded, paced HTTP transport shared by all lookups in a process."""

import asyncio
import hashlib
import logging
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit

import httpx

from ..diagnostics import current
from ..settings import Settings
from .errors import PartsError

LOG = logging.getLogger(__name__)


def validate_source_url(url: str) -> str:
    try:
        p = urlsplit(url)
        valid = (
            p.scheme == "https"
            and p.hostname == "www.mycnhstore.com"
            and p.port in (None, 443)
            and not p.username
            and not p.password
            and not p.fragment
            and not any(ord(c) < 33 or c == "\\" for c in url)
            and all(s not in {".", ".."} for s in p.path.split("/"))
        )
    except ValueError:
        valid = False
    if not valid:
        raise PartsError("invalid_source_url", "Only HTTPS MyCNHStore URLs are allowed.")
    return url


@dataclass(frozen=True)
class SourceResponse:
    url: str
    body: bytes
    observed_at: datetime
    content_hash: str


class SourceTransport:
    def __init__(
        self,
        user_agent: str,
        *,
        settings: Settings | None = None,
        http_transport=None,
        clock=time.monotonic,
        sleep=asyncio.sleep,
        jitter=None,
    ):
        settings = settings or Settings()
        self.operation_timeout_seconds = settings.source_operation_timeout_seconds
        self.max_response_bytes = settings.source_max_response_bytes
        self.client = httpx.AsyncClient(
            headers={"User-Agent": user_agent},
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(
                settings.source_read_timeout_seconds,
                connect=settings.source_connect_timeout_seconds,
                pool=settings.source_connect_timeout_seconds,
            ),
            transport=http_transport,
        )
        self.clock, self.sleep = clock, sleep
        self.jitter = jitter or (lambda: random.uniform(0, 0.250))
        self.lock = asyncio.Lock()
        self.next_start = 0.0
        self.cooldown_until = 0.0
        self.denials = deque()
        self.request_count = 0

    def _cooldown(self, response: httpx.Response) -> float:
        value = response.headers.get("Retry-After", "60")
        try:
            seconds = float(value)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 60
        if not 0 <= seconds < float("inf"):
            seconds = 60
        now = self.clock()
        self.cooldown_until = max(self.cooldown_until, now + seconds)
        if response.status_code in (403, 429):
            self.denials.append(now)
            while self.denials and self.denials[0] < now - 600:
                self.denials.popleft()
            if len(self.denials) >= 3:
                self.cooldown_until = max(self.cooldown_until, now + 900)
        return self.cooldown_until - now

    async def fetch(self, url: str, *, timeout: float | None = None) -> SourceResponse:
        validate_source_url(url)
        timeout = (
            self.operation_timeout_seconds
            if timeout is None
            else min(self.operation_timeout_seconds, timeout)
        )
        deadline = self.clock() + timeout
        try:
            async with asyncio.timeout(timeout):
                async with self.lock:
                    return await self._fetch(url, deadline)
        except TimeoutError:
            raise PartsError(
                "source_timeout", "Catalog operation exceeded its deadline.", 504, retryable=True
            ) from None

    async def _fetch(self, url: str, deadline: float) -> SourceResponse:
        attempts, redirects = 0, 0
        while attempts < 3:
            delay = max(self.next_start, self.cooldown_until) - self.clock()
            if self.clock() + max(delay, 0) >= deadline:
                raise PartsError(
                    "source_cooldown",
                    "Source backoff exceeds the operation deadline.",
                    503,
                    retryable=True,
                    retry_after=max(delay, 0),
                )
            if delay > 0:
                await self.sleep(delay)
            validate_source_url(url)
            started = self.clock()
            self.next_start = started + 1 + self.jitter()
            self.request_count += 1
            attempts += 1
            record = {"url": url, "status": None, "latency_seconds": None, "decoded_bytes": 0}
            try:
                async with self.client.stream("GET", url) as response:
                    record["status"] = response.status_code
                    if response.status_code in (403, 429, 503):
                        retry_after = self._cooldown(response)
                        if response.status_code != 503 or attempts == 3:
                            raise PartsError(
                                "source_cooldown",
                                "Source denied or throttled the request.",
                                response.status_code,
                                retryable=True,
                                retry_after=retry_after,
                            )
                        continue
                    if response.status_code in (301, 302, 303, 307, 308):
                        redirects += 1
                        if redirects > 3 or not response.headers.get("Location"):
                            raise PartsError("invalid_redirect", "Source redirect chain is invalid.", 502)
                        url = validate_source_url(urljoin(url, response.headers["Location"]))
                        # Redirect hops are bounded separately and obey the same pacing/deadline.
                        attempts -= 1
                        continue
                    if response.status_code >= 500 and attempts < 3:
                        continue
                    if response.status_code != 200:
                        raise PartsError(
                            "source_http_error",
                            f"Catalog returned HTTP {response.status_code}.",
                            response.status_code,
                            retryable=response.status_code >= 500,
                        )
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        record["decoded_bytes"] += len(chunk)
                        if record["decoded_bytes"] > self.max_response_bytes:
                            raise PartsError(
                                "source_response_too_large",
                                f"Decoded response exceeds the configured {self.max_response_bytes:,}-byte limit.",
                                502,
                            )
                        body.extend(chunk)
                    raw = bytes(body)
                    return SourceResponse(
                        str(response.url), raw, datetime.now(UTC), "sha256:" + hashlib.sha256(raw).hexdigest()
                    )
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if attempts == 3:
                    raise PartsError(
                        "source_unavailable", "Could not connect to the catalog.", 503, retryable=True
                    ) from None
            except httpx.HTTPError as exc:
                raise PartsError(
                    "source_unavailable",
                    f"Catalog transport failed: {type(exc).__name__}.",
                    503,
                    retryable=True,
                ) from None
            finally:
                record["latency_seconds"] = round(self.clock() - started, 4)
                if (diagnostics := current.get()) is not None:
                    diagnostics.requests.append(record)
                LOG.info("catalog_request %s", record)
        raise PartsError("source_unavailable", "Catalog attempts exhausted.", 503, retryable=True)

    async def aclose(self):
        await self.client.aclose()
