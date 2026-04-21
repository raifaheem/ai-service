"""Run the golden-set evaluation suite against a running instance (C.1).

Usage:
    EVAL_BASE_URL=http://localhost:8001 \\
    EVAL_SERVICE_TOKEN=test-token \\
    python evals/run_evals.py [path/to/cases.json]

Behaviour:
- Fires each case as a POST /v1/chat with X-Service-Token auth.
- Checks intent_category, risk_level, and content assertions
  (answer_must_contain_any_of / answer_must_not_contain) against the response.
- Prints a per-case line and a summary; exit code = number of failures.

Rule for authors: don't tune the prompts to make specific cases pass. Fix the
underlying class of problem a failing case reveals.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


async def run_case(
    client: httpx.AsyncClient,
    case: dict[str, Any],
    service_token: str,
    user_id: str,
) -> dict[str, Any]:
    resp = await client.post(
        "/v1/chat",
        json={
            "message": case["message"],
            "locale": case.get("locale", "ru"),
        },
        headers={"X-Service-Token": service_token, "X-User-Id": user_id},
    )
    failures: list[str] = []

    if resp.status_code != 200:
        return {
            "id": case["id"],
            "passed": False,
            "failures": [f"non-200 response: {resp.status_code} {resp.text[:200]}"],
        }

    body = resp.json()
    answer = (body.get("answer") or "").lower()
    intent = body.get("intent") or {}
    expected = case.get("expected", {})

    if "intent_category" in expected:
        allowed = _as_list(expected["intent_category"])
        actual = intent.get("category")
        if actual not in allowed:
            failures.append(f"intent_category: expected {allowed}, got {actual!r}")

    if "risk_level" in expected:
        allowed = _as_list(expected["risk_level"])
        actual = intent.get("risk_level")
        if actual not in allowed:
            failures.append(f"risk_level: expected {allowed}, got {actual!r}")

    must_any = expected.get("answer_must_contain_any_of")
    if must_any:
        lowered = [p.lower() for p in must_any]
        if not any(p in answer for p in lowered):
            failures.append(f"answer missing any of {must_any}; got starting with: {answer[:120]!r}")

    must_not = expected.get("answer_must_not_contain")
    if must_not:
        for forbidden in must_not:
            if forbidden.lower() in answer:
                failures.append(f"answer contains forbidden phrase {forbidden!r}")

    return {"id": case["id"], "passed": not failures, "failures": failures}


def _load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("cases", []))


async def main(cases_path: str, base_url: str, service_token: str) -> int:
    path = Path(cases_path)
    if not path.exists():
        print(f"cases file not found: {path}", file=sys.stderr)
        return 2

    cases = _load_cases(path)
    if not cases:
        print("no cases to run", file=sys.stderr)
        return 2

    results: list[dict[str, Any]] = []
    user_id = "eval-runner"
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        for case in cases:
            result = await run_case(client, case, service_token, user_id)
            status = "PASS" if result["passed"] else "FAIL"
            print(f"{status:4s} {result['id']}")
            for f in result["failures"]:
                print(f"      - {f}")
            results.append(result)

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    pct = round(100.0 * passed / total, 1) if total else 0.0
    print(f"\n{passed}/{total} cases passed ({pct}%)")
    return total - passed


if __name__ == "__main__":
    default_cases = Path(__file__).resolve().parent / "cases.json"
    cases_arg = sys.argv[1] if len(sys.argv) > 1 else str(default_cases)
    exit_code = asyncio.run(
        main(
            cases_arg,
            os.environ.get("EVAL_BASE_URL", "http://localhost:8001"),
            os.environ.get("EVAL_SERVICE_TOKEN", "test-token"),
        )
    )
    sys.exit(exit_code)
