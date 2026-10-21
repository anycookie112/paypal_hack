"""The LLM behind every agent, configured in .env:

    LLM_PROVIDER=anthropic  Claude through LangChain's Anthropic client (ANTHROPIC_API_KEY; LLM_MODEL e.g.
                            claude-haiku-5-5; LLM_EFFORT optional)
    LLM_PROVIDER=google     Gemini through LangChain's native Google client (LLM_API_KEY = a Gemini API key)
    LLM_PROVIDER=openai     any OpenAI-compatible endpoint at LLM_BASE_URL (Atria, OpenRouter, …); the default

Gemini 3 models need the native client: they attach a "thought signature" to each tool call that has to be sent back
with the next request, and the OpenAI-compatible endpoint loses it ("Function call is missing a thought_signature").
"""

from langchain_core.language_models import BaseChatModel

from app.config import (ANTHROPIC_API_KEY, LLM_API_KEY, LLM_BASE_URL, LLM_EFFORT, LLM_MODEL, LLM_PROVIDER,
                        LLM_TIMEOUT_SECONDS)

# Caps one reply: a live test (2026-10-11) had Atria emit ~1,700 identical tool calls in one never-ending reply,
# which the per-run call limits can't stop (they're checked between replies).
MAX_REPLY_TOKENS = 2000
# Claude thinks before it answers (adaptive thinking, on by default on current models), and thinking counts toward
# max_tokens: a cap sized for the answer alone could end a reply mid-thought.
MAX_REPLY_TOKENS_THINKING = 8000


class AgentError(RuntimeError):
    """The AI couldn't produce an answer (not configured, unreachable, timed out, or kept giving bad output).

    `completed` is what its tools had already done before it failed ([{"tool", "result"}]): an action may have
    happened even though there's no answer, e.g. the request was recorded and then the model timed out."""

    def __init__(self, message: str, *, completed: list[dict] | None = None):
        super().__init__(message)
        self.completed = completed or []


def _api_key() -> str:
    return (ANTHROPIC_API_KEY or LLM_API_KEY) if LLM_PROVIDER == "anthropic" else LLM_API_KEY


def available() -> bool:
    return bool(_api_key())


def default_model(name: str | None = None) -> BaseChatModel:
    """The configured model, or another one (`name`) on the same provider."""
    if not _api_key():
        raise AgentError("AI isn't configured: set LLM_API_KEY (or ANTHROPIC_API_KEY) on the server.")
    # One retry, not the client's default: a request that times out has usually gone wrong, and several attempts
    # at the timeout is a very long wait for the member.
    if LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=name or LLM_MODEL, api_key=_api_key(), max_retries=1,
                             default_request_timeout=LLM_TIMEOUT_SECONDS, max_tokens=MAX_REPLY_TOKENS_THINKING,
                             output_config={"effort": LLM_EFFORT} if LLM_EFFORT else None)
    if LLM_PROVIDER == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(model=name or LLM_MODEL, google_api_key=LLM_API_KEY, max_retries=1,
                                      timeout=LLM_TIMEOUT_SECONDS, max_output_tokens=MAX_REPLY_TOKENS)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=name or LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=1,
                      max_tokens=MAX_REPLY_TOKENS, timeout=LLM_TIMEOUT_SECONDS,
                      stream_chunk_timeout=LLM_TIMEOUT_SECONDS, stream_usage=True)
