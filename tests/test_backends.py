import io
import json
import subprocess

import pytest

from workorder2data import llm
from workorder2data.cli import main
from workorder2data.llm import (
    ClaudeCliClient, LLMError, OllamaClient, backend_for, make_client, resolve_model,
)


def test_claude_cli_builds_a_toolless_isolated_call(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": 1}', stderr="")

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    out = ClaudeCliClient(effort="low").complete(system="SYS", user="USER", model="sonnet", cached_system="STATIC")
    assert out == '{"ok": 1}'
    cmd = seen["cmd"]
    assert cmd[:2] == ["claude", "-p"] and cmd[cmd.index("--model") + 1] == "sonnet"
    assert cmd[cmd.index("--tools") + 1] == ""                                  # no tools
    assert "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    system = cmd[cmd.index("--system-prompt") + 1]
    assert system.startswith("STATIC") and system.endswith("SYS")                # cached prefix first
    assert seen["input"] == "USER" and seen["cwd"] != "."


def test_claude_cli_failure_raises_llmerror(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not logged in"))
    with pytest.raises(LLMError, match="not logged in"):
        ClaudeCliClient().complete(system="s", user="u", model="m")
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(llm.subprocess, "run", boom)
    with pytest.raises(LLMError):
        ClaudeCliClient().complete(system="s", user="u", model="m")


class FakeResponse(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_ollama_sends_json_mode_and_big_context_and_strips_think(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["body"] = req.full_url, json.loads(req.data)
        return FakeResponse(json.dumps({"message": {"content": '<think>{"x": 1}</think>{"a": 2}'}}).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    out = OllamaClient().complete(system="SYS", user="USER", model="qwen3.5:latest", cached_system="STATIC", max_tokens=300)
    assert out == '{"a": 2}'
    assert seen["url"] == "http://localhost:11434/api/chat"
    b = seen["body"]
    assert b["format"] == "json" and b["stream"] is False and b["think"] is False
    assert b["options"]["num_ctx"] >= 16384 and b["options"]["temperature"] == 0 and b["options"]["num_predict"] >= 1500
    assert b["messages"][0]["content"].startswith("STATIC") and b["messages"][1]["content"] == "USER"


def test_ollama_connection_error_is_an_llmerror(monkeypatch):
    def refuse(req, timeout=None):
        raise llm.urllib.error.URLError("refused")
    monkeypatch.setattr(llm.urllib.request, "urlopen", refuse)
    with pytest.raises(LLMError, match="ollama"):
        OllamaClient().complete(system="s", user="u", model="m")


def test_backend_and_model_resolution(monkeypatch):
    for v in ("W2D_BACKEND", "W2D_GENERATOR_BACKEND", "W2D_EXTRACTOR_BACKEND", "W2D_GENERATOR_MODEL", "W2D_EXTRACTOR_MODEL"):
        monkeypatch.delenv(v, raising=False)
    assert backend_for("extractor") == "anthropic"
    monkeypatch.setenv("W2D_BACKEND", "claude-cli")
    assert backend_for("generator") == "claude-cli"
    monkeypatch.setenv("W2D_EXTRACTOR_BACKEND", "ollama")
    assert backend_for("extractor") == "ollama" and backend_for("generator") == "claude-cli"
    assert backend_for("extractor", "anthropic") == "anthropic"
    with pytest.raises(ValueError):
        backend_for("extractor", "nope")
    assert resolve_model("generator", "claude-cli") == "opus" and resolve_model("extractor", "claude-cli") == "sonnet"
    assert resolve_model("extractor", "ollama", "qwen3.5:latest") == "qwen3.5:latest"
    with pytest.raises(ValueError, match="--model"):
        resolve_model("extractor", "ollama")


def test_make_client_types():
    assert isinstance(make_client("claude-cli"), ClaudeCliClient)
    assert isinstance(make_client("ollama", base_url="http://x:1"), OllamaClient)
    assert make_client("ollama", base_url="http://x:1/").base_url == "http://x:1"


def test_cli_ollama_without_model_gives_a_clear_error(capsys, monkeypatch):
    for v in ("W2D_BACKEND", "W2D_EXTRACTOR_BACKEND", "W2D_EXTRACTOR_MODEL", "W2D_GENERATOR_BACKEND", "W2D_GENERATOR_MODEL"):
        monkeypatch.delenv(v, raising=False)
    assert main(["batch", "x.txt", "--backend", "ollama"]) == 2
    assert "pass --model" in capsys.readouterr().err
