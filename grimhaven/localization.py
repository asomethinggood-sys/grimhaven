"""Bilingual localization (doc §1.2): locale_fa.json + locale_en.json, every
button/message/error addressed by a unique key."""
from __future__ import annotations

import json
from pathlib import Path

from .config import settings

_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"


class Locale:
    def __init__(self, locales_dir: Path | None = None):
        self.dir = locales_dir or settings.locales_dir
        self._tables: dict[str, dict[str, str]] = {}
        for lang in ("fa", "en"):
            path = self.dir / f"locale_{lang}.json"
            self._tables[lang] = json.loads(path.read_text(encoding="utf-8"))

    def t(self, lang: str, key: str, **fmt) -> str:
        lang = lang if lang in self._tables else "fa"
        table = self._tables[lang]
        text = table.get(key)
        if text is None:  # fall back to the other language, then to the key
            text = self._tables["en" if lang == "fa" else "fa"].get(key, key)
        if fmt:
            fmt = {k: self.num(lang, v) if isinstance(v, (int, float)) else v
                   for k, v in fmt.items()}
            try:
                text = text.format(**fmt)
            except (KeyError, IndexError, ValueError):
                pass
        return text

    @staticmethod
    def num(lang: str, value) -> str:
        """Grouped number, with Persian digits for the FA locale."""
        if isinstance(value, str):
            try:
                value = float(value) if ("." in value or "٫" in value) else int(value)
            except ValueError:
                return value
        if isinstance(value, float):
            value = int(round(value)) if abs(value - round(value)) < 1e-9 else value
        if isinstance(value, float):
            s = f"{value:,.1f}"
        else:
            s = f"{int(value):,}"
        if lang == "fa":
            s = s.replace(",", "،")
            s = "".join(_FA_DIGITS[int(ch)] if ch.isdigit() else ch for ch in s)
        return s


locales = Locale()


def t(lang: str, key: str, **fmt) -> str:
    return locales.t(lang, key, **fmt)
