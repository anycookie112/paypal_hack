"""Chat with the shop assistant in the terminal, then pay for its order through the PayPal sandbox.

    uv run python scripts/ai/chat.py                      # the demo bakery (all three payment styles)
    uv run python scripts/ai/chat.py "2 dozen cookies"    # one message, then exit
    uv run python scripts/ai/chat.py --pin order_builder=1 --trace
    uv run python scripts/ai/chat.py --db data/app.db --slug sweet-crumb-bakery   # a shop you set up in the app

You type as the customer; you see the reply stream in, every tool call and handover, and the order
priced by code. Then /pay, approve in the browser as the sandbox buyer, /approve, and act as the
owner with /complete, /cancel and /refund. Uses its own database (data/cli.db) by default, so the
app's data isn't touched. Payments go to SUPPLIER_EMAIL from .env (the demo bakery's PayPal).
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chat with the shop assistant and test payments.")
    parser.add_argument("message", nargs="*", help="send one message and exit (otherwise interactive)")
    parser.add_argument("--db", default="data/cli.db", help="database file (default: data/cli.db)")
    parser.add_argument("--slug", help="a business in that database instead of the demo bakery")
    parser.add_argument("--pin", action="append", default=[], metavar="NAME=VERSION",
                        help="use a prompt version, e.g. --pin order_builder=1 (repeatable)")
    parser.add_argument("--trace", action="store_true", help="also show what each tool returned")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args()


ARGS = parse_args()
# Settings are read when the app is imported, so set them first.
os.environ["DATABASE_URL"] = f"sqlite:///{(ROOT / ARGS.db).resolve().as_posix()}"
if ARGS.pin:
    os.environ["PROMPT_VERSIONS"] = ",".join(ARGS.pin)
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from sqlmodel import Session, select  # noqa: E402

from app.ai import AgentError, prompts  # noqa: E402
from app.ai.agents import OrderProposal, customer_assistant  # noqa: E402
from app.ai.tools import CartLine, ShopDeps, plan_for_cart  # noqa: E402
from app.db import engine, init_db  # noqa: E402
from app.domain.money import cents_to_str, to_cents  # noqa: E402
from app.models import Business, Order, Payment  # noqa: E402
from app.payments import service  # noqa: E402
from app.payments.paypal import PayPalClient, PayPalError  # noqa: E402
from app.payments.plan import PlanError  # noqa: E402
from demo_shop import seed  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
COLOR = sys.stdout.isatty() and not ARGS.no_color


def paint(code: str):
    return lambda text: f"\033[{code}m{text}\033[0m" if COLOR else str(text)


dim, bold, green, red, cyan, yellow = map(paint, ["2", "1", "32", "31", "36", "33"])

HELP = """
Type a message as the customer. Commands:
  /cart                      the last proposed order, priced by code
  /pay                       create the order and a PayPal sandbox checkout link
  /approve                   after approving in the browser: place the hold / take the payment
  /order [ID]                the order's status and every PayPal payment (an ID picks up an
                             order from an earlier session, e.g. to /approve it)
  /complete [LINE=AMOUNT]    owner: order is ready, take the final amount (e.g. /complete 2=12.80
                             for a weighed line; lines are numbered as in /cart)
  /cancel                    owner: cancel and release the hold
  /refund [CAPTURE] AMOUNT   owner: refund part of a charge (default: the latest charge)
  /prompts                   the prompt versions in use
  /reset                     start a new conversation
  /quit
"""


class Chat:
    def __init__(self, session: Session, business: Business):
        self.session, self.business = session, business
        self.paypal = PayPalClient()
        self.transcript: list[tuple[str, str]] = []
        self.proposal: OrderProposal | None = None
        self.order: Order | None = None

    # --- talking to the assistant -------------------------------------------------------------

    def deps(self) -> ShopDeps:
        return ShopDeps.load(self.session, self.business)

    def prompt_for(self, message: str) -> str:
        if not self.transcript:
            return message
        history = "\n".join(f"{who}: {text}" for who, text in self.transcript[-12:])
        return f"Conversation so far:\n{history}\n\nThe customer's new message:\n{message}"

    async def send(self, message: str) -> None:
        deps, start, streamed = self.deps(), time.time(), False
        print(cyan("assistant ›"), end=" ", flush=True)
        async for event in customer_assistant().stream(self.prompt_for(message), deps=deps):
            if event.type == "text":
                print(event.text, end="", flush=True)
                streamed = True
            elif event.type == "tool_call":
                args = json.dumps(event.data, ensure_ascii=False)
                print(dim(f"\n  → {event.agent}: {event.name}({args[:100]}{'…' if len(args) > 100 else ''})"), end="", flush=True)
            elif event.type == "tool_result" and ARGS.trace:
                data = json.dumps(event.to_json()["data"], ensure_ascii=False)
                print(dim(f"\n    ← {data[:300]}{'…' if len(data) > 300 else ''}"), end="", flush=True)
            elif event.type == "handover":
                print(yellow(f"\n  ⇢ handed over to {event.name}: {event.text}"), end="", flush=True)
            elif event.type == "output":
                result = event.result
        print()
        output = result.output
        if isinstance(output, OrderProposal):
            self.proposal = output
            self.show_cart(deps)
            lines = ", ".join(f"{line.quantity:g} x {deps.product(line.product_id).name}" for line in output.lines)
            self.transcript.append(("Assistant", f"{output.explanation} [Proposed order: {lines or 'none'}; "
                                                 f"questions: {'; '.join(output.questions) or 'none'}]"))
        else:
            if not streamed:
                print(output)
            self.transcript.append(("Assistant", str(output)))
        self.transcript.insert(-1, ("Customer", message))
        print(dim(f"  ({time.time() - start:.0f}s, {result.usage.requests} model calls, "
                  f"{result.usage.tool_calls} tool calls, {result.prompt_id})"))

    def show_cart(self, deps: ShopDeps | None = None) -> None:
        if not self.proposal:
            print("No order proposed yet.")
            return
        deps, p = deps or self.deps(), self.proposal
        if p.lines:
            try:
                plan = plan_for_cart(deps, [CartLine(product_id=x.product_id, quantity=x.quantity) for x in p.lines],
                                     p.ready_by)
            except PlanError as exc:
                print(red(f"  Can't price this order: {exc}"))
                return
            print(bold("\n  Proposed order") + (f" — ready by {p.ready_by:%a %d %b}" if p.ready_by else ""))
            for i, (line, planned) in enumerate(zip(p.lines, plan.lines)):
                print(f"  {i}. {line.quantity:g} x {planned.name:<34} {cents_to_str(planned.estimate_cents):>9}"
                      + dim(f"   {planned.style.replace('_', ' ')}"))
            print(f"  {'Total':>40} {bold(cents_to_str(plan.estimate_cents)):>9}"
                  + (dim(f"   budget {cents_to_str(round(p.budget * 100))}") if p.budget is not None else ""))
            for sentence in plan.summary():
                print(green(f"  {sentence}"))
        print(f"  {p.explanation}")
        for question in p.questions:
            print(yellow(f"  ? {question}"))

    # --- payments -------------------------------------------------------------------------------

    async def pay(self) -> None:
        if not self.proposal or not self.proposal.lines:
            print("There's no order to pay for yet.")
            return
        self.order = service.create_order(self.session, self.business,
                                          [(x.product_id, x.quantity) for x in self.proposal.lines],
                                          ready_by=self.proposal.ready_by, note="Created in the CLI")
        url = await service.checkout(self.session, self.paypal, self.order, self.business)
        print(f"Order {self.order.public_id}: {cents_to_str(self.order.hold_cents)} via PayPal ({self.order.intent}).")
        print(bold(f"\n  {url}\n"))
        print("Open it in a private window, log in as the Personal sandbox buyer and approve. PayPal then\n"
              "redirects to the app, which may not be running: that's fine. Come back and type /approve.")

    async def approve(self) -> None:
        if not self.order:
            print("Use /pay first.")
            return
        await service.approve(self.session, self.paypal, self.order)
        self.show_order()

    async def complete(self, args: list[str]) -> None:
        final = {}
        for arg in args:
            index, _, amount = arg.partition("=")
            final[int(index)] = to_cents(amount)
        await service.complete(self.session, self.paypal, self.order, final)
        self.show_order()

    async def refund(self, args: list[str]) -> None:
        if not args:
            print("Usage: /refund [CAPTURE_ID] AMOUNT")
            return
        if len(args) == 1:
            last = self.session.exec(select(Payment).where(Payment.order_id == self.order.id,
                                                           Payment.action == "capture").order_by(Payment.id.desc())).first()
            args = [last.paypal_id if last else "", args[0]]
        await service.refund(self.session, self.paypal, self.order, args[0], to_cents(args[1]))
        self.show_order()

    def show_order(self) -> None:
        if not self.order:
            print("No order yet. Use /pay.")
            return
        o = self.order
        self.session.refresh(o)
        print(bold(f"\n  Order {o.public_id}: {o.status.replace('_', ' ')}")
              + (f"  ({o.customer_name}, {o.customer_email})" if o.customer_email else ""))
        print(f"  held {cents_to_str(o.hold_cents)} | charged {cents_to_str(o.captured_cents)} | "
              f"refunded {cents_to_str(o.refunded_cents)} | PayPal fees {cents_to_str(o.fee_cents)}")
        for p in self.session.exec(select(Payment).where(Payment.order_id == o.id).order_by(Payment.id)).all():
            extra = " (final: rest released)" if p.final else ""
            parent = f" of {p.parent_id}" if p.parent_id else ""
            print(dim(f"  {p.created_at:%H:%M:%S}  {p.action:<9} {cents_to_str(p.amount_cents):>9}  "
                      f"{p.status:<10} {p.paypal_id}{parent}{extra}"))

    # --- the loop -------------------------------------------------------------------------------

    def command(self, line: str) -> bool:
        """Run a /command. Returns False to quit."""
        name, *args = line.split()
        needs_order = {"/complete", "/cancel", "/refund"}
        if name in needs_order and not self.order:
            print("No order yet. Use /pay.")
            return True
        if name in ("/quit", "/exit"):
            return False
        if name == "/help":
            print(HELP)
        elif name == "/cart":
            self.show_cart()
        elif name == "/pay":
            asyncio.run(self.pay())
        elif name == "/approve":
            asyncio.run(self.approve())
        elif name == "/order":
            if args:
                self.order = self.session.exec(select(Order).where(Order.public_id == args[0])).first()
                if not self.order:
                    print(f"No order {args[0]} in {ARGS.db}")
                    return True
            self.show_order()
        elif name == "/complete":
            asyncio.run(self.complete(args))
        elif name == "/cancel":
            asyncio.run(service.cancel(self.session, self.paypal, self.order))
            self.show_order()
        elif name == "/refund":
            asyncio.run(self.refund(args))
        elif name == "/prompts":
            for agent in ("front_desk", "order_builder"):
                print(f"  {prompts.get(agent).id}")
        elif name == "/reset":
            self.transcript, self.proposal, self.order = [], None, None
            print("New conversation.")
        else:
            print(f"Unknown command {name}. /help lists them.")
        return True

    def handle(self, line: str) -> bool:
        try:
            if line.startswith("/"):
                return self.command(line)
            asyncio.run(self.send(line))
        except (AgentError, PayPalError, service.PaymentError, PlanError, ValueError) as exc:
            print(red(f"\n  {type(exc).__name__}: {exc}"))
        except KeyboardInterrupt:
            print(yellow("\n  (stopped)"))
        return True


def masked(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:5]}…@{domain}" if domain else email


def main() -> None:
    init_db()
    with Session(engine) as session:
        if ARGS.slug:
            business = session.exec(select(Business).where(Business.slug == ARGS.slug)).first()
            if not business:
                sys.exit(f"No business '{ARGS.slug}' in {ARGS.db}")
        else:
            business = seed(session, os.environ.get("SUPPLIER_EMAIL") or "demo@example.com")
        chat = Chat(session, business)
        if ARGS.message:
            chat.handle(" ".join(ARGS.message))
            return
        deps = chat.deps()
        print(bold(f"{business.name}") + f" — {len(deps.products)} products, payments to {masked(business.paypal_email)}")
        print(dim(f"prompts: {prompts.get('front_desk').id}, {prompts.get('order_builder').id} | "
                  f"database: {ARGS.db} | /help for commands"))
        while True:
            try:
                line = input(f"\n{bold('you ›')} ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if line and not chat.handle(line):
                break


if __name__ == "__main__":
    main()
