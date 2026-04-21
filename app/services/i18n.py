from ..prompts import ADDON_PROMPTS, DISCLAIMERS, SYSTEM_PROMPTS

SUPPORTED_LOCALES = {"ru", "en", "kk"}

# Region → emergency phone string. Kept locale-agnostic: the number itself is
# universal, and the surrounding ADDON_PROMPTS["emergency"] template provides
# locale-appropriate framing ("EMERGENCY SERVICES (911)" / "СКОРУЮ ПОМОЩЬ (112)").
EMERGENCY_PHONES: dict[str, str] = {
    # North America
    "US": "911",
    "CA": "911",
    # UK
    "GB": "999",
    # CIS — 112 is the unified line, 103 is legacy ambulance direct.
    "KZ": "112 / 103",
    "RU": "112 / 103",
    "BY": "112 / 103",
    "UA": "112 / 103",
    "UZ": "112 / 103",
    "KG": "112 / 103",
    # EU + aligned (112 is the pan-European emergency number).
    "AT": "112",
    "BE": "112",
    "CZ": "112",
    "DE": "112",
    "DK": "112",
    "ES": "112",
    "FI": "112",
    "FR": "112",
    "GR": "112",
    "HU": "112",
    "IE": "112",
    "IT": "112",
    "NL": "112",
    "NO": "112",
    "PL": "112",
    "PT": "112",
    "RO": "112",
    "SE": "112",
    "TR": "112",
    "IN": "112",
    # Others
    "AU": "000",
    "NZ": "111",
    "JP": "119",
    "CN": "120",
}

# Fallback when the client did not supply a region (or supplied one we don't
# know). Keeps ru/kk anchored to the primary CIS audience; en stays neutral to
# avoid the old 911-in-KZ bug when the client omits region.
_DEFAULT_EMERGENCY_BY_LOCALE: dict[str, str] = {
    "ru": "112 или 103",
    "en": "your local emergency number",
    "kk": "112 немесе 103",
}


def normalize_locale(locale: str | None) -> str:
    if not locale:
        return "ru"

    value = locale.replace("_", "-").split("-", 1)[0].lower()
    if value in SUPPORTED_LOCALES:
        return value
    return "ru"


def get_system_prompt(locale: str) -> str:
    loc = normalize_locale(locale)
    return SYSTEM_PROMPTS.get(loc, SYSTEM_PROMPTS["ru"])


def get_disclaimer(locale: str) -> str:
    loc = normalize_locale(locale)
    return DISCLAIMERS.get(loc, DISCLAIMERS["ru"])


def get_prompt_addon(addon_name: str, locale: str) -> str | None:
    loc = normalize_locale(locale)
    addon = ADDON_PROMPTS.get(addon_name)
    if addon is None:
        return None
    return addon.get(loc, addon.get("ru"))


def get_emergency_phone(region: str | None, locale: str) -> str:
    """Resolve the emergency phone string for a region, with a locale-aware default.

    Region is an ISO 3166-1 alpha-2 code (case-insensitive). When absent or
    unknown, falls back to a locale-specific string — for ru/kk that is still
    the CIS number (112/103); for en it is a region-neutral phrase so the
    service never hands an English caller in KZ "911".
    """
    if region:
        phone = EMERGENCY_PHONES.get(region.upper())
        if phone:
            return phone
    loc = normalize_locale(locale)
    return _DEFAULT_EMERGENCY_BY_LOCALE.get(loc, _DEFAULT_EMERGENCY_BY_LOCALE["ru"])


def get_rag_instruction(locale: str, rag_context: str) -> str:
    loc = normalize_locale(locale)

    if loc == "en":
        return (
            "\n\n"
            "Below is context retrieved from the knowledge base. Use it only if it is relevant.\n"
            "If the context is only partially relevant, rely only on the useful parts.\n"
            "If the context is not helpful, do not invent facts.\n"
            "Do not present a diagnosis as established fact.\n"
            "When appropriate, mention that your answer is based on retrieved materials.\n\n"
            "KNOWLEDGE BASE CONTEXT:\n"
            f"{rag_context}"
        )

    if loc == "kk":
        return (
            "\n\n"
            "Төменде білім базасынан алынған контекст берілген. Оны тек шынымен релевант болса қолдан.\n"
            "Егер контекст жартылай ғана пайдалы болса, тек пайдалы бөліктеріне сүйен.\n"
            "Егер контекст көмектеспесе, фактілерді ойдан қоспа.\n"
            "Диагнозды нақты қойылған факт ретінде айтпа.\n"
            "Қажет болса, жауабың табылған материалдарға сүйенетінін көрсет.\n\n"
            "БІЛІМ БАЗАСЫНЫҢ КОНТЕКСТІ:\n"
            f"{rag_context}"
        )

    return (
        "\n\n"
        "Ниже приведён контекст из базы знаний. Используй его только если он релевантен.\n"
        "Если контекст частично релевантен — опирайся только на полезные фрагменты.\n"
        "Если контекст не помогает, не выдумывай факты.\n"
        "Не утверждай диагноз как установленный факт.\n"
        "Когда уместно, укажи, что ответ основан на найденных материалах.\n\n"
        "КОНТЕКСТ БАЗЫ ЗНАНИЙ:\n"
        f"{rag_context}"
    )
