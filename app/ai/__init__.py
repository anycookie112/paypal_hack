"""Everything AI, built on Pydantic AI.

    model.py       the LLM connection (any OpenAI-compatible endpoint, from .env)
    base.py        BaseAgent: prompts, tools, normal and streamed output, handovers
    prompts.py     every prompt, versioned
    tools.py       read-only tools over one shop's data (ShopDeps)
    agents.py      the customer-facing agents: front desk -> order builder
    onboarding.py  turning an owner's pasted menu or policies into drafts
"""

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")  # pydantic-ai prints an ad banner on first run

from app.ai.base import AgentResult, BaseAgent, StreamEvent, agent_prompt, agent_tool, agent_validator  # noqa: E402
from app.ai.model import AgentError, available, default_model  # noqa: E402

__all__ = ["AgentError", "AgentResult", "BaseAgent", "StreamEvent", "agent_prompt", "agent_tool",
           "agent_validator", "available", "default_model"]
