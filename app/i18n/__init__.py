"""UI string catalogs, locale negotiation and locale-aware formatting."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

LOCALES = ("en", "es")
DEFAULT_LOCALE = "en"
COOKIE_NAME = "forkfix_lang"
HTML_LANG = {"en": "en", "es": "es-419"}
INTL_LOCALE = {"en": "en-US", "es": "es-MX"}

_DIR = Path(__file__).parent


@cache
def catalog(locale: str) -> dict[str, Any]:
    return json.loads((_DIR / f"{locale}.json").read_text(encoding="utf-8"))


def flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in tree.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


@cache
def _flat(locale: str) -> dict[str, str]:
    return flatten(catalog(locale))


def negotiate(cookie: str | None, accept_language: str | None) -> str:
    if cookie in LOCALES:
        return cookie
    ranked: list[tuple[float, int, str]] = []
    for index, part in enumerate((accept_language or "").split(",")):
        pieces = part.strip().split(";")
        tag = pieces[0].strip().lower()
        if not tag:
            continue
        quality = 1.0
        for param in pieces[1:]:
            name, _, value = param.strip().partition("=")
            if name == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        ranked.append((-quality, index, tag.split("-")[0]))
    for negative_quality, _, language in sorted(ranked):
        if negative_quality < 0 and language in LOCALES:
            return language
    return DEFAULT_LOCALE


class Translator:
    def __init__(self, locale: str):
        self.locale = locale if locale in LOCALES else DEFAULT_LOCALE
        self.html_lang = HTML_LANG[self.locale]
        self.intl_locale = INTL_LOCALE[self.locale]
        self._strings = _flat(self.locale)
        self._fallback = _flat(DEFAULT_LOCALE)

    def __call__(self, key: str, **params: Any) -> str:
        template = self._strings.get(key) or self._fallback.get(key) or key
        return template.format(**params) if params else template

    def has(self, key: str) -> bool:
        return key in self._strings

    def plural(self, key: str, count: int, **params: Any) -> str:
        form = "one" if count == 1 else "other"
        return self(f"{key}.{form}", count=self.number(count), **params)

    def number(self, value: float | None, decimals: int = 0) -> str:
        if value is None:
            return "—"
        return f"{value:,.{decimals}f}"

    def acus(self, value: float | None) -> str:
        if value is None:
            return "—"
        return self.number(value, 0 if float(value).is_integer() else 1)

    def percent(self, value: float | None) -> str:
        return "—" if value is None else self("units.percent", value=round(value * 100))

    def duration(self, seconds: float | None) -> str:
        if seconds is None:
            return "—"
        total = max(0, int(seconds))
        if total < 60:
            return self("units.seconds", s=total)
        if total < 3600:
            minutes, rest = divmod(total, 60)
            return self("units.minutes_seconds", m=minutes, s=rest)
        hours, rest = divmod(total, 3600)
        return self("units.hours_minutes", h=hours, m=rest // 60)

    def datetime(self, value: str | float | None) -> str:
        if value is None:
            return "—"
        moment = (
            datetime.fromtimestamp(value, UTC)
            if isinstance(value, int | float)
            else datetime.fromisoformat(value).astimezone(UTC)
        )
        month = self._strings["months"].split(",")[moment.month - 1] if "months" in self._strings else ""
        return self(
            "formats.datetime",
            month=month,
            day=moment.day,
            year=moment.year,
            time=f"{moment:%H:%M}",
        )

    def client_strings(self) -> dict[str, str]:
        return {key[len("js.") :]: value for key, value in self._strings.items() if key.startswith("js.")}
