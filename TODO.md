# Group Fund: progress and to-do

Due **Nov 12, 2026**. The full plan is in [docs/SPEC.md](docs/SPEC.md) §15; how prompts are written is in
[docs/PROMPTS.md](docs/PROMPTS.md). Updated 2026-10-13.

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

**Tests:** 128, all passing (one is flaky: see below).

## Next: prompts v4 (agreed in principle, not started)

From the v3 live run:
- [ ] **Round hours up:** "by 11" at 8:40 became 2 hours (10:40). Coordinator prompt and `get_item`'s `hours`
      docstring: whole hours from now, rounded up.
- [ ] **Member profile in `<chat_context>`:** a `<member>` block (their limit and what's used, open requests, claims
      waiting, questions for them, PayPal email set). Fixes "you're *already* on it" right after confirming, and
      replaces the lookup agent's `my_status` tool.
- [ ] **No half-filled calls to act:** turn 1 sent act a request with no time, so act could only ask "when?".
      Coordinator: only call act when it has everything it needs.
- [ ] Rerun `try_chat.py` on Gemini and compare with v3.

## Open decisions

- [ ] **Flaky test** `test_a_runaway_reply_is_cut_down`: the 5 calls run at the same time, so their order varies.
      Fix: compare `sorted(chat.asked)`, and type `asked` as `list[int]` (removes the warnings).
- [ ] **Output schema examples** are sometimes copied word for word by weak models (Atria): make them templates?
- [ ] **`.env` defaults:** add `LLM_PROVIDER=google` and `LLM_MODEL=gemini-3.5-flash-lite` (runs pass them inline
      today).
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
