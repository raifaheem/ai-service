"""Pre-LLM block for off-policy topics (sex / profanity / drugs / violence).

Distinct from app/services/safety.py (prompt-injection / markup) because the
refusal language, audit event, and word-list maintenance cadence differ.

Self-harm / suicidal ideation are deliberately NOT included here — they flow
through the mental_health intent addon, which directs to the crisis line.

Hygiene rule: the matched substring is NEVER logged or written to audit. Only
category + pattern_id + locale_hit. Otherwise the audit stream becomes a
slur corpus.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


SENSITIVE_REFUSAL = {
    "ru": (
        "Эта тема вне моей компетенции. Я — медицинский ассистент и могу помочь "
        "с вопросами о здоровье, питании, физической активности, сне и общем "
        "самочувствии. Если у вас есть вопрос о здоровье — задайте его, "
        "пожалуйста."
    ),
    "en": (
        "This topic is outside my scope. I'm a medical assistant and can help "
        "with questions about health, nutrition, physical activity, sleep, and "
        "general well-being. If you have a health question, please ask it."
    ),
    "kk": (
        "Бұл тақырып менің құзыретімнен тыс. Мен — медициналық көмекшімін, "
        "денсаулық, тамақтану, дене белсенділігі, ұйқы және жалпы әл-ауқат "
        "туралы сұрақтарға көмектесе аламын. Денсаулыққа қатысты сұрағыңыз "
        "болса, оны қойыңыз."
    ),
}


@dataclass(frozen=True)
class SensitiveMatch:
    """Result of a positive content-policy match.

    `category`: which policy category fired (sexual / profanity / drugs / violence).
    `locale_hit`: which seed list matched (ru/en/kk), or "obfuscated" when the
                  match required confusable normalization or flex obfuscation
                  handling.
    `pattern_id`: short stable label for forensics. NEVER contains matched text.
    """

    category: str
    locale_hit: str
    pattern_id: str


# Boundary class: any letter (Latin or Cyrillic, including the extended
# Cyrillic supplement which covers Kazakh-specific letters Қ, Ң, Ү, Ұ, Ғ, Ә, Ө, І).
# Python's \b is Unicode-aware in py3 but we use an explicit class to be
# audit-trail-friendly and to match exactly the alphabet we care about.
_WORD_CHAR = r"[A-Za-zЀ-ԯ]"
_LB = rf"(?<!{_WORD_CHAR})"
_RB = rf"(?!{_WORD_CHAR})"


# --- Seeds (regex fragments, NOT plain strings) ---
# Each fragment is wrapped with _LB / _RB at compile time. Multi-word seeds
# use \s+ for whitespace tolerance. Character classes like [аи] are inline.

# Trailing \w* on most stems lets _RB land outside morphological suffixes
# ("кокаина", "эректильной", "расчленить" all extend the stem with letters).
# _LB still blocks substring false positives ("Essex" / "asexual" / "intercostal"
# stay safe because the preceding char is a letter — _LB fails at the seed start).

_SEXUAL_RU = [
    r"секс\w*",
    r"половой\s+акт",
    r"половые\s+органы",
    r"эрек[цт]\w*",
    r"эякул\w*",
    r"оргазм\w*",
    r"мастурб\w*",
    r"порно\w*",
    r"иппп",
    r"венерич\w*",
    r"гоноре[яийю]\w*",
    r"сифилис\w*",
    r"хламидио\w*",
    r"генитал\w*",
    r"вагин\w*",
    r"пенис\w*",
    r"сперм\w*",
    r"клитор\w*",
    r"либидо",
    # Compound forms — stem captures the shared "...секс" so we don't have to
    # enumerate every declension (гомосексуальный, бисексуалка, …).
    r"гомосекс\w*",
    r"бисекс\w*",
    r"транссекс\w*",
    r"пансекс\w*",
    r"гетеросекс\w*",
]
_SEXUAL_EN = [
    r"sex",
    r"sexual\w*",
    r"intercourse",
    r"erection\w*",
    r"erectile",
    r"ejaculat\w*",
    r"orgasm\w*",
    r"masturbat\w*",
    r"porn\w*",
    r"std",
    r"sti",
    r"venereal",
    r"gonorrhea\w*",
    r"syphilis\w*",
    r"chlamydia\w*",
    r"genital\w*",
    r"vagin\w*",
    r"penis\w*",
    r"sperm\w*",
    r"clitor\w*",
    r"libido",
    # Compound forms (homosexual, homosexuality, bisexual, …).
    r"homosexual\w*",
    r"bisexual\w*",
    r"transsexual\w*",
    r"pansexual\w*",
    r"heterosexual\w*",
]
_SEXUAL_KK = [
    r"жыныстық\w*",
    r"жыныс\s+мүше\w*",
    r"эрекция\w*",
    r"эякуляц\w*",
    r"оргазм\w*",
    r"мастурбац\w*",
    r"порно\w*",
    r"жжби",
]

_PROFANITY_RU = [
    r"бляд\w*",
    r"еба[нт]\w*",
    r"хуй\w*",
    r"пизд\w*",
    r"мудак\w*",
    r"гондон\w*",
    r"сук[аи]\w*",
]
_PROFANITY_EN = [
    r"fuck\w*",
    r"shit\w*",
    r"bitch\w*",
    r"cunt\w*",
    r"asshole\w*",
    r"motherfuck\w*",
]
_PROFANITY_KK = [
    r"боқ\w*",
    r"көт\w*",
    r"сиктір\w*",
]

_DRUGS_RU = [
    r"кокаин\w*",
    r"героин\w*",
    r"марихуан\w*",
    r"конопл\w*",
    r"гашиш\w*",
    r"амфетамин\w*",
    r"метамфетамин\w*",
    r"экстази",
    r"мдма",
    r"лсд",
    r"закладк[аи]\w*",
    r"спайс\w*",
]
_DRUGS_EN = [
    r"cocaine\w*",
    r"heroin\w*",
    r"marijuana\w*",
    r"cannabis\w*",
    r"hashish\w*",
    r"amphetamine\w*",
    r"methamphetamine\w*",
    r"ecstasy",
    r"mdma",
    r"lsd",
    r"crystal\s+meth\w*",
    r"bath\s+salts",
    r"crack\s+cocaine",
]
_DRUGS_KK = [
    r"кокаин\w*",
    r"героин\w*",
    r"марихуана\w*",
    r"гашиш\w*",
    r"амфетамин\w*",
]

_VIOLENCE_RU = [
    r"расчлен\w*",
    r"пытк\w*",
    r"изнасил\w*",
    r"обезглав\w*",
    r"перерезать\s+горло",
    r"убить\s+человек\w*",
    r"истечь\s+кровью",
]
_VIOLENCE_EN = [
    r"dismember\w*",
    r"torture\w*",
    r"rape\w*",
    r"behead\w*",
    r"disembowel\w*",
    r"slit\s+(?:his|her|their|your)\s+throat\w*",
    r"gore",
    r"bloodbath",
]
_VIOLENCE_KK = [
    r"азаптау\w*",
    r"зорлау\w*",
    r"бас\s+кесу\w*",
]


# --- Confusables (Latin → Cyrillic for visually-identical letters) ---
# Used to defeat cross-script obfuscation like "сeкс" (Cyrillic с-Latin e-к-с).
# Only includes pairs that look truly identical in standard sans-serif rendering.

_LATIN_TO_CYRILLIC = str.maketrans(
    {
        "a": "а",
        "A": "А",
        "e": "е",
        "E": "Е",
        "o": "о",
        "O": "О",
        "c": "с",
        "C": "С",
        "x": "х",
        "X": "Х",
        "p": "р",
        "P": "Р",
        "y": "у",
        "Y": "У",
        "k": "к",
        "K": "К",
        "H": "Н",
        "B": "В",
        "M": "М",
        "T": "Т",
    }
)


def _normalize_latin_to_cyrillic(text: str) -> str:
    """Replace Latin lookalikes with Cyrillic equivalents.

    Cheap defense against cross-script obfuscation in ru/kk content. Used only
    against the ru/kk pattern set — running it on Latin words would corrupt
    them (e.g. "cocaine" → "сoсаine" with Cyrillic letters).
    """
    return text.translate(_LATIN_TO_CYRILLIC)


# --- Flex obfuscation handler for a small core list ---
# Defeats "с*кс", "с-е-к-с", "с е к с" etc. Each character of the seed is
# replaced with a class accepting either the literal letter (with confusables)
# or a non-letter symbol; positions are joined with up to 2 whitespace chars.
# Applied ONLY to core seeds — exploding the regex over every word would
# detonate false-positive rate.

_OBFUSCATION_SYMBOL_CLASS = r"\*\.\-_"

_CORE_OBFUSCATION = {
    # category -> list of seeds to flex
    "sexual": ["секс", "sex", "порно", "porn"],
    "drugs": ["cocaine", "кокаин", "heroin", "героин"],
    "profanity": ["fuck", "бляд"],
}

# Per-character confusable group used by _flex (Cyrillic ↔ Latin lookalikes).
_FLEX_CONFUSABLES = {
    "а": "аaА",
    "a": "аaА",
    "е": "еeЕ",
    "e": "еeЕ",
    "о": "оoО",
    "o": "оoО",
    "с": "сcС",
    "c": "сcС",
    "s": "сcСsS",  # Latin 's' added — visually close
    "х": "хxХ",
    "x": "хxХ",
    "р": "рpР",
    "p": "рpР",
    "у": "уyУ",
    "y": "уyУ",
    "к": "кkК",
    "k": "кkК",
    "н": "нnН",
    "n": "нnН",
    "м": "мmМ",
    "m": "мmМ",
    "т": "тtТ",
    "t": "тtТ",
    "и": "иiИ",
    "i": "иiИ",
}


def _flex(seed: str) -> str:
    """Build a regex fragment tolerant of symbol/whitespace insertion and
    Cyrillic↔Latin substitution between every character of `seed`.
    """
    parts: list[str] = []
    for ch in seed:
        confusables = _FLEX_CONFUSABLES.get(ch.lower())
        if confusables:
            # The position can be a confusable letter OR a non-letter symbol
            # (this is what catches "с*кс").
            letter_class = "".join(dict.fromkeys(confusables))
            parts.append(f"(?:[{letter_class}]|[{_OBFUSCATION_SYMBOL_CLASS}])")
        else:
            parts.append(f"(?:{re.escape(ch)}|[{_OBFUSCATION_SYMBOL_CLASS}])")
    return r"\s{0,2}".join(parts)


# --- Compilation ---


def _compile_seeds(category: str, locale_hit: str, seeds: list[str]) -> list[tuple[str, str, str, re.Pattern[str]]]:
    out: list[tuple[str, str, str, re.Pattern[str]]] = []
    for i, seed in enumerate(seeds):
        pat = re.compile(_LB + seed + _RB, re.IGNORECASE)
        out.append((category, locale_hit, f"{category}_{locale_hit}_{i}", pat))
    return out


def _compile_flex() -> list[tuple[str, str, re.Pattern[str]]]:
    out: list[tuple[str, str, re.Pattern[str]]] = []
    for category, seeds in _CORE_OBFUSCATION.items():
        for i, seed in enumerate(seeds):
            pat = re.compile(_LB + _flex(seed) + _RB, re.IGNORECASE)
            out.append((category, f"obf_{category}_{i}_{seed}", pat))
    return out


# Patterns split by script:
# - English seeds: matched against the original message.
# - Cyrillic-locale seeds (ru/kk): matched against the Latin→Cyrillic-normalized
#   message (defeats "сeкс" with Latin e). Normalization would corrupt English
#   words, so it must only apply to the Cyrillic seed pass.
_PATTERNS_EN: list[tuple[str, str, str, re.Pattern[str]]] = (
    _compile_seeds("sexual", "en", _SEXUAL_EN)
    + _compile_seeds("profanity", "en", _PROFANITY_EN)
    + _compile_seeds("drugs", "en", _DRUGS_EN)
    + _compile_seeds("violence", "en", _VIOLENCE_EN)
)

_PATTERNS_CYR: list[tuple[str, str, str, re.Pattern[str]]] = (
    _compile_seeds("sexual", "ru", _SEXUAL_RU)
    + _compile_seeds("sexual", "kk", _SEXUAL_KK)
    + _compile_seeds("profanity", "ru", _PROFANITY_RU)
    + _compile_seeds("profanity", "kk", _PROFANITY_KK)
    + _compile_seeds("drugs", "ru", _DRUGS_RU)
    + _compile_seeds("drugs", "kk", _DRUGS_KK)
    + _compile_seeds("violence", "ru", _VIOLENCE_RU)
    + _compile_seeds("violence", "kk", _VIOLENCE_KK)
)

_PATTERNS_FLEX: list[tuple[str, str, re.Pattern[str]]] = _compile_flex()


def detect_sensitive_topic(message: str) -> SensitiveMatch | None:
    """Return the first matching policy category, or None.

    Three passes (cheapest first):
    1. English literal seeds against the original message.
    2. Cyrillic-locale literal seeds against the Latin→Cyrillic-normalized
       message (defeats cross-script obfuscation like "сeкс").
    3. Flex patterns over a small core list (defeats "с*кс", "с е к с").

    The function logs only the category + pattern_id + locale_hit — never the
    matched substring or the user message.
    """
    if not message:
        return None

    # Pass 1: English seeds against original.
    for category, locale_hit, pid, pat in _PATTERNS_EN:
        if pat.search(message):
            logger.info(
                "sensitive_topic.match",
                extra={"category": category, "locale_hit": locale_hit, "pattern_id": pid},
            )
            return SensitiveMatch(category=category, locale_hit=locale_hit, pattern_id=pid)

    # Pass 2: Cyrillic seeds against normalized text.
    normalized = _normalize_latin_to_cyrillic(message)
    cross_script = normalized != message
    for category, locale_hit, pid, pat in _PATTERNS_CYR:
        if pat.search(normalized):
            effective_locale_hit = "obfuscated" if cross_script else locale_hit
            logger.info(
                "sensitive_topic.match",
                extra={"category": category, "locale_hit": effective_locale_hit, "pattern_id": pid},
            )
            return SensitiveMatch(category=category, locale_hit=effective_locale_hit, pattern_id=pid)

    # Pass 3: flex over core list.
    # Reject all-symbol matches: every char of a flex seed can be a letter OR a
    # symbol in [*.-_], so a bare "...." matches the 4-char "porn" pattern.
    # Real obfuscation always retains at least one letter (s*e*x, с-е-к-с).
    for category, pid, pat in _PATTERNS_FLEX:
        m = pat.search(message)
        if m and any(c.isalpha() for c in m.group()):
            logger.info(
                "sensitive_topic.match",
                extra={"category": category, "locale_hit": "obfuscated", "pattern_id": pid},
            )
            return SensitiveMatch(category=category, locale_hit="obfuscated", pattern_id=pid)

    return None


def screen_response(answer: str) -> SensitiveMatch | None:
    """Post-LLM screening — re-run the same pre-LLM filter against the model's reply.

    Defense-in-depth for cases where the LLM mentions a banned word from
    parametric knowledge or a leaked KB chunk despite explicit prompt
    instructions (the "bed for sleep and sex" sleep-hygiene trope is the
    canonical example). Re-uses the same regex stack as the input gate so a
    word blocked on input stays blocked on output.

    The hygiene rule from `detect_sensitive_topic` still applies — only
    category + pattern_id are logged, never the matched text or the answer.
    """
    return detect_sensitive_topic(answer)
