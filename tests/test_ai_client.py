# -*- coding: utf-8 -*-
"""
The OpenAI client is built lazily and its absence is reported precisely - see
get_client()/require_client() in src/functions/ai.py.

"Missing package" and "missing key" are fixed in different places, so each
must be named on its own; and because the client used to be an import-time
constant, a process that started without one could never recover. Neither
`openai` nor OPENAI_KEY is needed here: AsyncOpenAI and the key are swapped
for fakes.
"""

import asyncio

import pytest
from pydantic import SecretStr

from src.functions import ai


class _FakeAsyncOpenAI:
    instances = []

    def __init__(self, api_key):
        self.api_key = api_key
        type(self).instances.append(self)


@pytest.fixture
def fake_openai(monkeypatch):
    """`openai` installed, key configured, and no client built yet."""
    _FakeAsyncOpenAI.instances = []
    monkeypatch.setattr(ai, "AsyncOpenAI", _FakeAsyncOpenAI)
    monkeypatch.setattr(ai.settings, "openai_key", SecretStr("test-key"))
    monkeypatch.setattr(ai, "_client", None)
    return _FakeAsyncOpenAI


def test_reason_is_none_when_package_and_key_are_present(fake_openai):
    assert ai._client_unavailable_reason() is None


def test_reason_names_the_missing_package(fake_openai, monkeypatch):
    monkeypatch.setattr(ai, "AsyncOpenAI", None)

    reason = ai._client_unavailable_reason()

    assert "'openai' package is not installed" in reason
    assert "OPENAI_KEY" not in reason


@pytest.mark.parametrize("missing_key", [None, SecretStr("")], ids=["unset", "empty"])
def test_reason_names_the_missing_key(fake_openai, monkeypatch, missing_key):
    monkeypatch.setattr(ai.settings, "openai_key", missing_key)

    reason = ai._client_unavailable_reason()

    assert "OPENAI_KEY is not set" in reason
    assert "package" not in reason


def test_get_client_builds_once_with_the_configured_key(fake_openai):
    first = ai.get_client()
    second = ai.get_client()

    assert first is second
    assert len(fake_openai.instances) == 1
    assert first.api_key == "test-key"


def test_get_client_recovers_once_the_key_appears(fake_openai, monkeypatch):
    # The reason for building lazily: an unavailable client must not stick.
    monkeypatch.setattr(ai.settings, "openai_key", None)
    assert ai.get_client() is None
    assert fake_openai.instances == []

    monkeypatch.setattr(ai.settings, "openai_key", SecretStr("late-key"))

    assert ai.get_client().api_key == "late-key"


def test_require_client_returns_the_client_when_available(fake_openai):
    assert ai.require_client("anything") is ai.get_client()


def test_require_client_names_the_feature_and_the_reason(fake_openai, monkeypatch):
    monkeypatch.setattr(ai, "AsyncOpenAI", None)

    with pytest.raises(RuntimeError) as excinfo:
        ai.require_client("note analysis")

    assert "note analysis disabled" in str(excinfo.value)
    assert "'openai' package is not installed" in str(excinfo.value)


@pytest.mark.parametrize(
    "call,feature",
    [
        (lambda: ai.get_analysis(content="An entry."), "note analysis"),
        (lambda: ai.get_url_summary("https://example.com/page"), "URL summary"),
        (lambda: ai.get_url_title("https://example.com/page"), "URL title"),
    ],
    ids=["get_analysis", "get_url_summary", "get_url_title"],
)
def test_ai_entry_points_raise_a_clear_error_without_a_client(
    fake_openai, monkeypatch, call, feature
):
    # Not an AttributeError from calling into `None`.
    monkeypatch.setattr(ai.settings, "openai_key", None)

    with pytest.raises(RuntimeError, match=f"{feature}.*disabled.*OPENAI_KEY"):
        asyncio.run(call())
