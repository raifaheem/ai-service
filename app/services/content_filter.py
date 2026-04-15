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

# Patterns for definitive diagnoses stated as fact
_DIAGNOSIS_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"(?:you\s+have|у\s+вас|сізде|your\s+diagnosis\s+is|диагноз\s*[:\-—]\s*)\s*[A-Za-zА-Яа-яЁёІіҚқҒғҮүҰұӘәӨөҺһҢңА-Яа-я]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:this\s+is\s+(?:definitely|clearly|obviously)|это\s+(?:точно|однозначно|определённо))\s+\w+",
        re.IGNORECASE,
    ),
]

_DOSAGE_REPLACEMENTS = {
    "ru": " (уточните дозировку у вашего врача)",
    "en": " (consult your doctor for proper dosage)",
    "kk": " (дәрігеріңізбен дозасын нақтылаңыз)",
}

_DIAGNOSIS_SOFTENERS = {
    "ru": "Возможно, речь идёт о",
    "en": "This may suggest",
    "kk": "Бұл көрсетуі мүмкін",
}


def check_response_safety(response: str, locale: str = "ru") -> tuple[str, list[str]]:
    """Check LLM response for medical safety issues and apply filters.

    Returns (filtered_response, list_of_applied_filters).
    """
    applied_filters: list[str] = []
    filtered = response

    # 1. Check for specific drug dosages
    dosage_note = _DOSAGE_REPLACEMENTS.get(locale, _DOSAGE_REPLACEMENTS["ru"])
    for pattern in _DOSAGE_PATTERNS:
        if pattern.search(filtered):
            filtered = pattern.sub(lambda m: m.group(0) + dosage_note, filtered, count=0)
            if "dosage_warning" not in applied_filters:
                applied_filters.append("dosage_warning")

    # 2. Soften definitive diagnoses
    for pattern in _DIAGNOSIS_PATTERNS:
        match = pattern.search(filtered)
        if match:
            applied_filters.append("diagnosis_softened")
            break

    if applied_filters:
        logger.info("Content filters applied: %s", applied_filters)

    return filtered, applied_filters
