"""Verify the RAG knowledge base has enough coverage to serve chat queries.

Runs three checks:
  1. Corpus has chunks at all (`/metrics` → `qdrant_collection_size > 0`)
  2. Per-language coverage (`/v1/rag/stats` → ≥min-sources per locale)
  3. Test-query relevance (`/v1/rag/search` → ≥threshold of queries hit above
     `RAG_SCORE_THRESHOLD`)

Exits 0 when all checks pass, 1 otherwise. Intended to be run after seeding,
and also useful in CI pre-prod to catch accidental corpus wipes.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._common import (
    ServiceClient,
    configure_logging,
    load_service_token,
    logger,
)

DEFAULT_BASE_URL = "http://localhost:8001"
DEFAULT_MIN_SOURCES_PER_LANG = 10
DEFAULT_RELEVANCE_RATIO = 0.8
DEFAULT_SCORE_THRESHOLD = 0.35
DEFAULT_REQUIRED_LANGUAGES = ("ru", "en", "kk")

# Built-in probe queries — 3 per language, spread across the mandatory topic areas
# (symptoms, nutrition/lifestyle, mental health) from the Phase 12 spec.
DEFAULT_QUERIES: list[dict] = [
    {"language": "ru", "query": "У меня болит голова, что делать?"},
    {"language": "ru", "query": "Как улучшить сон при бессоннице?"},
    {"language": "ru", "query": "Как справиться с тревогой и стрессом?"},
    {"language": "en", "query": "How do I manage chronic back pain?"},
    {"language": "en", "query": "What is a healthy hydration level per day?"},
    {"language": "en", "query": "Techniques for reducing anxiety"},
    {"language": "kk", "query": "Бас ауруы кезінде не істеу керек?"},
    {"language": "kk", "query": "Ұйқысыздық кезінде қалай көмектесу керек?"},
    {"language": "kk", "query": "Стресстен қалай арылуға болады?"},
]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def all_ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def print(self) -> None:
        print()
        print("Knowledge base verification")
        print("=" * 40)
        for c in self.checks:
            mark = "OK" if c.ok else "FAIL"
            print(f"  [{mark}] {c.name}: {c.detail}")
        print("=" * 40)
        print("RESULT:", "all checks passed" if self.all_ok else "one or more checks FAILED")


def check_total_chunks(client: ServiceClient) -> Check:
    try:
        metrics = client.metrics()
    except httpx.HTTPError as exc:
        return Check("total chunks", False, f"could not reach /metrics: {exc}")

    total = metrics.get("qdrant_collection_size")
    if not isinstance(total, int) or total <= 0:
        return Check("total chunks", False, f"qdrant_collection_size={total}; corpus looks empty")
    return Check("total chunks", True, f"{total} chunks in corpus")


def check_language_coverage(
    client: ServiceClient,
    *,
    required_languages: tuple[str, ...],
    min_sources_per_lang: int,
) -> Check:
    try:
        stats = client.rag_stats()
    except httpx.HTTPError as exc:
        return Check(
            "language coverage",
            False,
            f"could not reach /v1/rag/stats: {exc} " "(is ENABLE_DEV_ROUTES=true?)",
        )

    sources_by_lang: dict = stats.get("sources_by_language") or {}
    missing = []
    per_lang = []
    for lang in required_languages:
        n = int(sources_by_lang.get(lang, 0))
        per_lang.append(f"{lang}={n}")
        if n < min_sources_per_lang:
            missing.append(f"{lang} has {n} < {min_sources_per_lang}")

    detail = ", ".join(per_lang)
    if missing:
        return Check("language coverage", False, f"{detail}; missing: {'; '.join(missing)}")
    return Check("language coverage", True, detail)


def check_query_relevance(
    client: ServiceClient,
    *,
    queries: list[dict],
    score_threshold: float,
    pass_ratio: float,
) -> Check:
    hits = 0
    per_query: list[str] = []
    for probe in queries:
        q = probe["query"]
        lang = probe.get("language")
        try:
            result = client.rag_search(query=q, language=lang, limit=3)
        except httpx.HTTPError as exc:
            per_query.append(f"  {lang} «{q}» → error: {exc}")
            continue

        results = result.get("results") or []
        top = max((float(r.get("score", 0.0)) for r in results), default=0.0)
        mark = "ok" if top >= score_threshold else "low"
        per_query.append(f"  {lang} «{q}» → top_score={top:.2f} ({mark})")
        if top >= score_threshold:
            hits += 1

    for line in per_query:
        logger.info(line)

    total = len(queries)
    if total == 0:
        return Check("query relevance", False, "no probe queries configured")

    ratio = hits / total
    detail = f"{hits}/{total} queries above score {score_threshold} (ratio {ratio:.2f})"
    if ratio + 1e-9 < pass_ratio:
        return Check("query relevance", False, detail)
    return Check("query relevance", True, detail)


def load_queries(path: Path | None) -> list[dict]:
    if path is None:
        return DEFAULT_QUERIES
    if not path.exists():
        raise SystemExit(f"queries file not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("queries file must contain a JSON list of {query, language} objects")
    return raw


def run(
    *,
    base_url: str,
    min_sources_per_lang: int,
    required_languages: tuple[str, ...],
    score_threshold: float,
    pass_ratio: float,
    queries: list[dict],
) -> Report:
    report = Report()
    service_token = load_service_token()

    with ServiceClient(base_url, service_token) as client:
        report.add(check_total_chunks(client))
        report.add(
            check_language_coverage(
                client,
                required_languages=required_languages,
                min_sources_per_lang=min_sources_per_lang,
            )
        )
        report.add(
            check_query_relevance(
                client,
                queries=queries,
                score_threshold=score_threshold,
                pass_ratio=pass_ratio,
            )
        )

    return report


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Verify the health-ai-service RAG corpus.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--min-sources-per-lang", type=int, default=DEFAULT_MIN_SOURCES_PER_LANG)
    p.add_argument(
        "--required-languages",
        default=",".join(DEFAULT_REQUIRED_LANGUAGES),
        help="Comma-separated list of locales that must meet the minimum",
    )
    p.add_argument("--score-threshold", type=float, default=DEFAULT_SCORE_THRESHOLD)
    p.add_argument("--pass-ratio", type=float, default=DEFAULT_RELEVANCE_RATIO)
    p.add_argument(
        "--queries-file",
        type=Path,
        help="JSON file with custom probe queries: [{'query': str, 'language': str}, ...]",
    )
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(verbose=args.verbose)
    queries = load_queries(args.queries_file)
    required = tuple(lang.strip() for lang in args.required_languages.split(",") if lang.strip())

    report = run(
        base_url=args.base_url,
        min_sources_per_lang=args.min_sources_per_lang,
        required_languages=required,
        score_threshold=args.score_threshold,
        pass_ratio=args.pass_ratio,
        queries=queries,
    )
    report.print()
    return 0 if report.all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
