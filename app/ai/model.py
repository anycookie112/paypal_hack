"""The LLM behind every agent: any OpenAI-compatible endpoint, configured in .env."""

from langchain_openai import ChatOpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS


class AgentError(RuntimeError):
    """The AI couldn't produce an answer (not configured, unreachable, timed out, or kept giving bad output).

    `completed` is what its tools had already done before it failed ([{"tool", "result"}]): an action may have
    happened even though there's no answer, e.g. the request was recorded and then the model timed out."""

    def __init__(self, message: str, *, completed: list[dict] | None = None):
        super().__init__(message)
        self.completed = completed or []


def available() -> bool:
    return bool(LLM_API_KEY)


def default_model(name: str | None = None) -> ChatOpenAI:
    """The configured model, or another one (`name`) on the same provider."""
    if not LLM_API_KEY:
        raise AgentError("AI isn't configured: set LLM_API_KEY on the server.")
    # One retry, not the client's default two: a request that times out has usually gone wrong, and
    # three attempts at the timeout is a very long wait for the member.
    # max_tokens caps one reply: a live test (2026-10-11) had Atria emit ~1,700 identical tool calls in one
    # never-ending reply, which the per-run call limits can't stop (they're checked between replies).
    return ChatOpenAI(model=name or LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=1,
                      max_tokens=2000, timeout=LLM_TIMEOUT_SECONDS, stream_chunk_timeout=LLM_TIMEOUT_SECONDS,
                      stream_usage=True)
