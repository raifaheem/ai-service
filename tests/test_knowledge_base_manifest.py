"""Structural tests for the knowledge-base manifest.

Catches the failure mode where someone adds an article file but forgets the
manifest entry (or vice versa), or where an article lands without full
attribution — both of which silently break RAG source citations.

Tests are pure filesystem + JSON reads; no app imports.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_KB_DIR = _ROOT / "data" / "knowledge_base"
_MANIFEST_PATH = _KB_DIR / "manifest.json"

_SUPPORTED_LANGUAGES = {"ru", "en", "kk"}
_REQUIRED_ARTICLE_FIELDS = {"source_id", "file", "title", "language", "topic", "attribution"}
_REQUIRED_ATTRIBUTION_FIELDS = {"source", "source_url", "license", "accessed"}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def articles(manifest) -> list[dict]:
    return manifest["articles"]


# --------------- structural checks ---------------


def test_manifest_has_top_level_articles(manifest):
    assert "articles" in manifest
    assert isinstance(manifest["articles"], list)
    assert len(manifest["articles"]) > 0


def test_every_article_has_required_fields(articles):
    missing: list[tuple[str, set[str]]] = []
    for entry in articles:
        source_id = entry.get("source_id", "<unknown>")
        absent = _REQUIRED_ARTICLE_FIELDS - set(entry.keys())
        if absent:
            missing.append((source_id, absent))
    assert not missing, f"Articles missing required fields: {missing}"


def test_every_article_has_full_attribution(articles):
    incomplete: list[tuple[str, set[str]]] = []
    for entry in articles:
        attribution = entry.get("attribution", {})
        absent = _REQUIRED_ATTRIBUTION_FIELDS - set(attribution.keys())
        if absent:
            incomplete.append((entry.get("source_id", "<unknown>"), absent))
    assert not incomplete, f"Articles with incomplete attribution: {incomplete}"


def test_source_ids_are_unique(articles):
    ids = [entry["source_id"] for entry in articles]
    duplicates = {sid for sid in ids if ids.count(sid) > 1}
    assert not duplicates, f"Duplicate source_id values: {duplicates}"


def test_languages_are_supported(articles):
    bad = [
        (entry["source_id"], entry["language"])
        for entry in articles
        if entry["language"] not in _SUPPORTED_LANGUAGES
    ]
    assert not bad, f"Articles with unsupported language: {bad}"


def test_source_id_matches_language_prefix(articles):
    mismatched = [
        entry["source_id"]
        for entry in articles
        if not entry["source_id"].startswith(f"{entry['language']}-")
    ]
    assert not mismatched, (
        f"source_id should start with '<language>-' prefix. Mismatched: {mismatched}"
    )


# --------------- filesystem cross-check ---------------


def test_every_manifest_file_exists(articles):
    missing = [entry["source_id"] for entry in articles if not (_KB_DIR / entry["file"]).is_file()]
    assert not missing, f"Manifest entries with missing files: {missing}"


def test_no_orphaned_article_files(articles):
    """Every .md under articles/<lang>/ must be referenced by manifest.

    Orphaned files are often half-finished drafts that sneak into the RAG
    corpus only via a direct seed but never get cited because the manifest
    doesn't know them. Cheaper to fail this test than to debug a missing
    citation in production.
    """
    manifested_files = {entry["file"] for entry in articles}
    on_disk: set[str] = set()
    for lang_dir in (_KB_DIR / "articles").iterdir():
        if not lang_dir.is_dir():
            continue
        for md in lang_dir.glob("*.md"):
            relative = md.relative_to(_KB_DIR).as_posix()
            on_disk.add(relative)

    orphans = on_disk - manifested_files
    assert not orphans, (
        f"Article files on disk without manifest entry: {orphans}. "
        f"Add them to manifest.json or delete them."
    )


# --------------- article content sanity ---------------


def test_every_article_file_starts_with_h1(articles):
    no_h1 = []
    for entry in articles:
        text = (_KB_DIR / entry["file"]).read_text(encoding="utf-8")
        if not text.lstrip().startswith("# "):
            no_h1.append(entry["source_id"])
    assert not no_h1, f"Articles not starting with H1 heading: {no_h1}"


def test_every_article_has_attribution_footer(articles):
    """Each article must end with an italicized source line — that's the
    provenance surface the content filter and RAG both rely on."""
    no_footer = []
    for entry in articles:
        text = (_KB_DIR / entry["file"]).read_text(encoding="utf-8").strip()
        # Look at the last non-empty paragraph; expect an italic '*...*' block.
        last_paragraph = text.split("\n\n")[-1].strip()
        if not (last_paragraph.startswith("*") and last_paragraph.endswith("*")):
            no_footer.append(entry["source_id"])
    assert not no_footer, (
        f"Articles missing italic attribution footer: {no_footer}. "
        f"Expected trailing '*Adapted from ...*' paragraph."
    )


# --------------- pending_translations ---------------


def test_pending_translations_well_formed_if_present(manifest, articles):
    """D.2 introduced a `pending_translations` section tracking kk articles
    deferred for human review. If present, it must be internally consistent.
    """
    pending = manifest.get("pending_translations")
    if pending is None:
        pytest.skip("no pending_translations section — nothing to check")

    assert "items" in pending, "pending_translations must have an `items` list"
    items = pending["items"]
    assert isinstance(items, list) and items, "pending_translations.items must be a non-empty list"

    source_ids = {entry["source_id"] for entry in articles}
    issues: list[str] = []
    for item in items:
        missing = {"source_id", "translate_from", "topic"} - set(item.keys())
        if missing:
            issues.append(f"{item}: missing fields {missing}")
            continue
        # The source we translate from must already exist in the corpus.
        if item["translate_from"] not in source_ids:
            issues.append(
                f"{item['source_id']}: translate_from '{item['translate_from']}' "
                f"is not a known source_id"
            )
        # The pending source_id must NOT already exist as a real article.
        if item["source_id"] in source_ids:
            issues.append(
                f"{item['source_id']}: listed as pending but already exists in articles — "
                f"remove from pending_translations once the real entry lands"
            )
    assert not issues, f"pending_translations issues: {issues}"


# --------------- coverage guard ---------------


def test_language_coverage_meets_minimum(articles):
    """Keep a floor for each supported language so accidental deletions are caught.
    Current floor reflects the corpus after D.2 — tune upward as coverage grows.
    """
    per_language: dict[str, int] = {}
    for entry in articles:
        per_language[entry["language"]] = per_language.get(entry["language"], 0) + 1

    floors = {"ru": 16, "en": 16, "kk": 10}
    below = {
        lang: (count, floors[lang])
        for lang, count in per_language.items()
        if lang in floors and count < floors[lang]
    }
    assert not below, f"Language coverage below floor: {below}"
