# Order Assistant (working name)

An AI ordering assistant any small business can set up. Owners add their products and plain-English rules; customers describe what they need and their budget; PayPal handles payment the way each product needs (pay now, hold then charge the exact amount, or deposit + balance).

Built for the PayPal AI Hackathon 2026. Sandbox only, no real money.

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

Open **http://localhost:5173**. To try the production build instead, run `npm run build` in `frontend/`; the API server then serves the app itself on http://localhost:8000.

The app starts with an empty database (`data/app.db`). Click **Set up a business**.

## Owner onboarding

1. **Business:** name, type, PayPal email for receiving payments.
2. **Products:** upload a CSV (template downloadable in the app), paste a menu for the AI to read, or add products by hand. Everything is shown for review before saving.
3. **Rules & payments:** the business starts with default settings for its type (e.g. a bakery needs 1 day's notice). Change any setting with fixed fields, turn it off, or make it different for a category (e.g. custom cakes take a 50% deposit). Under **Advanced**, owners comfortable with AI can write policies in plain English instead; each proposed rule is validated by code and confirmed before saving.

AI features need `LLM_API_KEY` (any OpenAI-compatible endpoint; default Atria Dawn). Without it, CSV upload, manual entry and all settings still work.

## Tests

```bash
uv run pytest
```

## Deploy (Render)

- Build command: `cd frontend && npm ci && npm run build && cd .. && uv sync --frozen`
- Start command: `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set the environment variables from `.env.example`. Render's free disk is wiped on each deploy, so the app starts empty again.

## Project layout

```
app/
  main.py           FastAPI app: mounts the API, serves frontend/dist
  config.py db.py   settings from .env; SQLite via SQLModel
  models.py         Business, Product, Rule tables
  api/              JSON API under /api: meta, businesses, products, rules (one router each)
  domain/           business logic with no web or AI code: money, catalog (products, CSV), rules
  ai/               everything AI, on Pydantic AI
    model.py          LLM connection (any OpenAI-compatible endpoint)
    base.py           BaseAgent: prompts, tools, normal + streamed output, handovers
    prompts.py        every prompt, versioned (pin with PROMPT_VERSIONS=front_desk=1)
    tools.py          read-only tools over one shop's data
    agents.py         customer agents: front desk -> order builder
    onboarding.py     pasted menu / policies -> drafts (Advanced setup)
frontend/           React 19 + TypeScript + HeroUI v3 + Tailwind v4 (Vite)
tests/              pytest (fake AI models, throwaway database)
scripts/
  paypal/           sandbox experiments: holds/captures/refunds, paying a third-party business
  kroger/           Kroger API explorer; builds a real test catalog into data/
docs/SPEC.md        product spec and decisions
```
