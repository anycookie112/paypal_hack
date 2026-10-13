"""Everything AI, built on LangChain and LangGraph.

    model.py       the LLM provider (any OpenAI-compatible endpoint, from .env)
    base.py        BaseAgent: prompts, model, tools, normal and streamed output; `as_tool` makes it a subagent
    prompts.py     every prompt, versioned

The group fund's agents (coordinator, lookup, actions; the receipt reader) are described in docs/SPEC.md §8.
"""

from app.ai.base import BaseAgent, StreamEvent
from app.ai.model import AgentError, available, default_model

__all__ = ["AgentError", "BaseAgent", "StreamEvent", "available", "default_model"]
