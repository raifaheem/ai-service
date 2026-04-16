"""Extended i18n tests for full coverage."""
from app.services.i18n import (
    normalize_locale,
    get_system_prompt,
    get_disclaimer,
    get_prompt_addon,
    get_rag_instruction,
)


class TestGetRagInstruction:
    def test_en_rag_instruction(self):
        result = get_rag_instruction("en", "Some context")
        assert "KNOWLEDGE BASE" in result
        assert "Some context" in result

    def test_ru_rag_instruction(self):
        result = get_rag_instruction("ru", "Контекст")
        assert "КОНТЕКСТ БАЗЫ ЗНАНИЙ" in result
        assert "Контекст" in result

    def test_kk_rag_instruction(self):
        result = get_rag_instruction("kk", "Контекст")
        assert "БІЛІМ БАЗАСЫНЫҢ КОНТЕКСТІ" in result

    def test_unknown_locale_defaults_to_ru(self):
        result = get_rag_instruction("fr", "Context")
        assert "КОНТЕКСТ БАЗЫ ЗНАНИЙ" in result


class TestGetSystemPrompt:
    def test_en(self):
        prompt = get_system_prompt("en")
        assert len(prompt) > 100

    def test_ru(self):
        prompt = get_system_prompt("ru")
        assert len(prompt) > 100

    def test_kk(self):
        prompt = get_system_prompt("kk")
        assert len(prompt) > 100


class TestGetPromptAddon:
    def test_nonexistent_addon(self):
        result = get_prompt_addon("nonexistent_addon", "en")
        assert result is None

    def test_existing_addon(self):
        result = get_prompt_addon("symptom_check", "en")
        assert result is not None
        assert len(result) > 10
