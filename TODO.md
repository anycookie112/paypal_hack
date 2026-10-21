# Group Fund: progress and to-do

Due **Nov 12, 2026**. The full plan is in [docs/SPEC.md](docs/SPEC.md) §15; how prompts are written is in
[docs/PROMPTS.md](docs/PROMPTS.md). Updated 2026-10-21.

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

**Tests:** 203, all passing.

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

## Done since: claims (2026-10-16)

- `app/core/claims.py`: hand in a receipt for a confirmed request. Code checks it's theirs and confirmed, dated right,
  not a duplicate (same file → refused; same store + date + total → a PIC), within the limit (the extra waits for a PIC
  with the member's reason, never over the hard ceiling), and payable (PayPal email; the item photo if needed). What's
  missing comes back as a list. Anything unusual goes to a PIC (`review`), who accepts or rejects it. Accepted: the
  request is `claimed`, the group sees "Priya got Gloves, $18.40", the 3-day hold starts.
- Files are checked by their first bytes (JPG, PNG, HEIC, WEBP, PDF; up to 10 MB) and saved under `UPLOADS_DIR` by hash;
  only the member and the PICs can open them.
- `app/jobs.py`: the server pays what's due every 5 minutes (`PAYOUT_INTERVAL_SECONDS`), only with PayPal configured.
- Web: a Claim page (pick the request, receipt, photo, store, date, total; the reason when over the limit) and a Review
  page for PICs (the receipt, the reasons, accept or reject).
- **The claim table has a new column:** reset the local demo database with `uv run python scripts/seed_demo.py --reset`.

## Done since: member pages (2026-10-19)

- **Home:** the member's month (limit used), what they're getting (hand in the receipt, or "can't get it" to release),
  what's being paid back and when, questions others asked them (answered right there), news (notifications, mark
  read), and the group's budgets this month.
- **Feed** (`app/core/feed.py`): on-it and got-it posts with their status, 👍, and "something's wrong?" during the hold:
  first ask the buyer; only if that doesn't sort it, a formal report that goes privately to the treasurer.
- **Chat** (`app/api/chat.py`): the coordinator in the web app, the reply streamed as it's written (newline-delimited
  JSON), with plain-words steps ("Checking the group's records…"); the tools' insides never reach the browser.
- Tried end to end in the browser with Gemini: Marco was told Priya is on the gloves; a 👍; Marco asked about the bags,
  Priya answered from her home page, and Marco saw the answer.

## Done since: Claude Haiku 5.5 (2026-10-21)

- `LLM_PROVIDER=anthropic` (LangChain's Anthropic client, `ANTHROPIC_API_KEY`, optional `LLM_EFFORT`). Each agent run now
  builds its prompt once (Claude refuses a thinking block sent back after its prompt changed).
- Demo story in the browser with Haiku: every model call 1–2 s, chat turns 2–8 s (Gemini Flash Lite: 3–23 s), replies
  correct and closer to the prompt.

## Done since: typed results and the state log (2026-10-21)

- **Typed subagent results, checked by code.** Every tool returns a typed record (`ItemRequested`, `Purchase`,
  `Budgets`, …); lookup and act finish by copying those records exactly (`LookupResult` / `ActResult`, no free-text
  fields). `BaseAgent` runs the output's `check()` against the run's tool results and sends any difference back to the
  model (twice, then the run fails). In the Haiku run, every record was copied right the first time; the hedge and
  the made-up "request screen" are gone. act also has `cant_do` and a fixed list of what can be `missing`.
- **Asking twice is refused by code:** a member's second open request for the same thing (same item, or the same
  words) points to the first. `add_reason` adds to a request waiting for a PIC and tells the PICs.
- **A reason must be the member's own words** (checked like `when_said`).
- **Lookup sees the buyer's answers** ("on the shelf by the door").
- **States** (`app/core/states.py`): one table of every request and claim status and the moves between them, with
  why; every status change goes through `move()`, which refuses a move the table doesn't have and logs it
  (`StateChange`). Coordinator **v7** gets the map (`<lifecycle>`, generated from the table) and what moved since its
  last reply (`<since_your_last_reply>`): v6 had told Marco "I was wrong" about gloves Priya bought after it spoke.
- Prompts: lookup v5, action v6, coordinator v6 → v7. Coordinator calls read ~950 more tokens (mostly the fixed map).
- **New table** (`statechange`): `uv run python scripts/seed_demo.py --reset` for an old local database.

## Found in the Haiku demo runs (to fix)

- [x] The AI can't see answers to questions (now in the `purchase` record).
- [x] No way to add a reason to a request waiting for a PIC (`add_reason`; asking twice is refused).
- [x] The budget hedge ("I can't confirm these figures are up to date") came from the coordinator's brief: v6 asks only
      for what the member needs, and lookup can only return records.
- [ ] Haiku adds "I can't promise they'll approve it" to almost every reply; uses he/she, not names. (Prompt v8.)
- [ ] A list item that needs no reason still stores the member's whole sentence as its reason.
- [ ] One clean run isn't proof: the reliability script should replay the demo turns several times.

## Next (in order)

1. [ ] **PIC approvals queue:** requests waiting for a PIC (with the reason, and approve / reject) next to the receipt
       reviews, so the demo story doesn't stop at "waiting for a PIC".
2. [ ] **Reliability script:** each demo turn 5–10 times on Haiku, wrong answers counted per turn and prompt version.
3. [ ] **PayPal side of the demo:** top-up with PayPal Checkout, the ledger, and a demo "fast-forward" for the hold.
4. [ ] Receipt reader.
5. [ ] Deploy to Render; demo video, Devpost write-up, README.

- [ ] Admin: a members page.
- [ ] PIC approvals in chat; the confirm card for formal reports in chat.
- [ ] A per-member scratchpad (preferences, availability) only if wanted later: what changed since the last reply now
      comes from the state log, which code writes.
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

- [ ] Receipt reader (its own pipeline, vision model): pre-fills the claim form; code compares.
- [ ] Model retry / fallback to a second provider.
- [ ] No chat bot (Telegram/Discord): a plain web app, no sign-in (decided 2026-10-21).
- [ ] Demo video, Devpost write-up, README.
