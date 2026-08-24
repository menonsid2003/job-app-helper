from app.config import settings
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMProvider
from app.llm.ollama_provider import OllamaProvider
from app.scoring_usage import scoring_usage


def make_default_provider() -> LLMProvider:
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            max_tokens=settings.anthropic_max_tokens,
            max_retries=settings.anthropic_max_retries,
            on_usage=scoring_usage.record_call,
        )
    if settings.llm_provider != "ollama":
        raise ValueError(f"Unknown LLM_PROVIDER {settings.llm_provider!r} — expected 'ollama' or 'anthropic'")
    return OllamaProvider(
        host=settings.ollama_host,
        model=settings.ollama_model,
        timeout_seconds=settings.ollama_timeout_seconds,
        max_retries=settings.ollama_max_retries,
    )
