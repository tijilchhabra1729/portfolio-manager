"""The provider-agnostic LLM layer: JSON extraction and provider selection."""

from __future__ import annotations

import pytest

from app.llm.base import AnalystError, extract_json_object
from app.llm.claude import ClaudeModel
from app.llm.openai_compat import OpenAICompatModel


# --- JSON extraction ----------------------------------------------------------------


def test_plain_object_parses():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_fenced_block_is_unwrapped():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_preamble_and_trailing_prose_stripped():
    text = 'Sure! Here you go:\n{"insights": []}\nHope that helps.'
    assert extract_json_object(text) == {"insights": []}


def test_bare_array_is_wrapped():
    assert extract_json_object("[1, 2, 3]") == {"items": [1, 2, 3]}


def test_no_json_raises():
    with pytest.raises(AnalystError):
        extract_json_object("I cannot help with that.")
    with pytest.raises(AnalystError):
        extract_json_object("")


# --- provider selection -------------------------------------------------------------


def _reset_settings():
    from app.config import settings
    settings.cache_clear()


def test_premium_with_claude_key_gets_claude(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    _reset_settings()
    from app.llm.select import select_model

    assert isinstance(select_model("premium"), ClaudeModel)
    _reset_settings()


def test_free_user_gets_groq_even_with_claude_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    monkeypatch.setenv("GEMINI_API_KEY", "")  # isolate the groq fallback (Gemini would win if set)
    _reset_settings()
    from app.llm.select import select_model

    model = select_model("free")
    assert isinstance(model, OpenAICompatModel) and model.name == "groq"
    _reset_settings()


def test_gemini_preferred_over_groq_for_free(monkeypatch):
    # With both free keys set, Gemini wins (its free token budget absorbs the fan-out).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "gm-x")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    _reset_settings()
    from app.llm.select import select_model

    assert select_model("free").name == "gemini"
    assert select_model("premium").name == "gemini"  # premium with no Claude key also lands here
    _reset_settings()


def test_premium_without_claude_key_falls_back_to_groq(monkeypatch):
    # Empty (not delenv): pydantic-settings still reads .env, so a dev key there would leak
    # in; an explicit empty env var overrides the file.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    monkeypatch.setenv("GEMINI_API_KEY", "")  # isolate the groq fallback
    _reset_settings()
    from app.llm.select import select_model

    assert isinstance(select_model("premium"), OpenAICompatModel)
    _reset_settings()


def test_no_keys_means_no_model(monkeypatch):
    # Empty (not delenv): pydantic-settings still reads .env, so a dev key there would leak
    # in; an explicit empty env var overrides the file.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    _reset_settings()
    from app.llm.select import any_llm_configured, select_model

    assert select_model("premium") is None
    assert select_model("free") is None
    assert any_llm_configured() is False
    _reset_settings()
