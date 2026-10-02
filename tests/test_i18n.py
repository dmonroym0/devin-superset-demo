import json
import re
from pathlib import Path

import pytest

from app.i18n import flatten, negotiate

ROOT = Path(__file__).parents[1]
TEMPLATES = ROOT / "app/templates"


def _catalog(locale: str) -> dict[str, str]:
    return flatten(json.loads((ROOT / f"app/i18n/{locale}.json").read_text()))


def test_catalogs_have_matching_keys_and_placeholders():
    en = _catalog("en")
    es = _catalog("es")

    assert en.keys() == es.keys()
    for key in en.keys() & es.keys():
        en_placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", en[key]))
        es_placeholders = set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", es[key]))
        assert en_placeholders == es_placeholders, f"{key}: {en_placeholders} != {es_placeholders}"


@pytest.mark.parametrize("locale", ["en", "es"])
def test_catalog_values_are_not_empty(locale):
    empty = [key for key, value in _catalog(locale).items() if not value.strip()]
    assert not empty, f"{locale}.json has empty values: {empty}"


def test_templates_have_no_hard_coded_english():
    jinja_blocks = re.compile(r"\{#.*?#\}|\{%.*?%\}|\{\{.*?\}\}", re.DOTALL)
    script_or_style = re.compile(r"<(script|style)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
    visible_attributes = re.compile(
        r"\b(title|alt|aria-label|aria-valuetext|placeholder|aria-description)\s*=\s*([\"'])(.*?)\2",
        re.IGNORECASE | re.DOTALL,
    )
    meta_description = re.compile(
        r"<meta\b(?=[^>]*\bname\s*=\s*([\"'])description\1)[^>]*>",
        re.IGNORECASE | re.DOTALL,
    )
    content_attribute = re.compile(r"\bcontent\s*=\s*([\"'])(.*?)\1", re.IGNORECASE | re.DOTALL)
    letters = re.compile(r"[A-Za-zÀ-ÿ]")

    for path in sorted(TEMPLATES.glob("*.html")):
        source = path.read_text()
        cleaned = script_or_style.sub("", jinja_blocks.sub("", source))
        candidates = [("text", text) for text in re.findall(r">([^<]+)<", cleaned)]
        for match in visible_attributes.finditer(cleaned):
            candidates.append((match.group(1), match.group(3)))
        for tag in meta_description.finditer(cleaned):
            match = content_attribute.search(tag.group(0))
            if match:
                candidates.append(("meta description", match.group(2)))
        offending = [
            f"{kind}: {snippet.strip()!r}" for kind, snippet in candidates if letters.search(snippet.strip())
        ]
        assert not offending, f"{path.relative_to(ROOT)} contains hard-coded English: {offending}"


def test_static_translation_keys_exist_in_english_catalog():
    en = _catalog("en")
    call = re.compile(r"\bt\(\s*(['\"])([^'\"]+)\1")
    files = [*TEMPLATES.glob("*.html"), ROOT / "app/dashboard.py"]
    missing = []
    for path in files:
        source = path.read_text()
        for match in call.finditer(source):
            if source[match.end() :].lstrip().startswith("~"):
                continue
            key = match.group(2)
            if key not in en:
                missing.append(f"{path.relative_to(ROOT)}: {key}")
    assert not missing, f"Missing English catalog keys: {missing}"


@pytest.mark.parametrize(
    ("cookie", "accept_language", "expected"),
    [
        ("es", "en", "es"),
        (None, "es-MX,es;q=0.9", "es"),
        (None, "fr, es;q=0.5", "es"),
        (None, "en;q=0.1, es;q=0", "en"),
        (None, "garbage", "en"),
        (None, "", "en"),
        ("de", "es-MX", "es"),
    ],
)
def test_locale_negotiation(cookie, accept_language, expected):
    assert negotiate(cookie, accept_language) == expected
