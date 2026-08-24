from app.llm.anthropic_provider import AnthropicProvider
from app.llm.factory import make_default_provider
from app.llm.ollama_provider import OllamaProvider


def test_defaults_to_ollama(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "ollama")
    provider = make_default_provider()
    assert isinstance(provider, OllamaProvider)


def test_selects_anthropic_when_configured(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "anthropic")
    monkeypatch.setattr("app.config.settings.anthropic_api_key", "sk-test-fake")
    provider = make_default_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-opus-5"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr("app.config.settings.llm_provider", "bogus")
    try:
        make_default_provider()
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "bogus" in str(exc)
