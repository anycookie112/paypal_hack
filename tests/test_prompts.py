"""Versioned prompts."""

import pytest

from app.ai import prompts

prompts.define("test_greeter", 1, "Greet the user.", changes="First version.")
prompts.define("test_greeter", 2, "Greet the user. Never guess facts.", changes="v1 guessed.")


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

