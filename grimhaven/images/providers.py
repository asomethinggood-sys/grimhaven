"""Image *discovery* providers.

Why these sources (Phase-2 investigation, October 2026)
--------------------------------------------------------
* **Pinterest** — deliberately NOT wired up. The official v5 API exposes no
  general search (only a partner-scoped ``/search/partner/pins`` beta that
  requires an approved developer app + OAuth); every Python "Pinterest search"
  library on PyPI is abandoned (pinpy: last release 2021) or scrapes HTML
  behind login/CAPTCHA walls — which the task rules forbid and which breaks on
  a whim anyway. Most importantly, a pin is a *pointer* to somebody else's
  artwork: downloading and redistributing the original file gives the bot no
  licence to do so. So Pinterest cannot be a *reliable* source for this game.
* **Wikimedia Commons** (primary) — official public MediaWiki API, no key, no
  app registration; every file carries machine-readable licensing metadata
  (``extmetadata``), and most content is public domain / CC-licensed, which is
  exactly what a bot that re-hosts images needs.
* **Openverse** (secondary) — openly-licensed search API (CC / PDM), also key
  free for low volume; the bot restricts it to modification-friendly licences
  (no ``nd`` and, by default, no ``nc`` — a game screenshot is a derivative).
* **Pixabay** (optional) — free key, permissive Content License; only enabled
  when ``PIXABAY_API_KEY`` is configured, so the system never hard-depends on
  a registration.

Every provider returns :class:`Candidate` records with as much attribution
information as the source publishes; the service stores all of it.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .fetch import FetchError, Fetcher
from .validate import ALLOWED_MIMES

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def _strip_html(value: str) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", value))).strip()[:600]


def _filetype_to_mime(value: Any) -> str:
    """Normalize Openverse's bare ``filetype`` ("png") to a mime ("image/png")."""
    ft = str(value or "").strip().lower()
    if not ft:
        return ""
    return ft if ft.startswith("image/") else f"image/{ft}"


@dataclass
class Candidate:
    """One search hit, ready to be downloaded and validated."""

    source_url: str
    page_url: str = ""
    provider: str = ""
    title: str = ""
    width: int = 0
    height: int = 0
    mime_hint: str = ""
    size_hint: int = 0
    license: str = ""
    author: str = ""
    attribution: str = ""
    query: str = ""

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


class ImageProvider:
    name = "abstract"
    #: minimum delay enforced by the Fetcher between hits to this provider
    min_interval = 0.8

    def __init__(self, fetcher: Fetcher, *, min_width: int = 480):
        self.fetcher = fetcher
        self.min_width = min_width

    async def search(self, query: str, limit: int = 6) -> list[Candidate]:
        raise NotImplementedError

    def _usable(self, c: Candidate) -> bool:
        if c.mime_hint and c.mime_hint not in ALLOWED_MIMES:
            return False
        if self.min_width and 0 < c.width < self.min_width:
            return False
        return bool(c.source_url.startswith("https://"))

    def candidates_from(self, raw: list[Candidate], limit: int) -> list[Candidate]:
        usable = [c for c in raw if self._usable(c)]
        # roomier artwork first: Telegram photos deserve decent resolution
        usable.sort(key=lambda c: -(c.width or 0))
        return usable[:limit]


class WikimediaCommonsProvider(ImageProvider):
    """MediaWiki API search over File: namespace, with ``extmetadata`` licensing."""

    name = "wikimedia-commons"
    API_URL = "https://commons.wikimedia.org/w/api.php"
    min_interval = 1.1

    async def search(self, query: str, limit: int = 6) -> list[Candidate]:
        params = {
            "action": "query", "format": "json",
            "generator": "search",
            # filetype:bitmap keeps SVG/HTML junk out of the result page
            "gsrsearch": f"{query} filetype:bitmap",
            "gsrnamespace": "6",
            "gsrlimit": str(min(20, max(4, limit * 3))),
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiurlwidth": "1600",
            "maxlag": "5",
        }
        data = await self.fetcher.get_json(self.API_URL, params)
        pages = (data.get("query") or {}).get("pages") or {}
        out: list[Candidate] = []
        for page in pages.values():
            infos = page.get("imageinfo") or []
            if not infos:
                continue
            ii = infos[0]
            meta = ii.get("extmetadata") or {}
            license_name = _strip_html((meta.get("LicenseShortName") or {}).get("value", ""))
            artist = _strip_html((meta.get("Artist") or {}).get("value", ""))
            credit = _strip_html((meta.get("Credit") or {}).get("value", ""))
            usage = _strip_html((meta.get("UsageTerms") or {}).get("value", ""))
            attr_bits = [b for b in (artist or credit, license_name or usage) if b]
            c = Candidate(
                source_url=ii.get("url") or "",
                page_url=ii.get("descriptionurl") or "",
                provider=self.name,
                title=_strip_html(page.get("title", "")),
                width=int(ii.get("width") or 0),
                height=int(ii.get("height") or 0),
                mime_hint=(ii.get("mime") or "").lower(),
                size_hint=int(ii.get("size") or 0),
                license=license_name or usage,
                author=artist,
                attribution=" · ".join(attr_bits),
            )
            out.append(c)
        return self.candidates_from(out, limit)


class OpenverseProvider(ImageProvider):
    """Openverse (WordPress.org) — openly licensed images across many sources."""

    name = "openverse"
    API_URL = "https://api.openverse.org/v1/images/"
    min_interval = 0.7

    def __init__(self, fetcher: Fetcher, *, min_width: int = 480,
                 licenses: str = "cc0,pdm,by,by-sa"):
        super().__init__(fetcher, min_width=min_width)
        self.licenses = [x.strip() for x in (licenses or "").split(",") if x.strip()]

    async def search(self, query: str, limit: int = 6) -> list[Candidate]:
        params = {"q": query, "page_size": str(min(20, max(5, limit * 3))),
                  "mature": "false"}
        if self.licenses:
            params["licenses"] = ",".join(self.licenses)
        data = await self.fetcher.get_json(self.API_URL, params)
        out: list[Candidate] = []
        for r in data.get("results") or []:
            lic = r.get("license") or ""
            ver = r.get("license_version") or ""
            c = Candidate(
                source_url=r.get("url") or "",
                page_url=r.get("foreign_landing_url") or "",
                provider=self.name,
                title=_strip_html(r.get("title") or ""),
                width=int(r.get("width") or 0),
                height=int(r.get("height") or 0),
                # Openverse reports a bare extension ("png"); the gate wants a mime
                mime_hint=_filetype_to_mime(r.get("filetype")),
                size_hint=int(r.get("filesize") or 0),
                license=f"CC {lic} {ver}".strip(),
                author=_strip_html(r.get("creator") or ""),
                attribution=_strip_html(r.get("attribution") or ""),
            )
            out.append(c)
        return self.candidates_from(out, limit)


class PixabayProvider(ImageProvider):
    """Optional Pixabay search — active only when an API key is configured."""

    name = "pixabay"
    API_URL = "https://pixabay.com/api/"
    min_interval = 0.9
    LICENSE = "Pixabay Content License"

    def __init__(self, fetcher: Fetcher, *, min_width: int = 480, api_key: str = ""):
        super().__init__(fetcher, min_width=min_width)
        self.api_key = (api_key or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, limit: int = 6) -> list[Candidate]:
        if not self.enabled:
            raise FetchError("disabled", self.API_URL, "no PIXABAY_API_KEY configured",
                             retryable=False)
        params = {"key": self.api_key, "q": query, "image_type": "photo",
                  "safesearch": "true", "per_page": str(min(100, max(5, limit * 3))),
                  "min_width": str(self.min_width or 480)}
        data = await self.fetcher.get_json(self.API_URL, params)
        out: list[Candidate] = []
        for r in data.get("hits") or []:
            url = r.get("fullHDURL") or r.get("largeImageURL") or r.get("imageURL") or ""
            out.append(Candidate(
                source_url=url,
                page_url=r.get("pageURL") or "",
                provider=self.name,
                title=_strip_html(r.get("tags") or ""),
                width=int(r.get("image_width") or 0),
                height=int(r.get("image_height") or 0),
                size_hint=int(r.get("image_size") or 0),
                license=self.LICENSE,
                attribution=f"Pixabay · {self.LICENSE}",
            ))
        return self.candidates_from(out, limit)


#: name → factory(fetcher, settings). Adding a provider is one entry here.
PROVIDER_REGISTRY: dict[str, type[ImageProvider]] = {
    "commons": WikimediaCommonsProvider,
    "wikimedia": WikimediaCommonsProvider,
    "openverse": OpenverseProvider,
    "pixabay": PixabayProvider,
}


def build_providers(names: list[str], fetcher: Fetcher, settings) -> list[ImageProvider]:
    """Instantiate configured providers in order; unknown names are logged+skipped."""
    built: list[ImageProvider] = []
    for raw in names:
        name = (raw or "").strip().lower()
        cls = PROVIDER_REGISTRY.get(name)
        if cls is None:
            logger.warning("artwork: unknown provider %r ignored (known: %s)",
                           name, ",".join(sorted(PROVIDER_REGISTRY)))
            continue
        kwargs: dict = {"min_width": settings.artwork_min_width}
        if cls is OpenverseProvider:
            kwargs["licenses"] = settings.openverse_licenses
        if cls is PixabayProvider:
            kwargs["api_key"] = settings.pixabay_api_key
        try:
            built.append(cls(fetcher, **kwargs))
        except TypeError:                # provider does not accept an option
            built.append(cls(fetcher))
    return built
