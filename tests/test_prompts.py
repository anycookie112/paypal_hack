"""Versioned prompts, and agents recording which version they ran with (fake chat model, no network)."""

import asyncio

import pytest

from app.ai import BaseAgent, prompts
from tests.fakes import FakeChatModel

prompts.define("test_greeter", 1, "Greet the user.", changes="First version.")
prompts.define("test_greeter", 2, "Greet the user. Never guess facts.", changes="v1 guessed.")


class Greeter(BaseAgent[None, str]):
    name = "greeter"
    prompt = "test_greeter"


def test_latest_prompt_by_default_and_pinning(monkeypatch):
    assert prompts.get("test_greeter").version == 2
    assert prompts.get("test_greeter", 1).id == "test_greeter@v1"
    monkeypatch.setenv("PROMPT_VERSIONS", "other=3, test_greeter=1")
    assert prompts.get("test_greeter").version == 1
    with pytest.raises(KeyError, match="no v99"):
        prompts.get("test_greeter", 99)
    with pytest.raises(KeyError, match="No prompt named"):
        prompts.get("missing")


def test_versions_are_append_only():
    with pytest.raises(ValueError, match="already defined"):
        prompts.define("test_greeter", 1, "changed", changes="")
    with pytest.raises(ValueError, match="next version"):
        prompts.define("test_greeter", 9, "skipped", changes="")
    assert all(p.changes for p in prompts.history("test_greeter"))


def test_agent_uses_and_records_its_prompt_version():
    seen = {}

    def respond(messages, tools):
        seen["instructions"] = messages[0].content
        return "hello"

    result = asyncio.run(Greeter(model=FakeChatModel(respond=respond), prompt_version=1).run("hi"))
    assert result.prompt_id == "test_greeter@v1"
    assert seen["instructions"] == "Greet the user."
