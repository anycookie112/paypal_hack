"""The LLM behind every agent: any OpenAI-compatible endpoint, configured in .env."""

from langchain_openai import ChatOpenAI

from app.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TIMEOUT_SECONDS


class AgentError(RuntimeError):
    """The AI couldn't produce an answer (not configured, unreachable, timed out, or kept giving bad output)."""


def available() -> bool:
    return bool(LLM_API_KEY)


def default_model() -> ChatOpenAI:
    if not LLM_API_KEY:
        raise AgentError("AI isn't configured: set LLM_API_KEY on the server.")
    # One retry, not the client's default two: a request that times out has usually gone wrong, and
    # three attempts at the timeout is a very long wait for the customer.
    return ChatOpenAI(model=LLM_MODEL, base_url=LLM_BASE_URL, api_key=LLM_API_KEY, max_retries=1,
                      timeout=LLM_TIMEOUT_SECONDS, stream_chunk_timeout=LLM_TIMEOUT_SECONDS, stream_usage=True)
