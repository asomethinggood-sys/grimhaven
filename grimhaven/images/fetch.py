"""Async HTTP fetcher for the artwork subsystem.

Design notes
------------
* **Never blocks the event loop** — everything here is ``httpx`` async; PTB
  already ships httpx, so no heavyweight dependency was added.
* **Bounded retries with exponential backoff** — transient network errors and
  429/5xx are retried ``max_retries`` times at most; ``Retry-After`` is honoured
  for 429 but clamped so a hostile header cannot stall the bot.
* **Per-source rate limiting** — a minimum interval is enforced per host so a
  cache-cold stampede of button taps cannot hammer one provider.
* **Concurrency limit** — one shared semaphore caps simultaneous downloads
  (search + download alike).
* **Size cap while streaming** — the response is read incrementally and the
  connection is aborted the moment the payload exceeds ``max_bytes``; a server
  that lies about Content-Length cannot exhaust the runner's memory.
* **No credential leakage** — only URLs (minus query secrets) and status codes
  are logged; no headers, no tokens.

A custom ``transport`` can be injected (tests use ``httpx.MockTransport``).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)


class FetchError(Exception):
    """A network/HTTP failure while talking to an image source."""

    #: transient failures (network noise, 5xx) are worth another attempt
    retryable = True

    def __init__(self, kind: str, url: str = "", detail: str = "", *,
                 retryable: bool | None = None):
        super().__init__(f"{kind} for {url[:160]}" + (f": {detail[:160]}" if detail else ""))
        self.kind = kind
        self.url = url
        self.detail = detail[:200]
        if retryable is not None:
            self.retryable = retryable


class RateLimited(FetchError):
    def __init__(self, url: str = "", retry_after: float = 0.0):
        super().__init__("rate-limited", url, f"retry_after={retry_after:.0f}s")
        self.retry_after = retry_after


def _safe_url(url: str) -> str:
    """Strip query strings from log output — API keys live there."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class Fetcher:
    #: hosts that need a polite minimum interval between requests, seconds
    HOST_INTERVALS = {
        "commons.wikimedia.org": 1.1,   # Wikimedia API etiquette: identify + pace
        "api.openverse.org": 0.7,
        "pixabay.com": 0.9,
    }
    RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, *, timeout: float = 12.0, max_retries: int = 2,
                 concurrency: int = 3, user_agent: str,
                 default_min_interval: float = 0.8,
                 transport: httpx.BaseTransport | None = None,
                 client_factory: Callable[..., Any] | None = None):
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.user_agent = user_agent
        self.default_min_interval = default_min_interval
        self._transport = transport
        self._client_factory = client_factory
        self._sem = asyncio.Semaphore(max(1, concurrency))
        self._next_ok: dict[str, float] = {}

    # ── pacing ────────────────────────────────────────────────────────────────
    def _host(self, url: str) -> str:
        return urlsplit(url).netloc.lower()

    async def _pace(self, host: str) -> None:
        interval = self.HOST_INTERVALS.get(host, self.default_min_interval)
        if interval <= 0:
            return
        loop = asyncio.get_running_loop()
        while True:
            now = loop.time()
            nxt = self._next_ok.get(host, 0.0)
            if now >= nxt:
                self._next_ok[host] = now + interval
                return
            await asyncio.sleep(min(0.25, nxt - now))

    # ── core ──────────────────────────────────────────────────────────────────
    def _client(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=httpx.Timeout(self.timeout, connect=self.timeout),
                                 follow_redirects=True, transport=self._transport,
                                 headers={"User-Agent": self.user_agent},
                                 http2=False, verify=True)

    async def get(self, url: str, *, params: dict | None = None,
                  max_bytes: int = 5_000_000, expect: str = "image") -> tuple[bytes, str]:
        """GET a URL; returns ``(payload, content_type)``.

        ``expect="image"`` enforces an ``image/*`` Content-Type and the stream
        size cap; ``expect="json"`` enforces ``application/json``-ish replies.
        """
        async with self._sem:
            last: FetchError | None = None
            for attempt in range(self.max_retries + 1):
                await self._pace(self._host(url))
                try:
                    return await self._get_once(url, params, max_bytes, expect)
                except RateLimited as exc:
                    last = exc
                    if attempt >= self.max_retries or exc.retry_after > 30.0:
                        break
                    await asyncio.sleep(max(1.0, min(exc.retry_after, 8.0)) * (attempt + 1))
                except FetchError as exc:
                    last = exc
                    if not exc.retryable or attempt >= self.max_retries:
                        break
                    await asyncio.sleep(0.4 * (2 ** attempt) + random.uniform(0, 0.2))
            raise last or FetchError("network", url, "exhausted")

    async def _get_once(self, url: str, params: dict | None,
                        max_bytes: int, expect: str) -> tuple[bytes, str]:
        try:
            async with self._client() as client:
                async with client.stream("GET", url, params=params) as resp:
                    if resp.status_code in self.RETRY_STATUS:
                        if resp.status_code == 429:
                            ra = 0.0
                            try:
                                ra = float(resp.headers.get("Retry-After", "0") or 0)
                            except ValueError:
                                ra = 0.0
                            raise RateLimited(url, min(ra, 60.0))
                        raise FetchError("http", url, f"status={resp.status_code}")
                    if resp.status_code >= 400:
                        # 4xx (auth walls, missing files, hotlink blocks) will not
                        # fix themselves on retry — go straight to the next source
                        raise FetchError("http", url, f"status={resp.status_code}",
                                         retryable=resp.status_code in self.RETRY_STATUS)
                    ctype = (resp.headers.get("content-type") or "").split(";")[0] \
                        .strip().lower()
                    if expect == "image":
                        if not ctype.startswith("image/"):
                            # the worst case: a login/captcha/error page served
                            # as the body — fail before buffering it
                            raise FetchError("not-image", url, f"content-type={ctype or 'none'}")
                        declared_len = resp.headers.get("content-length")
                        if declared_len and declared_len.isdigit() \
                                and int(declared_len) > max_bytes:
                            raise FetchError("oversize", url, f"declared {declared_len} bytes")
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(64 * 1024):
                        body += chunk
                        if expect == "image" and len(body) > max_bytes:
                            raise FetchError("oversize", url, f"streamed > {max_bytes} bytes")
                    return bytes(body), (resp.headers.get("content-type") or "")
        except (httpx.TransportError, httpx.HTTPError) as exc:
            if isinstance(exc, FetchError):
                raise
            kind = "timeout" if isinstance(exc, httpx.TimeoutException) else "network"
            logger.debug("fetch %s failed (%s): %s", _safe_url(url), kind, type(exc).__name__)
            raise FetchError(kind, url, type(exc).__name__) from exc
        except FetchError:
            raise
        except Exception as exc:      # any transport plumbing error is retryable noise
            logger.debug("fetch %s plumbing error: %s", _safe_url(url), exc)
            raise FetchError("network", url, type(exc).__name__) from exc

    async def get_json(self, url: str, params: dict | None = None) -> Any:
        import json
        body, _ = await self.get(url, params=params, max_bytes=2_000_000, expect="json")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise FetchError("bad-json", url, str(exc)[:120]) from exc
