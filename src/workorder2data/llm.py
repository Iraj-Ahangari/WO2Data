"""Minimal model-client interface. v1 ships an Anthropic adapter; others plug in behind the same Protocol."""

import os
from pathlib import Path
from typing import Protocol

GENERATOR_MODEL_DEFAULT = "claude-opus-5-5"
EXTRACTOR_MODEL_DEFAULT = "claude-sonnet-5-5"
EXTRACTOR_EFFORT_DEFAULT = "medium"


class LLMClient(Protocol):
    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 2000, cached_system: str | None = None
    ) -> str:
        """`cached_system` is a large static prefix (e.g. taxonomy lists) the provider may cache across calls."""
        ...


class LLMRefusal(RuntimeError):
    """The model declined to answer (stop_reason == refusal)."""


class AnthropicClient:
    def __init__(self, effort: str = "low"):
        import anthropic  # imported lazily so tests and offline use do not need credentials

        self._client = anthropic.Anthropic()
        self._effort = effort

    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 2000, cached_system: str | None = None
    ) -> str:
        blocks = []
        if cached_system:
            blocks.append({"type": "text", "text": cached_system, "cache_control": {"type": "ephemeral"}})
        blocks.append({"type": "text", "text": system})
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=blocks,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self._effort},
        )
        if response.stop_reason == "refusal":
            raise LLMRefusal(str(getattr(response, "stop_details", "")))
        return "".join(b.text for b in response.content if b.type == "text")


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from a git-ignored .env into os.environ (existing variables win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
