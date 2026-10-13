# Group Fund (working name)

For volunteer groups and clubs, where everyone chips in and no one is in charge of buying. Members ask a bot before
buying something for the group (gloves for the cleanup, snacks for the meeting); the bot says if someone's already on
it, and code checks the request against the group's budget and settings. After they buy and submit the receipt, the
purchase is announced to the group, and once a short waiting period passes with no problems reported, PayPal Payouts
pays them back. The AI coordinates, drafts and reads; it never approves or moves money.

Built for the PayPal AI Hackathon 2026. Sandbox only, no real money. The design and build plan are in
[docs/SPEC.md](docs/SPEC.md). **Status:** rebuilding from an earlier idea (an ordering assistant for small
businesses, still on `main`); the app itself comes next.

## Run locally

Requires Python 3.13 with [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
cp .env.example .env              # fill in your keys
uv sync
cd frontend && npm install && cd ..
```

Development (two terminals):

```bash
uv run python main.py             # API on http://localhost:8000
cd frontend && npm run dev        # app on http://localhost:5173 (proxies /api to :8000)
```

To try the production build instead, run `npm run build` in `frontend/`; the API server then serves the app itself
on http://localhost:8000.

AI features need `LLM_API_KEY` (any OpenAI-compatible endpoint).

## Tests

```bash
uv run pytest
```

## Demo data

```bash
uv run python scripts/seed_demo.py --reset    # Riverside Volunteers: 4 people, starter list, past purchases
```

## PayPal experiments

```bash
uv run python scripts/paypal/payouts.py send             # raw Payouts API: sandbox payout to BUYER_EMAIL
uv run python scripts/paypal/core_payout_check.py      # app/core end to end: request → claim → hold → payout
```

## Deploy (Render)

- Build command: `cd frontend && npm ci && npm run build && cd .. && uv sync --frozen`
- Start command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set the environment variables from `.env.example`. Render's free disk is wiped on each deploy, so the app starts
  empty again.

## Project layout

```
app/
  main.py           FastAPI app: mounts the API, serves frontend/dist
  config.py db.py   settings from .env; SQLite via SQLModel
  models.py         tables (SPEC §14.1)
  core/             decides and moves money, no web or AI code: settings, decisions, requests, payouts, memory
  api/              JSON API under /api: thin, calls app/core
  domain/money.py   dollars <-> cents
  ai/               everything AI, on LangChain + LangGraph
    model.py          LLM connection (any OpenAI-compatible endpoint)
    base.py           BaseAgent on LangChain's create_agent: prompts, model, tools, run + stream; as_tool
    prompts.py        every prompt, versioned (pin with PROMPT_VERSIONS=name=1)
  payments/paypal.py PayPal REST client
frontend/           React 19 + TypeScript + HeroUI v3 + Tailwind v4 (Vite)
tests/              pytest (fake chat model, throwaway database)
scripts/paypal/     sandbox experiments: payouts; holds/captures/refunds and third-party payees (earlier idea)
scripts/seed_demo.py the demo group with past purchases (--reset to start over)
docs/SPEC.md        product spec, architecture and build plan
```
