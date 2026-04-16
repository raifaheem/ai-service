"""Tests for article_analyzer service."""
import json
from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

import pytest

from app.services.article_analyzer import build_article_analysis_prompt, analyze_article_text


class TestBuildArticleAnalysisPrompt:
    def test_en_locale(self):
        prompt = build_article_analysis_prompt("Test Title", "Some medical text", "en")
        assert "English" in prompt
        assert "Test Title" in prompt
        assert "Some medical text" in prompt

    def test_ru_locale(self):
        prompt = build_article_analysis_prompt("Title", "Text", "ru")
        assert "Russian" in prompt

    def test_kk_locale(self):
        prompt = build_article_analysis_prompt("Title", "Text", "kk")
        assert "Kazakh" in prompt

    def test_unknown_locale_defaults_ru(self):
        prompt = build_article_analysis_prompt("Title", "Text", "fr")
        assert "Russian" in prompt

    def test_contains_json_schema(self):
        prompt = build_article_analysis_prompt("Title", "Text", "en")
        assert "summary" in prompt
        assert "key_findings" in prompt
        assert "limitations" in prompt
        assert "confidence" in prompt


class TestAnalyzeArticleText:
    async def test_valid_json_response(self):
        response_data = {
            "summary": "Article summary",
            "key_findings": ["Finding 1"],
            "limitations": ["Limitation 1"],
            "practical_meaning": ["Meaning 1"],
            "red_flags": [],
            "confidence": "high",
        }

        @dataclass
        class MockMessage:
            content: str = json.dumps(response_data)

        @dataclass
        class MockChoice:
            message: MockMessage = None

            def __post_init__(self):
                if self.message is None:
                    self.message = MockMessage()

        @dataclass
        class MockCompletion:
            choices: list = None

            def __post_init__(self):
                if self.choices is None:
                    self.choices = [MockChoice()]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.article_analyzer.client", mock_client):
            result = await analyze_article_text("Test Article", "Medical text here", "en")

        assert result["summary"] == "Article summary"
        assert result["key_findings"] == ["Finding 1"]
        assert result["confidence"] == "high"

    async def test_invalid_json_response(self):
        @dataclass
        class MockMessage:
            content: str = "Not valid JSON at all"

        @dataclass
        class MockChoice:
            message: MockMessage = None

            def __post_init__(self):
                if self.message is None:
                    self.message = MockMessage()

        @dataclass
        class MockCompletion:
            choices: list = None

            def __post_init__(self):
                if self.choices is None:
                    self.choices = [MockChoice()]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.article_analyzer.client", mock_client):
            result = await analyze_article_text("Test", "Text", "en")

        assert "Not valid JSON" in result["summary"]
        assert result["confidence"] == "low"
        assert result["key_findings"] == []

    async def test_empty_response(self):
        @dataclass
        class MockMessage:
            content: str = None

        @dataclass
        class MockChoice:
            message: MockMessage = None

            def __post_init__(self):
                if self.message is None:
                    self.message = MockMessage()

        @dataclass
        class MockCompletion:
            choices: list = None

            def __post_init__(self):
                if self.choices is None:
                    self.choices = [MockChoice()]

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MockCompletion())

        with patch("app.services.article_analyzer.client", mock_client):
            result = await analyze_article_text("Test", "Text", "en")

        assert "Analysis failed" in result["summary"]
