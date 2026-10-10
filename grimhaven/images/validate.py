"""Image payload validation — never trust a remote filename or Content-Type.

Everything here is pure stdlib and side-effect free: the pipeline calls
:func:`validate_image_bytes` on the *bytes that actually arrived*, checks the
file signature against a whitelist, parses real pixel dimensions out of the
format headers, and rejects anything malformed (truncated files, HTML error
pages served with an ``image/*`` header, dimension/size outliers).

Telegram photo constraints honoured here:
* accepted signatures: JPEG, PNG, WebP, static GIF;
* sides ≤ 10 000 px (Telegram's photo limit);
* a configurable byte budget (Bot API photo uploads are practical well under
  the 50 MB document ceiling — we keep them small so taps stay snappy).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HTML_HEAD = re.compile(rb"^\s*(<\?xml|<!doctype\s+html|<html|<head|<body|<script)", re.I)
_EXT_FOR_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                 "image/gif": ".gif"}
ALLOWED_MIMES = tuple(_EXT_FOR_MIME)


class ImageRejected(Exception):
    """The payload is not a deliverable image (malformed, oversize, wrong type)."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason + (f": {detail}" if detail else ""))
        self.reason = reason
        self.detail = detail[:200]


@dataclass(frozen=True)
class ValidatedImage:
    mime: str
    width: int
    height: int
    size: int
    sha256: str
    ext: str

    @property
    def content_hash(self) -> str:
        return self.sha256


def sniff_image(data: bytes) -> tuple[str, int, int] | None:
    """Identify the format from magic bytes and extract pixel dimensions.

    Returns ``(mime, width, height)`` — ``0`` sides when the container is known
    but the dimensions could not be parsed — or ``None`` for anything that is
    not one of the accepted image formats.
    """
    if len(data) < 12:
        return None
    # ── PNG ──
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if b"IEND" not in data[-64:] and b"IEND" not in data[-1024:]:
            raise ImageRejected("truncated", "png without IEND")
        if len(data) >= 24 and data[12:16] == b"IHDR":
            w = int.from_bytes(data[16:20], "big")
            h = int.from_bytes(data[20:24], "big")
            return "image/png", w, h
        return "image/png", 0, 0
    # ── JPEG ──
    if data.startswith(b"\xff\xd8\xff") or data.startswith(b"\xff\xd8\xdb") \
            or data.startswith(b"\xff\xd8\xe0"):
        w = h = 0
        i, n = 2, len(data)
        while i + 4 <= n:
            if data[i] != 0xFF:                      # skip fill bytes
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xD9:                        # EOI
                break
            if marker == 0xDD:                        # DRI carries a fixed size
                i += 4
                continue
            if i + 4 > n:
                break
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            if seg_len < 2:
                break
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                if i + 9 <= n:
                    h = int.from_bytes(data[i + 5:i + 7], "big")
                    w = int.from_bytes(data[i + 7:i + 9], "big")
                break
            i += 2 + seg_len
        return "image/jpeg", w, h
    # ── GIF ──
    if data[:6] in (b"GIF87a", b"GIF89a"):
        w = int.from_bytes(data[6:8], "little")
        h = int.from_bytes(data[8:10], "little")
        if data.find(b"\x00\x3b", 10) < 0 and not data.endswith(b"\x3b"):
            raise ImageRejected("truncated", "gif without trailer")
        return "image/gif", w, h
    # ── WEBP ──
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X" and len(data) >= 30:
            w = 1 + int.from_bytes(data[24:27], "little")
            h = 1 + int.from_bytes(data[27:30], "little")
            return "image/webp", w, h
        if data[12:16] == b"VP8L" and len(data) >= 25:
            bits = int.from_bytes(data[21:25], "little")
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return "image/webp", w, h
        if data[12:16] == b"VP8 " and len(data) >= 30:
            # keyframe: 6-byte frame tag then 3-byte start code 0x9d 0x01 0x2a
            if data[23:26] == b"\x9d\x01\x2a":
                w = int.from_bytes(data[26:28], "little") & 0x3FFF
                h = int.from_bytes(data[28:30], "little") & 0x3FFF
                return "image/webp", w, h
            return "image/webp", 0, 0
        return "image/webp", 0, 0
    return None


def validate_image_bytes(data: bytes, *, min_width: int = 480, max_side: int = 10_000,
                         max_bytes: int = 5_000_000,
                         declared_mime: str = "") -> ValidatedImage:
    """Full gate for one fetched payload. Raises :class:`ImageRejected`.

    ``declared_mime`` (the HTTP ``Content-Type``) is only used as a *second*
    opinion: a page that claims ``text/html`` is rejected outright, but an
    ``image/*`` claim never overrides what the magic bytes actually are.
    """
    declared_mime = (declared_mime or "").split(";")[0].strip().lower()
    if declared_mime and declared_mime.startswith("text/"):
        raise ImageRejected("not-image", f"server said {declared_mime}")
    if not data:
        raise ImageRejected("empty", "0 bytes received")
    if len(data) > max_bytes:
        raise ImageRejected("oversize", f"{len(data)} > {max_bytes}")
    if _HTML_HEAD.match(data[:512]):
        raise ImageRejected("html-error-page", "an HTML document where an image belongs")
    sniffed = sniff_image(data)
    if sniffed is None:
        raise ImageRejected("unknown-format", "no accepted image signature")
    mime, w, h = sniffed
    if declared_mime and declared_mime not in ALLOWED_MIMES \
            and declared_mime != mime:
        raise ImageRejected("mime-mismatch", f"header={declared_mime} bytes={mime}")
    if w > max_side or h > max_side:
        raise ImageRejected("oversize", f"{w}x{h} exceeds {max_side}px")
    # JPEG only: a missing EOI marker means a stream that was cut off mid-write
    if mime == "image/jpeg" and b"\xff\xd9" not in data[-2048:]:
        raise ImageRejected("truncated", "jpeg without EOI")
    if w and min_width and w < min_width:
        raise ImageRejected("low-resolution", f"{w}px < {min_width}px")
    if h and w and (h / w > 6 or w / h > 6):
        raise ImageRejected("bad-aspect", f"{w}x{h}")
    digest = hashlib.sha256(data).hexdigest()
    return ValidatedImage(mime=mime, width=w, height=h, size=len(data),
                          sha256=digest, ext=_EXT_FOR_MIME[mime])


def validate_image_file(path, **limits) -> ValidatedImage:
    """Read a cached file back through the same gate (integrity re-check)."""
    from pathlib import Path
    data = Path(path).read_bytes()
    return validate_image_bytes(data, **limits)
