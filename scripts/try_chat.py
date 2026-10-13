"""Try the coordinator and its subagents against the real model (the LLM in .env): a few chat turns, end to end.

    uv run python scripts/try_chat.py                 # the latest prompts
    uv run python scripts/try_chat.py --prompts 1     # pin every agent's prompt to v1, to compare
    uv run python scripts/try_chat.py --turns 2       # only the first two turns

Uses a throwaway database with the demo group (tests/demo.py) and some past purchases; nothing touches data/app.db
or PayPal. Every turn prints the reply, how long it took, and each step the agents took (from the agent log).
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = f"sqlite:///{(Path(tempfile.mkdtemp()) / 'chat.db').as_posix()}"
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, select  # noqa: E402

from app.db import engine, init_db  # noqa: E402
from app.models import AgentLog  # noqa: E402
from tests.demo import NOW, make_group, member, past_purchase  # noqa: E402

TURNS = [
    ("Priya", "We're out of gloves for Saturday's cleanup, I'll grab some"),
    ("Priya", "by 11"),
    ("Marco", "I'll pick up gloves on my way in"),
    ("Dana", "The coffee isn't in the cupboard"),
]
HISTORY = [("Marco", "Gloves", 18_40, 20), ("Priya", "Trash bags", 11_99, 16), ("Marco", "Coffee", 14_50, 5)]


def short(data_json: str, limit: int = 160) -> str:
    text = json.dumps(json.loads(data_json), ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "…"


async def main(turns: int) -> None:
    from app.ai.main import Coordinator, reply  # after PROMPT_VERSIONS is set

    init_db()
    with Session(engine) as session:
        make_group(session)
        for who, what, total, days_ago in HISTORY:
            past_purchase(session, who, what, total, days_ago)
        agent = Coordinator()
        print(f"Prompts: {agent.prompt_id}, {agent.lookup.prompt_id}, {agent.action.prompt_id}\n")
        for who, text in TURNS[:turns]:
            seen = session.exec(select(AgentLog.id).order_by(AgentLog.id.desc())).first() or 0
            started = time.monotonic()
            answer = await reply(session, member(session, who), text, agent=agent, now=NOW)
            took = time.monotonic() - started
            print(f"{who}: {text}")
            print(f"AI ({took:.0f} s){' FAILED' if answer.failed else ''}: {answer.text}")
            for step in session.exec(select(AgentLog).where(AgentLog.id > seen).order_by(AgentLog.id)):
                print(f"    {step.agent:<11} {step.event:<11} {step.name:<16} {short(step.data_json)}")
            print(flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompts", type=int, help="pin every agent's prompt to this version")
    parser.add_argument("--turns", type=int, default=len(TURNS))
    args = parser.parse_args()
    if args.prompts:
        os.environ["PROMPT_VERSIONS"] = ",".join(f"{n}={args.prompts}" for n in ("coordinator", "lookup", "action"))
    asyncio.run(main(args.turns))
