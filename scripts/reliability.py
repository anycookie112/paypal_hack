"""How reliable is the AI? The demo story (docs/SPEC.md §11) run N times against the real model, every turn checked by
code: did the right thing happen in the records, and does the reply say what it must (and nothing it mustn't)?

    LLM_PROVIDER=anthropic LLM_MODEL=claude-haiku-5-5 uv run python scripts/reliability.py --runs 10
    ... --prompts coordinator=6          # pin prompt versions (as PROMPT_VERSIONS), to compare with the latest
    ... --parallel 3                     # runs at the same time (each has its own database)

Each run gets a fresh throwaway database with the demo group; nothing touches data/app.db or PayPal. Prints a pass
rate per turn, then every failure with its reply. Everything (replies, checks, times, tokens) is saved to
data/reliability/.
"""

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRATCH = Path(tempfile.mkdtemp(prefix="reliability-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(SCRATCH / 'unused.db').as_posix()}"
os.environ["UPLOADS_DIR"] = str(SCRATCH / "uploads")
os.environ["PAYOUT_INTERVAL_SECONDS"] = "0"
sys.path.insert(0, str(ROOT))

from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from app import models  # noqa: E402,F401  (register tables)
from app.core import memory, payouts  # noqa: E402
from app.core.claims import Upload, submit_claim  # noqa: E402
from app.core.demo import seed  # noqa: E402
from app.core.times import deadline_from  # noqa: E402
from app.domain.money import cents_to_str  # noqa: E402
from app.models import AgentLog, Item, Member, Request, utcnow  # noqa: E402

JPG = b"\xff\xd8\xff\xe0"


@dataclass
class Run:
    """One run's database session, its story clock, and what the checks need to remember between turns."""
    session: Session
    start: datetime
    gloves_request: int | None = None

    def at(self, minutes: int) -> datetime:
        return self.start + timedelta(minutes=minutes)


# The story's clock: minutes after 07:20 (today, UTC) when each turn happens. Every message, purchase and answer gets
# its story time, so they're minutes apart as in real use: run back to back, they all shared one minute, and nothing
# showed that Priya bought the gloves after the AI said she was getting them.
STORY = {"gloves": 0, "by 11": 1, "marco gloves": 4, "table": 10, "table reason": 12,
         "where gloves": 50, "budget": 55, "ladder": 60, "ladder price": 62}
BOUGHT, ASKED, ANSWERED = 25, 35, 40  # outside the chat, between "table reason" and "where gloves"


Check = Callable[[Run, str, datetime], str | None]  # the reply and when the turn started -> what's wrong, or None


def person(run: Run, name: str) -> Member:
    return run.session.exec(select(Member).where(Member.name == name)).one()


def open_requests(run: Run, name: str) -> list[Request]:
    return run.session.exec(select(Request).where(Request.member_id == person(run, name).id,
                                                  Request.status.in_(("on_it", "needs_approval")))).all()


# --- the checks -----------------------------------------------------------------------------------

def priya_is_on_the_gloves(run: Run, reply: str, started: datetime) -> str | None:
    gloves = run.session.exec(select(Item).where(Item.name == "Gloves")).one()
    mine = [r for r in open_requests(run, "Priya") if r.item_id == gloves.id and r.status == "on_it"]
    if len(mine) != 1:
        return f"Priya has {len(mine)} confirmed gloves requests, not 1"
    run.gloves_request = mine[0].id
    return None


def by_eleven(run: Run, reply: str, started: datetime) -> str | None:
    if run.gloves_request is None:
        return "no gloves request to change"
    until = run.session.get(Request, run.gloves_request).on_it_until.replace(tzinfo=started.tzinfo)
    wanted = deadline_from("by 11", started)
    return None if until == wanted else f"deadline is {until:%H:%M}, not {wanted:%H:%M}"


def marco_doesnt_double_up(run: Run, reply: str, started: datetime) -> str | None:
    if open_requests(run, "Marco"):
        return "Marco got a request: a double buy"
    return None if "Priya" in reply else "the reply doesn't say Priya is getting them"


def at_most_one_table(run: Run, reply: str, started: datetime) -> str | None:
    tables = [r for r in open_requests(run, "Marco") if r.item_id is None]
    return None if len(tables) <= 1 else f"{len(tables)} table requests"


def one_table_with_the_reason(run: Run, reply: str, started: datetime) -> str | None:
    tables = [r for r in open_requests(run, "Marco") if r.item_id is None]
    if len(tables) != 1:
        return f"{len(tables)} table requests, not 1"
    if tables[0].status != "needs_approval":
        return f"the table is {tables[0].status}, not waiting for a PIC"
    if (tables[0].estimate_low_cents, tables[0].estimate_cents) != (40_00, 40_00):
        return f"the price is {tables[0].estimate_low_cents}–{tables[0].estimate_cents} cents, not about $40"
    return None if "flyers" in tables[0].reason.lower() else f"the reason is {tables[0].reason!r}: no flyers"


def ladders(run: Run) -> list[Request]:
    return [r for r in open_requests(run, "Dana") if r.item_id is None]


ASKS_PRICE = re.compile(r"\b(cost|price|how much|spend)\b", re.I)


def asks_the_ladders_price(run: Run, reply: str, started: datetime) -> str | None:
    if ladders(run):
        return "a ladder request went to the PICs without a price"
    return None if ASKS_PRICE.search(reply) else "the reply doesn't ask what it will cost"


def ladder_waits_with_the_range(run: Run, reply: str, started: datetime) -> str | None:
    found = ladders(run)
    if len(found) != 1:
        return f"{len(found)} ladder requests, not 1"
    ladder = found[0]
    if ladder.status != "needs_approval":
        return f"the ladder is {ladder.status}, not waiting for a PIC"
    if (ladder.estimate_low_cents, ladder.estimate_cents) != (30_00, 50_00):
        return f"the price is {ladder.estimate_low_cents}–{ladder.estimate_cents} cents, not $30–$50"
    return None if "banner" in ladder.reason.lower() else f"the reason is {ladder.reason!r}: no banner"


# "My earlier message was out of date" is true, so it passes; calling it wrong or a mistake doesn't.
WRONG = re.compile(r"\b(i was wrong|that was wrong|wasn't right|mistake|correct my|i misspoke)\b", re.I)


def says_where_and_no_false_correction(run: Run, reply: str, started: datetime) -> str | None:
    if "shelf by the door" not in reply.lower():
        return "doesn't pass on Priya's answer (\"on the shelf by the door\")"
    return f"calls an earlier reply wrong: {m.group(0)!r}" if (m := WRONG.search(reply)) else None


HEDGE = re.compile(r"(can't|cannot|couldn't) (confirm|be sure|verify)|up to date|may not be (accurate|current)", re.I)


def exact_budget_no_hedge(run: Run, reply: str, started: datetime) -> str | None:
    status = memory.group_status(run.session, run.session.get(models.Group, person(run, "Dana").group_id))
    supplies = next(b for b in status.categories if b.name == "Supplies")
    left = cents_to_str(supplies.left_cents)
    if left not in reply:
        return f"doesn't say {left} is left"
    return f"hedges: {m.group(0)!r}" if (m := HEDGE.search(reply)) else None


def outside_the_chat(run: Run) -> None:
    """What happens on other pages between the chat turns: Priya hands in the receipt, Marco asks where the gloves
    are (the feed), Priya answers (her home page)."""
    priya, marco = person(run, "Priya"), person(run, "Marco")
    claim = submit_claim(run.session, priya, run.gloves_request, receipt=Upload(JPG + b" receipt", "r.jpg"),
                         store="Riverside Hardware", receipt_date=run.start.date(), total_cents=18_40,
                         photo=Upload(JPG + b" shelf", "p.jpg"), now=run.at(BOUGHT)).claim
    question = payouts.ask_buyer(run.session, claim.id, marco, "The gloves aren't in the supply box",
                                 now=run.at(ASKED))
    payouts.answer_question(run.session, question.id, priya, "On the shelf by the door", now=run.at(ANSWERED))


TURNS: list[tuple[str, str, str, Check]] = [
    ("gloves", "Priya", "We're out of gloves for Saturday's cleanup, I'll grab some", priya_is_on_the_gloves),
    ("by 11", "Priya", "by 11", by_eleven),
    ("marco gloves", "Marco", "I'll pick up gloves on my way in", marco_doesnt_double_up),
    ("table", "Marco", "Can I get a folding table for the market stall? About $40", at_most_one_table),
    ("table reason", "Marco", "We need it to show the flyers at the Saturday market", one_table_with_the_reason),
    ("where gloves", "Marco", "Did Priya say where the gloves are?", says_where_and_no_false_correction),
    ("budget", "Dana", "How much is left in the supplies budget this month?", exact_budget_no_hedge),
    ("ladder", "Dana", "I need a ladder to hang the banner at the market", asks_the_ladders_price),
    ("ladder price", "Dana", "Probably 30 to 50", ladder_waits_with_the_range),
]
OUTSIDE_AFTER = "table reason"  # the receipt, the question and the answer happen after this turn


# --- running --------------------------------------------------------------------------------------

@dataclass
class TurnResult:
    run: int
    turn: str
    reply: str
    problem: str | None
    seconds: float
    model_calls: int
    input_tokens: int
    output_tokens: int
    steps: list[str] = field(default_factory=list)


async def one_run(number: int, agent) -> list[TurnResult]:
    from app.ai.main import reply

    engine = create_engine(f"sqlite:///{(SCRATCH / f'run{number}.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    results = []
    with Session(engine) as session:
        run = Run(session, start=utcnow().replace(hour=7, minute=20, second=0, microsecond=0))
        with contextlib.redirect_stdout(io.StringIO()):
            seed(session, now=run.start)
        for label, who, text, check in TURNS:
            seen = session.exec(select(AgentLog.id).order_by(AgentLog.id.desc())).first() or 0
            started, clock = run.at(STORY[label]), time.monotonic()
            try:
                answer = await reply(session, person(run, who), text, agent=agent, now=started)
                said, problem = answer.text, "the AI failed: " + answer.text if answer.failed else None
            except Exception as exc:  # a bug, not the model: record it and stop this run
                said, problem = "", f"crashed: {type(exc).__name__}: {exc}"
            took = time.monotonic() - clock
            if problem is None:
                problem = check(run, said, started)
            log = session.exec(select(AgentLog).where(AgentLog.id > seen).order_by(AgentLog.id)).all()
            models_ = [json.loads(s.data_json) for s in log if s.event == "model"]
            results.append(TurnResult(
                number, label, said, problem, round(took, 1), len(models_),
                sum(m.get("input_tokens") or 0 for m in models_), sum(m.get("output_tokens") or 0 for m in models_),
                [f"{s.agent} {s.event} {s.name} {s.data_json[:200]}" for s in log
                 if s.event in ("tool_call", "tool_result", "rejected", "error")]))
            if problem and problem.startswith("crashed"):
                break
            if label == OUTSIDE_AFTER and run.gloves_request is not None:
                outside_the_chat(run)
    engine.dispose()
    print(f"run {number}: {sum(r.problem is None for r in results)}/{len(TURNS)} turns passed", flush=True)
    return results


async def main(runs: int, parallel: int, pinned: str) -> None:
    from app.ai.main import Coordinator

    agent = Coordinator()
    prompts = f"{agent.prompt_id}, {agent.lookup.prompt_id}, {agent.action.prompt_id}"
    print(f"{runs} runs of {len(TURNS)} turns, {parallel} at a time. Model: {os.environ.get('LLM_MODEL')}. "
          f"Prompts: {prompts}\n", flush=True)
    limit = asyncio.Semaphore(parallel)

    async def limited(n: int):
        async with limit:
            return await one_run(n, agent)
    results = [r for run in await asyncio.gather(*(limited(n) for n in range(1, runs + 1))) for r in run]

    print(f"\n{'turn':<14} {'passed':>8} {'avg s':>7} {'calls':>6} {'in tok':>8} {'out tok':>8}")
    for label, *_ in TURNS:
        mine = [r for r in results if r.turn == label]
        if not mine:
            continue
        passed = sum(r.problem is None for r in mine)
        avg = lambda f: sum(f(r) for r in mine) / len(mine)  # noqa: E731
        print(f"{label:<14} {passed:>4}/{len(mine):<3} {avg(lambda r: r.seconds):>7.1f} "
              f"{avg(lambda r: r.model_calls):>6.1f} {avg(lambda r: r.input_tokens):>8.0f} "
              f"{avg(lambda r: r.output_tokens):>8.0f}")
    total = sum(r.problem is None for r in results)
    print(f"{'all':<14} {total:>4}/{len(results):<3}")

    failed = [r for r in results if r.problem]
    if failed:
        print("\nFailures:")
    for r in failed:
        print(f"- run {r.run}, {r.turn}: {r.problem}\n    reply: {r.reply}")

    out = ROOT / "data" / "reliability"
    out.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d-%H%M%S}{'-' + pinned.replace(',', '_').replace('=', '') if pinned else ''}.json"
    (out / name).write_text(json.dumps({"prompts": prompts, "model": os.environ.get("LLM_MODEL"),
                                        "results": [asdict(r) for r in results]}, indent=1, ensure_ascii=False),
                            encoding="utf-8")
    print(f"\nSaved: data/reliability/{name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--prompts", default="", help='pinned versions, e.g. "coordinator=6"')
    args = parser.parse_args()
    if args.prompts:
        os.environ["PROMPT_VERSIONS"] = args.prompts
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main(args.runs, args.parallel, args.prompts))
