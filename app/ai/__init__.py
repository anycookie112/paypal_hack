"""Everything AI, built on LangChain and LangGraph.

    model.py       the LLM connection (any OpenAI-compatible endpoint, from .env)
    base.py        BaseAgent: prompts, tools, normal and streamed output, handovers (a LangGraph graph per agent)
    prompts.py     every prompt, versioned
    tools.py       read-only tools over one shop's data (ShopDeps)
    agents.py      the customer-facing agents: front desk -> order builder
    onboarding.py  turning an owner's pasted menu or policies into drafts
"""

from app.ai.base import (AgentResult, BaseAgent, ModelRetry, RunContext, StreamEvent, Usage, agent_prompt,
                         agent_tool, agent_validator)
from app.ai.model import AgentError, available, default_model

__all__ = ["AgentError", "AgentResult", "BaseAgent", "ModelRetry", "RunContext", "StreamEvent", "Usage",
           "agent_prompt", "agent_tool", "agent_validator", "available", "default_model"]
