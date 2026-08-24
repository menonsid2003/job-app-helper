from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.llm.anthropic_provider import AnthropicProvider


def _text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        stop_details=None,
    )


def _make_provider(monkeypatch, create_fn) -> AnthropicProvider:
    provider = AnthropicProvider(api_key="sk-test-fake", model="claude-opus-5")
    monkeypatch.setattr(provider.client.messages, "create", create_fn)
    return provider


def test_complete_json_parses_clean_json(monkeypatch):
    provider = _make_provider(monkeypatch, lambda **kw: _text_response('{"score": 90, "ok": true}'))
    result = provider.complete_json("system", "user")
    assert result == {"score": 90, "ok": True}


def test_complete_json_extracts_json_from_surrounding_prose(monkeypatch):
    provider = _make_provider(
        monkeypatch, lambda **kw: _text_response('Sure, here you go:\n{"score": 42}\nHope that helps!')
    )
    result = provider.complete_json("system", "user")
    assert result == {"score": 42}


def test_complete_json_retries_once_on_unparseable_then_succeeds(monkeypatch):
    responses = iter([_text_response("not json at all"), _text_response('{"score": 1}')])
    provider = _make_provider(monkeypatch, lambda **kw: next(responses))
    result = provider.complete_json("system", "user")
    assert result == {"score": 1}


def test_complete_json_raises_after_repeated_unparseable_output(monkeypatch):
    provider = _make_provider(monkeypatch, lambda **kw: _text_response("still not json"))
    with pytest.raises(RuntimeError, match="not valid JSON"):
        provider.complete_json("system", "user")


def test_complete_json_raises_on_refusal(monkeypatch):
    provider = _make_provider(
        monkeypatch, lambda **kw: _text_response("", stop_reason="refusal")
    )
    with pytest.raises(RuntimeError, match="refusal"):
        provider.complete_json("system", "user")


def test_complete_json_wraps_rate_limit_error(monkeypatch):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(429, request=request)

    def raise_rate_limit(**kw):
        raise anthropic.RateLimitError(message="rate limited", response=response, body=None)

    provider = _make_provider(monkeypatch, raise_rate_limit)
    with pytest.raises(RuntimeError, match="rate limited"):
        provider.complete_json("system", "user")


def test_complete_json_passes_model_and_prompts(monkeypatch):
    captured = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _text_response('{"ok": true}')

    provider = _make_provider(monkeypatch, fake_create)
    provider.complete_json("be terse", "score this job")

    assert captured["model"] == "claude-opus-5"
    assert captured["system"] == "be terse"
    assert captured["messages"] == [{"role": "user", "content": "score this job"}]
