from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """Swappable LLM backend. Implementations must return parsed JSON (a dict)
    for a system+user prompt pair, or raise on unrecoverable failure."""

    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict:
        ...
