from ..prompts import ADDON_PROMPTS, DISCLAIMERS, SYSTEM_PROMPTS

SUPPORTED_LOCALES = {"ru", "en", "kk"}


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
