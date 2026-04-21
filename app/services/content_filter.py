"""Post-LLM content safety: flag dosage recommendations and append a footer.

Previous versions rewrote the response in place — inserting parenthetical warnings
mid-sentence and "softener" prefixes before diagnoses. Those produced grammatically
broken output ("take 500mg (consult your doctor for proper dosage) ibuprofen twice
daily" / "Возможно, речь идёт о у вас низкий уровень витамина D") and the softener
path actively papered over problems the system prompt is supposed to prevent anyway.

This module now does one thing: detect dosage mentions and append a single locale-
aware footer to the response. Diagnosis softening is gone — the system prompt is
the right place for that rule.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Patterns matching specific drug dosage recommendations (e.g. "take 500mg ibuprofen")
_DOSAGE_PATTERNS: list[re.Pattern] = [
    # "take/принимать/drink 500mg" or "500 mg twice daily"
    re.compile(
        r"(?:take|drink|принимать|принимайте|выпейте|ішіңіз|қабылдаңыз)\s+"
        r"\d+\s*(?:mg|мг|g|г|ml|мл|mcg|мкг|IU|МЕ)\b",
        re.IGNORECASE,
    ),
    # Dosage pattern: "250mg 3 times a day"
    re.compile(
        r"\d+\s*(?:mg|мг|g|г|ml|мл|mcg|мкг)\s+\d+\s*(?:times?|раз[а-я]*|рет)",
        re.IGNORECASE,
    ),
    # "dose of 10mg"
    re.compile(
        r"(?:dose|доз[а-я]*|дозас[а-я]*)\s+(?:of\s+)?\d+\s*(?:mg|мг|g|г|ml|мл)",
        re.IGNORECASE,
    ),
]

_DOSAGE_FOOTER = {
    "ru": "⚠️ Перед приёмом любых препаратов уточните дозировку у вашего врача или фармацевта.",
    "en": "⚠️ Before taking any medication, consult your doctor or pharmacist for proper dosage.",
    "kk": "⚠️ Кез келген дәрі қабылдар алдында дозаны дәрігер немесе фармацевтпен нақтылаңыз.",
}


def check_response_safety(response: str, locale: str = "ru") -> tuple[str, list[str]]:
    """Check LLM response for medical safety issues and append a footer if needed.

    Returns (filtered_response, list_of_applied_filters).

    The response body is never modified in place — the footer is appended exactly
    once when any dosage pattern matches anywhere in the text.
    """
    if not response:
        return response, []

    applied_filters: list[str] = []
    for pattern in _DOSAGE_PATTERNS:
        if pattern.search(response):
            applied_filters.append("dosage_warning")
            break

    filtered = response
    if "dosage_warning" in applied_filters:
        footer = _DOSAGE_FOOTER.get(locale, _DOSAGE_FOOTER["ru"])
        filtered = f"{response.rstrip()}\n\n{footer}"

    if applied_filters:
        logger.info("Content filters applied: %s", applied_filters)

    return filtered, applied_filters
