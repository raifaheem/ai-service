import json

from ..config import settings
from .i18n import normalize_locale
from .openai_call_guard import openai_call_guard
from .openai_client import client


def build_article_analysis_prompt(title: str, text: str, language: str) -> str:
    loc = normalize_locale(language)

    if loc == "en":
        output_language = "English"
    elif loc == "kk":
        output_language = "Kazakh"
    else:
        output_language = "Russian"

    return f"""
You are analyzing a medical article or medical text.

Produce a careful, restrained, evidence-oriented analysis.
Do not invent facts that are not present in the text.
If information is insufficient, state that directly.
Do not present conclusions as final medical fact.
If the article has weaknesses, clearly state them.

Return strictly JSON with this structure:
{{
  "summary": "string",
  "key_findings": ["string", "string"],
  "limitations": ["string", "string"],
  "practical_meaning": ["string", "string"],
  "red_flags": ["string", "string"],
  "confidence": "low|medium|high"
}}

Write all field values in {output_language}.

TITLE:
{title}

TEXT:
{text}
""".strip()


async def analyze_article_text(
    title: str,
    text: str,
    language: str = "ru",
) -> dict:
    prompt = build_article_analysis_prompt(
        title=title,
        text=text,
        language=language,
    )

    async with openai_call_guard():
        resp = await client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.2,
            max_tokens=1200,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": "You are a careful assistant for analyzing medical materials.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

    content = (resp.choices[0].message.content or "").strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {
            "summary": content or "Analysis failed: invalid response from LLM",
            "key_findings": [],
            "limitations": [],
            "practical_meaning": [],
            "red_flags": [],
            "confidence": "low",
        }

    return {
        "summary": data.get("summary", ""),
        "key_findings": data.get("key_findings", []) or [],
        "limitations": data.get("limitations", []) or [],
        "practical_meaning": data.get("practical_meaning", []) or [],
        "red_flags": data.get("red_flags", []) or [],
        "confidence": data.get("confidence", "medium"),
    }
