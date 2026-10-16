# Group Fund: progress and to-do

Due **Nov 12, 2026**. The full plan is in [docs/SPEC.md](docs/SPEC.md) §15; how prompts are written is in
[docs/PROMPTS.md](docs/PROMPTS.md). Updated 2026-10-15.

## Done

**Core (`app/core/`, no web or AI code)**
- Tables, settings with defaults, starter item list, demo seed script (`scripts/seed_demo.py`).
- Decision engine: confirmed / needs a PIC / declined / already on it, with reasons in plain words.
- Requests: tell the group, release, approve, reject; the member's own deadline (`hours`, capped).
- Payouts: the 3-day hold, reports that extend it, over-budget extras, the ledger, `run_due` against PayPal.
  Checked once in the sandbox (`scripts/paypal/core_payout_check.py`).
- Memory: who's on what, purchases, budgets, conversations, the agent log, and the **item board** (every item
  grouped as being got / running low / recently bought / available, using the decision engine's own rules).
- Setup (`setup.py`): items and categories (admins only; switched off, never deleted; caps up to the hard ceiling;
  names can't clash), the group's name, budget and settings, all checked before any is saved.

**AI (`app/ai/`)**
- `BaseAgent` on LangChain `create_agent`: prompts, provider, streaming, tools; finish by calling an output schema;
  errors go back to the model as tool results; per-agent limits; partial output (`completed`) on failure;
  repeated calls refused; runaway replies cut to 5 calls.
- Coordinator (`main.py`) with two subagents: lookup (read tools) and act (action tools). `reply()` /
  `stream_reply()` keep the conversation and log every step.
- Prompts v1 → v3. v3 puts the item list in `<chat_context>`, so "I'm getting gloves" needs no lookup call.
- Providers: `LLM_PROVIDER=google` (Gemini, native client) or `openai` (any OpenAI-compatible endpoint).
- Live test (`scripts/try_chat.py`) on Gemini 3.5 Flash Lite, v3: 4 turns in ~14 s (v2: ~25 s), all correct.

**Web**
- API: "View as" sign-in (`X-Member-Id`), `/api/members`, `/api/catalog`, `/api/items`, `/api/categories`,
  `/api/settings`. Errors come back in plain words (403 for "admins only").
- React + HeroUI: the "View as" switcher (`?as=sam`), an Items page and a Settings page (read-only for all but the
  treasurer).

**Tests:** 156, all passing.

## Done since: prompts v4 and v5 (2026-10-15)

- `<member>` in `<chat_context>`: the member's own requests, limit, receipts waiting and questions for them; replaces
  the lookup agent's `my_status` tool.
- Times: the AI passes the member's **own words** (`when_said`), code checks them against what the member wrote (an
  invented time is refused) and turns them into an **exact deadline** (`app/core/times.py`: "by 11" → 11:00). No
  "when?" question first: the group's usual time unless they said one. `change_time` moves a deadline.
- Missing details are decided by code (`NeedsInfo`: "Missing: why it's needed").
- Subagent descriptions say when (and when not) to use them, without example sentences (models copied them).
- Every model call and tool result is timed in the agent log (ms, tokens).
- Live on Gemini (v5): 4/4 turns correct. Each Gemini call took 3–5.5 s that day; our code takes ~20 ms. A turn that
  goes through `act` or `lookup` costs 4 model calls (~20 s on a slow day); one answered from `<chat_context>`, 1.

## Next

- [ ] Claims (core step 4): see "Still to build".
- [ ] Optional speed-up: let the coordinator call simple action tools (`get_item`, `change_time`) itself, which would
      save the action agent's 2 model calls per action (changes the coordinator + subagents design: decide first).

## Open decisions

- [ ] **Output schema examples** are sometimes copied word for word by weak models (Atria): make them templates?
- [ ] **Times are shown in UTC** ("10:40 UTC"): add a group timezone (Settings page).
- [ ] **OpenRouter Gemma** (`google/gemma-4-26b-a4b-it:free`): untested; the free pool was rate-limited (429).
      Retry later, or add our Gemini key to OpenRouter's integrations.
- [ ] Prompt caching: `<chat_context>` changes every call, so the history after it isn't cached. Move it after the
      history only if prompts grow long enough for it to matter.

## Still to build (SPEC §15)

- [ ] **Claims (step 4):** receipt upload linked to a confirmed request; date, duplicate and budget checks; the
      over-budget extra with a justification; "got it" feed posts; `start_hold`.
- [ ] Background loop that sends due payouts.
- [ ] **Member pages:** home (budget left, my requests and claims, notifications), new request, claim, feed (👍,
      report a problem).
- [ ] **Admin pages:** members, approvals queue and alerts, ledger with a demo "fast-forward", top-up with PayPal
      Checkout.
- [ ] **Chat page** with streaming (`stream_reply`); PIC approvals in chat; the confirm card for formal reports.
- [ ] Receipt reader (its own pipeline, vision model): pre-fills the claim form; code compares.
- [ ] Model retry / fallback to a second provider.
- [ ] Telegram or Discord bot (week 4), reliability script (~15 requests), deploy to Render.
- [ ] Demo video, Devpost write-up, README.
