"""Live test: does the assistant price orders through the billing tool and explain payment correctly?

Runs customer messages against the real model (LLM_* in .env) with the demo bakery
(demo_shop.py, all three payment styles), then checks each answer in code. Scenarios run in parallel.

Usage:
  uv run python -u scripts/ai/billing_scenarios.py            # all scenarios
  uv run python -u scripts/ai/billing_scenarios.py party rye   # some
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.ai.agents import OrderProposal, customer_assistant  # noqa: E402
from app.ai.tools import CartLine, plan_for_cart  # noqa: E402
from app.domain.money import cents_to_str  # noqa: E402
from demo_shop import DEPS  # noqa: E402



def has(text: str, *words: str) -> bool:
    return all(w.lower() in text.lower() for w in words)


# name -> (message, check(output, text) -> list of problems)
SCENARIOS = {
    "party": ("Kid's party a week from Saturday, 20 kids, about $120, one has a nut allergy.",
              lambda o, t: [] if isinstance(o, OrderProposal) and o.lines and has(o.explanation, "deposit")
              else ["expected an order whose explanation mentions the cake deposit"]),
    "rye": ("I'd like about 3 lb of rye bread and 6 bagels for Friday.",
            lambda o, t: [] if isinstance(o, OrderProposal) and o.lines and
            any(w in o.explanation.lower() for w in ("held", "hold", "exact"))
            else ["expected the explanation to say the bread is held and charged at the exact weight"]),
    "tight": ("Can I get a custom cake? My budget is $30.",
              lambda o, t: [] if isinstance(o, OrderProposal) and (not o.lines or o.questions or o.budget == 30)
              else ["expected no over-budget cake: offer something else or ask"]),
    "question": ("Do I have to pay for a custom cake all upfront?",
                 lambda o, t: [] if has(t, "deposit") and "50" in t else ["expected the 50% deposit to be explained"]),
    "wedding": ("A wedding cake for 120 guests in two months please.",
                lambda o, t: [] if not isinstance(o, OrderProposal) or not o.lines or o.questions
                else ["wedding cakes are priced on request: expected questions, not a priced order"]),
}


async def run(name: str) -> str:
    message, check = SCENARIOS[name]
    out = [f"\n=== {name}: {message}"]
    start, steps, text = time.time(), [], ""
    try:
        async for event in customer_assistant().stream(message, deps=DEPS):
            if event.type == "text":
                text += event.text
            elif event.type == "tool_call":
                steps.append(f"{event.name}({json.dumps(event.data)[:80]})")
            elif event.type == "handover":
                steps.append(f"-> {event.name}")
            elif event.type == "output":
                result = event.result
    except Exception as exc:
        return "\n".join(out + [f"   FAILED after {time.time() - start:.0f}s: {type(exc).__name__}: {exc}"])

    output = result.output
    out.append(f"   {time.time() - start:.0f}s, {result.usage.requests} model calls, {result.prompt_id}")
    out.append("   steps: " + " | ".join(steps))
    problems = []
    if isinstance(output, OrderProposal):
        for line in output.lines:
            product = DEPS.product(line.product_id)
            out.append(f"   {line.quantity:g} x {product.name if product else '??? unknown id'}  ({line.reason})")
        if output.lines:
            plan = plan_for_cart(DEPS, [CartLine(product_id=x.product_id, quantity=x.quantity) for x in output.lines])
            out.append(f"   code total {cents_to_str(plan.estimate_cents)} | now {cents_to_str(plan.due_now_cents)}"
                       f" | held {cents_to_str(plan.hold_cents - plan.due_now_cents)} | budget {output.budget}")
        out.append(f"   explanation: {output.explanation}")
        if output.questions:
            out.append(f"   questions: {output.questions}")
        final_text = output.explanation + " " + " ".join(output.questions)
    else:
        out.append(f"   answer: {output}")
        final_text = str(output)
    problems += check(output, final_text or text)
    out.append("   PASS" if not problems else "   CHECK FAILED: " + "; ".join(problems))
    return "\n".join(out)


async def main(names: list[str]) -> None:
    for report in asyncio.as_completed([run(name) for name in names]):
        print(await report, flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # model replies can contain emoji
    asyncio.run(main(sys.argv[1:] or list(SCENARIOS)))
