"""Grimhaven artwork subsystem — discovery, download, validation, cache,
and Telegram delivery of game-event illustrations.

Public surface (see docs/ARTWORK.md for the full guide):

* :class:`ArtworkService` — cache-first orchestration;
* :func:`begin_artwork` / :func:`deliver_artwork` — the handler-facing
  Telegram delivery layer (background task, never raises);
* providers: Wikimedia Commons and Openverse by default, Pixabay when a key
  is configured (Pinterest is intentionally absent — see providers module
  docstring for the investigation record);
* :mod:`grimhaven.images.validate` — file-signature/dimension gate that every
  payload passes before it is cached or sent.
"""
from .fetch import FetchError, Fetcher
from .providers import (Candidate, OpenverseProvider, PixabayProvider,
                        WikimediaCommonsProvider, build_providers)
from .service import ArtworkService
from .delivery import (begin_artwork, claim_once_key, deliver_artwork,
                       join_artwork, send_photo_row)
from .validate import ImageRejected, validate_image_bytes, validate_image_file

__all__ = [
    "ArtworkService",
    "Candidate",
    "FetchError",
    "Fetcher",
    "ImageRejected",
    "OpenverseProvider",
    "PixabayProvider",
    "WikimediaCommonsProvider",
    "begin_artwork",
    "build_providers",
    "claim_once_key",
    "deliver_artwork",
    "join_artwork",
    "send_photo_row",
    "validate_image_bytes",
    "validate_image_file",
]
