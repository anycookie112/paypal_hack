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

**Tests:** 248, all passing.

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

## Done since: reliability script, coordinator v8, the Lab (2026-10-21)

- `scripts/reliability.py`: the demo story N times against the real model, each turn checked by code (records and
  reply), pass rates per turn, every reply saved to `data/reliability/`. `--prompts coordinator=7` compares versions.
- 10 runs of v7: 62/70; "did Priya say where the gloves are?" passed 2/10 (5 runs said "I was wrong earlier").
  **v8** (answer what was asked; earlier replies were true when sent) and a framed `<since_your_last_reply>`: **70/70**.
- **Lab page** (`/lab`, `app/api/lab.py`): the four members' chats side by side with their demo lines, the AI log for
  everyone (subagent steps indented, model calls optional, click a step for its data), open requests, receipts and
  the latest moves, and "Reset the demo" (`LAB_RESET=0` turns it off). The demo seed is now `app/core/demo.py`.

## Done since: the member's own words (2026-10-22)

- act gets the member's last 3 messages word for word in `<chat_context>` (`<member_said>`, added by code) and copies
  reasons and times from there; the coordinator quotes the member word for word instead of retelling them.
- action v7 / coordinator v9, 10 runs: 70/70; quote refusals 18 → 0; the "table reason" turn 6.6 → 4.0 model calls,
  9.7 → 5.8 s. But v7's rule quoted the test's own words as its example, and v9 had a template.
- **action v8 / coordinator v10**: the same rules in general words, no examples. 10 runs: 70/70; "table reason" 4.0
  calls; 1 quote refusal (an invented `when_said: "now"`, retried once).

## Done since: a price range for anything not on the list (2026-10-22)

- Something not on the list needs a rough price and a reason before a PIC sees it; code lists everything missing at
  once ("Missing: a price range; why it's needed"). The price is the member's own words (`price_said`, quote-checked),
  turned into a low–high range by code (`app/core/prices.py`). The rules count the top end; it's declined only if
  even the low end is over the hard ceiling (otherwise a PIC can approve up to the ceiling). The PICs see the range.
- Reasons are never judged by the AI: the coordinator asks once more only if an answer says nothing about why.
- Prompts: **action v9**, **coordinator v11** (general wording, no examples).
- Reliability: two new turns (Dana's ladder, no price, then "Probably 30 to 50"); the table turn checks "about $40".
  10 runs: 89/90; both ladder turns 10/10. One "where gloves" reply said "I was wrong earlier" again (v8/v10: 0/10).
- **New column** (`request.estimate_low_cents`): `uv run python scripts/seed_demo.py --reset` (or the Lab's reset).

## Done since: what each person did, and when (2026-10-22)

- **`<people>`** in the coordinator's `<chat_context>` (replaces `<since_your_last_reply>`): what each person did in
  the last 24 hours, with times, written by code (`memory.recent_activity`): their requests and receipts moving (with
  why, only what the member may see) and questions about a purchase asked and answered (only for the two of them and
  the PICs). It says when the AI last replied.
- The member's messages reach the AI with their time ("[07:22] …"); the AI's replies don't (so it doesn't copy them).
- Answers record when they were given (`report.answered_at`, **new column**: reset the demo database).
- `scripts/reliability.py` runs on a story clock (07:20 onwards, realistic gaps): back to back, every message shared
  one minute, so nothing showed Priya bought the gloves after the AI said she was getting them.
- **Coordinator v12.** 10 runs: **90/90**; "where gloves" 10/10, now 1 model call and 2.0 s (was 4 calls, ~7 s): the
  answer is in `<people>`, no lookup. v12 and the clock changed together, so which one fixed it isn't separated.

## Done since: the receipt reader (2026-10-22)

- **One model call, not an agent** (`app/ai/receipts.py`, prompt **receipt v1**): the receipt photo or PDF in, fixed
  fields out (`app/core/receipts.py` `Reading`: store, date, currency, total, lines, whether a line is the item,
  what it was unsure of). Photos are turned upright and sent as a JPEG of at most 1600 px (HEIC converted; Pillow and
  pillow-heif added); uploads up to 25 MB (a phone photo was 11.5 MB).
- **Claim page:** picking the receipt reads it (`POST /api/claims/read`) and fills in the store, date and total (the
  total only in the group's currency), shows what it read, and warns what a PIC will look at.
- **When it's handed in**, code compares the reading (kept by the file's hash, `receipt_read` **new table**) with what
  the member entered: a different total or date, another currency, nothing like the item, or unreadable → a PIC, with
  both values. Nothing is refused because of a reading. The reading is kept on the claim.
- Tried on a Swiss restaurant receipt (CHF 54.50, 2007, a euro conversion): read right in 6–12 s; handed in for trash
  bags, it reached Dana with three reasons. `scripts/try_receipt.py` reads any receipt from the command line.
- To do: the reader comments on the date order even when it's clear ("unambiguous… but…"): receipt v2. PDF and HEIC
  untested with the real model.

## Found in the Haiku demo runs (to fix)

- [x] The AI can't see answers to questions (now in the `purchase` record).
- [x] No way to add a reason to a request waiting for a PIC (`add_reason`; asking twice is refused).
- [x] The budget hedge ("I can't confirm these figures are up to date") came from the coordinator's brief: v6 asks only
      for what the member needs, and lookup can only return records.
- [ ] Haiku adds "I can't promise they'll approve it" to almost every reply; uses he/she, not names. (Prompt v8.)
- [ ] A list item that needs no reason still stores the member's whole sentence as its reason.
- [x] One clean run isn't proof: `scripts/reliability.py` (v8: 70/70).
- [x] The coordinator retold the member in its briefs ("they need it to show the flyers"), so act's quote check
      refused the reason and act retried (2–4 extra calls in 5 of 10 runs). Fixed in action v8 / coordinator v10.
- [x] lookup copied only the category the member asked about from the `budgets` record, and the check sent it back
      (11 times in 10 runs). `budgets` now takes an optional category: 1 rejection in the next 10 runs (another kind).
- [x] "I was wrong earlier" came back with coordinator v11 (1/10 in each of two runs): the test ran every message in
      the same minute and the chat had no times. Fixed with `<people>`, timed messages and the story clock (v12: 0/10).
- [ ] act sometimes writes its own reason on the table turn ("needed for the market stall" for "for the market
      stall"), and the quote check sends it back: 2 times in 10 runs.

## Next (in order)

1. [x] **PIC approvals queue** (Review page, `app/api/approvals.py`): requests waiting for a PIC (the rules' reasons, the
       member's reason, approve with the cap or say no with a note), extras paid over a limit, and flagged receipts.
2. [x] **Reliability script** (`scripts/reliability.py`).
3. [ ] **PayPal side of the demo:** top-up with PayPal Checkout, the ledger, and a demo "fast-forward" for the hold.
4. [x] Receipt reader (done before 3, 2026-10-22).
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
