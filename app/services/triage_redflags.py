"""Deterministic red-flag keyword detection (S3).

The LLM in `normalize_answer` already sets `red_flag` for the obvious cases,
but a hostile (or just confused) user can sometimes manipulate it. This
module is the belt-and-suspenders pass: a literal keyword scan over the raw
user text. If any keyword matches, we set `red_flag=True` regardless of what
the LLM said.

Scope: applied only on steps that already have `red_flag_check=True` in
TRIAGE_FORM (primary_complaint, accompanying, explicit_red_flags). Keeping
the regex list short keeps false positives manageable — the LLM is still
the primary detector for nuanced cases.
"""

from __future__ import annotations

import re

from .i18n import normalize_locale

# Each entry is a *compiled* regex; case-insensitive, with word boundaries
# where appropriate. The regexes are intentionally narrow — false positives
# would short-circuit a triage session and emit an emergency_phone message,
# which is annoying but not dangerous; missed positives (false negatives)
# are the bigger risk and are why the LLM still runs.
_RED_FLAG_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "ru": [
        re.compile(r"\bбол[ьи][а-я]*\s+в\s+груди\b", re.IGNORECASE),
        re.compile(r"\bдавящ[а-я]+\s+бол[ьи][а-я]*\s+в\s+груди\b", re.IGNORECASE),
        re.compile(r"\bотда(?:ё|е)т\s+в\s+(?:руку|челюст|плеч)", re.IGNORECASE),
        re.compile(r"\bпотер[яиял][а-я]*\s+сознани", re.IGNORECASE),
        re.compile(r"\bобморок\b", re.IGNORECASE),
        re.compile(r"\bне\s+могу\s+дышать\b", re.IGNORECASE),
        re.compile(r"\bодышк\w+\s+в\s+поко", re.IGNORECASE),
        re.compile(r"\bсильн\w+\s+кровотечен", re.IGNORECASE),
        re.compile(r"\bкров[ьи][а-я]*\s+(?:изо\s+рта|ртом|со\s+стулом|с\s+мочой)", re.IGNORECASE),
        re.compile(r"\bсуицид", re.IGNORECASE),
        re.compile(r"\bхочу\s+(?:умереть|покончить|убить\s+себя)", re.IGNORECASE),
        re.compile(r"\bпарализова", re.IGNORECASE),
        re.compile(r"\bне\s+чувству[ю|е][а-я]*\s+(?:руку|ногу|лицо)", re.IGNORECASE),
        re.compile(r"\bтемпература\s+(?:39|40|41|42)", re.IGNORECASE),
    ],
    "en": [
        re.compile(r"\bchest\s+pain\b", re.IGNORECASE),
        re.compile(r"\bcrushing\s+chest\b", re.IGNORECASE),
        re.compile(r"\bradiat(?:es|ing)\s+(?:to|down)\s+(?:arm|jaw|shoulder)", re.IGNORECASE),
        re.compile(r"\b(?:lost|loss\s+of)\s+conscious", re.IGNORECASE),
        re.compile(r"\bfaint(?:ed|ing)\b", re.IGNORECASE),
        re.compile(r"\bcan'?t\s+breath", re.IGNORECASE),
        re.compile(r"\bshortness\s+of\s+breath\s+at\s+rest\b", re.IGNORECASE),
        re.compile(r"\bheavy\s+bleeding\b", re.IGNORECASE),
        re.compile(r"\bcoughing\s+up\s+blood\b", re.IGNORECASE),
        re.compile(r"\bblood\s+in\s+(?:stool|urine|vomit)\b", re.IGNORECASE),
        re.compile(r"\bsuicid", re.IGNORECASE),
        re.compile(r"\b(?:want|going)\s+to\s+(?:die|kill\s+myself|end\s+(?:my\s+)?life)", re.IGNORECASE),
        re.compile(r"\bparaly[sz]", re.IGNORECASE),
        re.compile(r"\bnumbness\s+in\s+(?:arm|leg|face)", re.IGNORECASE),
    ],
    "kk": [
        re.compile(r"\bкеуде[а-я]*\s+ауыр", re.IGNORECASE),
        re.compile(r"\bес[ка]?н[еа]н\s+тан", re.IGNORECASE),
        re.compile(r"\bтыныс\s+ал[аы]\s+алмай", re.IGNORECASE),
        re.compile(r"\bқан\s+кет", re.IGNORECASE),
        re.compile(r"\bсуицид", re.IGNORECASE),
        re.compile(r"\bөмір[а-я]*\s+қи[яу]", re.IGNORECASE),
    ],
}


def keyword_red_flag(text: str, locale: str) -> str | None:
    """Return the first matched pattern (as a hint string) or None.

    The hint format is `"keyword:<pattern>"` so audit logs can distinguish
    deterministic catches from LLM-flagged ones.
    """
    if not text:
        return None
    loc = normalize_locale(locale)
    patterns = _RED_FLAG_PATTERNS.get(loc, _RED_FLAG_PATTERNS["ru"])
    for pat in patterns:
        if pat.search(text):
            return f"keyword:{pat.pattern}"
    return None
