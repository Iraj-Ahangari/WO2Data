"""Minimal model-client interface. v1 ships an Anthropic adapter; others plug in behind the same Protocol."""

import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
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


class LLMError(RuntimeError):
    """The backend failed (process error, connection error, timeout)."""

BACKENDS = ("anthropic", "claude-cli", "ollama")
# (generator, extractor) model defaults per backend; None = the user must pass --model
MODEL_DEFAULTS = {
    "anthropic": (GENERATOR_MODEL_DEFAULT, EXTRACTOR_MODEL_DEFAULT),
    "claude-cli": ("opus", "sonnet"),
    "ollama": (None, None),
}


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


def _full_system(system: str, cached_system: str | None) -> str:
    return f"{cached_system}\n\n{system}" if cached_system else system


class ClaudeCliClient:
    """Uses the Claude Code CLI (`claude -p`) with your existing login: no API key needed. Slower per call
    (a process per request) and counted against your Claude plan. Runs in a temp dir so no project files load."""

    def __init__(self, effort: str = "low", timeout: int = 300, claude_bin: str = "claude"):
        self.effort, self.timeout, self.claude_bin = effort, timeout, claude_bin

    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 2000, cached_system: str | None = None
    ) -> str:
        cmd = [
            self.claude_bin, "-p", "--model", model, "--system-prompt", _full_system(system, cached_system),
            "--tools", "", "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
            "--effort", self.effort, "--output-format", "text",
        ]
        try:
            run = subprocess.run(cmd, input=user, capture_output=True, text=True, timeout=self.timeout, cwd=tempfile.gettempdir())
        except (OSError, subprocess.TimeoutExpired) as e:
            raise LLMError(f"claude CLI failed: {e}") from e
        if run.returncode != 0:
            raise LLMError(f"claude CLI exited {run.returncode}: {run.stderr.strip()[:500]}")
        return run.stdout


class OllamaClient:
    """Local models through Ollama's native chat API (free, offline). Uses JSON mode and a larger context window:
    Ollama's default context is too small for the taxonomy prompts and would silently truncate them."""

    def __init__(self, base_url: str = "http://localhost:11434", num_ctx: int = 16384, timeout: int = 900):
        self.base_url, self.num_ctx, self.timeout = base_url.rstrip("/"), num_ctx, timeout

    def complete(
        self, *, system: str, user: str, model: str, max_tokens: int = 2000, cached_system: str | None = None
    ) -> str:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": _full_system(system, cached_system)}, {"role": "user", "content": user}],
            "stream": False,
            "format": "json",
            "think": False,
            "options": {"temperature": 0, "num_ctx": self.num_ctx, "num_predict": max(max_tokens, 1500)},
        }
        req = urllib.request.Request(f"{self.base_url}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content = json.loads(resp.read())["message"]["content"]
        except (urllib.error.URLError, OSError, KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"ollama request failed: {e}") from e
        return re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)


def backend_for(role: str, explicit: str | None = None) -> str:
    """Backend for `role` ('generator' or 'extractor'): flag, then W2D_<ROLE>_BACKEND, then W2D_BACKEND, then anthropic."""
    name = explicit or os.environ.get(f"W2D_{role.upper()}_BACKEND") or os.environ.get("W2D_BACKEND") or "anthropic"
    if name not in BACKENDS:
        raise ValueError(f"unknown backend {name!r}; choose one of {', '.join(BACKENDS)}")
    return name


def resolve_model(role: str, backend: str, explicit: str | None = None) -> str:
    model = explicit or os.environ.get(f"W2D_{role.upper()}_MODEL") or MODEL_DEFAULTS[backend][0 if role == "generator" else 1]
    if not model:
        raise ValueError(f"backend {backend!r} has no default {role} model; pass --model (for ollama e.g. qwen3.5:latest)")
    return model


def make_client(backend: str = "anthropic", *, effort: str = "low", base_url: str | None = None) -> LLMClient:
    if backend == "anthropic":
        return AnthropicClient(effort=effort)
    if backend == "claude-cli":
        return ClaudeCliClient(effort=effort)
    if backend == "ollama":
        return OllamaClient(base_url=base_url or os.environ.get("W2D_BASE_URL", "http://localhost:11434"))
    raise ValueError(f"unknown backend {backend!r}")
