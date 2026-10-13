# Group Fund: AI Coordination and PayPal Reimbursements for Volunteer Groups and Clubs

_Working name · Last updated: 2026-10-13 · PayPal AI Hackathon (Devpost), submissions close **Nov 12, 2026**_

> **Pivot (2026-10-12):** this replaces the earlier idea, an ordering assistant for small businesses. That spec is in
> git history (commit `45b88ae`); the old code was removed on the `office-fund` branch. Target narrowed from offices
> to volunteer groups and clubs on 2026-10-13.

## 1. Concept

**A collaborative app: tell the AI first, then go get it.** In a group, whether a volunteer group, a club or a team,
when you need something for the group *now*, you usually have to wait: for the one person allowed to buy, for someone
to reply "yes, go ahead", or for whoever said they'd get it. Or you buy it yourself and hope you're paid back. Two
people end up buying the same thing, or nobody does.

Here, you **tell the group's AI coordinator first** ("no gloves for this morning, I'm grabbing some"). In seconds:

1. **No double buying.** It checks whether someone's already on it or just bought it, and tells the group who's
   getting what.
2. **Fast.** Common things are confirmed on the spot, within the budget and rules the treasurer set. No waiting
   for anyone.
3. **Paid back, and confirmed.** You buy it, snap the receipt, the purchase is announced, the group can confirm
   it, and after a short waiting period with no problems, **PayPal pays you back**.

**Tagline:** "Need it now? Tell the group's AI, then go get it." **Pitch line:** "An AI coordinator for the group's
shared purchases. The treasurer sets the rules; the AI does the running around."

### The story (demo and video)
**Saturday, 8:40 am. The cleanup starts at 9.** Priya opens the supply box: no gloves.

- **Before:** she messages the coordinator, who doesn't answer. She can go without gloves, or buy them and hope to
  be paid back in a few weeks. Meanwhile Marco saw the same empty box and is already at another shop buying gloves.
  The treasurer later spends Sunday evening matching receipts to a spreadsheet.
- **With the app:** Priya tells the AI, *"No gloves for this morning, I'm grabbing some."*
  - *"Nobody's on it. Gloves are pre-approved up to $25: go ahead. I've told the group."*
  - Marco asks a minute later: *"Priya's getting gloves. Trash bags are low, though. Can you grab those?"*
  - Priya snaps the receipt; the feed shows "🧤 Priya got gloves"; people 👍 at the cleanup; three days later PayPal
    pays her back. The treasurer did nothing.
  - If something's off ("the gloves aren't in the supply box"), the payout is held and the treasurer gets a neutral
    summary. Nobody is accused, and the money is still safe.

**Ending line:** "Priya didn't wait for anyone, and nobody bought gloves twice."

Names, amounts and timings are illustrative. **To do:** replace or support with a real experience from the team, and
one or two real quotes from club treasurers or volunteers (no invented statistics).

### Why tell the AI, and not just the group chat?

| Group chat | The AI coordinator |
|---|---|
| Messages get buried: "I'm getting gloves" at 8:40 has scrolled away by 8:45 | Keeps track: nobody has to read everything; ask and it tells you who's on it |
| Saying it isn't permission: someone has to reply "go ahead", and that wait is the problem | Confirms on the spot for common items, within the treasurer's rules |
| Doesn't pay you back: the receipt photo still has to be matched up and paid by hand | Connects what you said you'd buy, the receipt and the payout |
| Forgets: "when did we last buy gloves?" means scrolling | Remembers, and spots "gloves again, after 2 days?" |
| Problems become public call-outs | Reports go privately to the treasurer |

*A group chat is a conversation; the AI is the group's memory.* It's not either/or: the bot can live in the group's
chat and post short updates there ("🧤 Priya is getting gloves"). The chat stays for chatting; the bot is the record.

**Where the chat wins:** tiny groups (3–5 people) who talk all the time. The app pays off once the chat gets noisy
(around 10+ people) or money and paying people back are involved.

### The AI is a coordinator, not an admin
It does the legwork an organizer would do, with none of the authority: it can't approve on its own judgement, pay
anyone, or change the rules.

| Job | Who does it |
|---|---|
| "Is anyone getting gloves?", telling everyone | AI |
| "Can I buy this?" | Code, following the treasurer's rules; the AI passes on the answer |
| Spotting double buys, "didn't we just buy that?" | AI notices, code checks the history |
| Collecting and reading receipts | AI reads, code compares with what was confirmed |
| Paying people back | PayPal, after the waiting period |
| Unusual purchases, problems, disputes | The treasurer, with the AI's summary |
| Budget, limits, what's pre-approved | The treasurer |

### Who it's for
**Test: do people end up waiting for someone else to buy small things they need right now?** Either because nobody's
sure who's buying, or because only one person is allowed to.

| Target | Why people wait |
|---|---|
| **Volunteer groups**: cleanups, community fridges, mutual aid (**main demo**) | Nobody's sure who's buying, or whether they'll be paid back |
| **Clubs and societies**: student societies, sports teams, hobby clubs, parent groups | The treasurer only reimburses; members front money for events |
| **Teams in bigger organizations**, small offices | Only facilities or a manager can buy; requests take days |
| Not for: a group where one person really does buy everything, quickly | Nobody waits |

Also not for org-wide procurement across departments (an enterprise procurement problem).

### What makes it different: coordination before anyone buys
Expense and reimbursement tools handle the money **after** someone bought something. This coordinates **before**:
what's needed, who's on it, what was just bought, and whether you're allowed, in seconds.

### Similar products (checked 2026-10-13)

| Product | What it does | Difference |
|---|---|---|
| [Open Collective](https://docs.opencollective.com/help/expenses-and-getting-paid/submitting-expenses) | Transparent budgets for groups (incl. mutual aid); members submit an expense with receipts **after** buying, a group admin approves, the fiscal host reviews, payout by PayPal or bank (some weekly) | The closest product. After-the-fact and admin-reviewed: no telling first, no "already bought", no auto-confirmation, no chat. |
| [HCB](https://hackclub.com/hcb) (Hack Club) | Fiscal sponsorship for clubs, hackathons and charities (US/Canada, 7% of income): member debit cards, reimbursements (incl. PayPal), donations; a [Discord bot](https://help.hcb.hackclub.com/en/articles/15484060-how-do-i-use-the-hcb-discord-bot) for spending alerts, balance and `/reimburse` | Cards remove fronting money, but need fiscal sponsorship and go to a few people; no coordination of who's buying what, no AI |
| [ClubTreasury](https://apps.apple.com/app/id6642640430) | iPhone app for club finances: receipt scanning, treasurer roles | Bookkeeping only |
| [BeeKeeper](https://support.futurefund.com/beekeeper/expense-management/expense-reports) | Volunteer-organization platform with expense reports, treasurer review | After-the-fact |
| Student union tools (e.g. Durham SU's Konnekt365) | Expense form + receipt → exec approval chain | Multi-step, slow |
| DIY setups (e.g. [Chicago mutual aid](https://ajplus.net/stories/mtual-aid-extremely-online): hotline, Trello, Airtable, Slack, Venmo) | Volunteers post reimbursement requests, paid back by Venmo | Shows the need: groups glue tools together |
| Discord sign-up bots | "Claim a slot" lists that close when full | The claiming pattern, but for shifts, not purchases or money |
| LangChain Restock (Slack demo) | An agent buys office supplies online; a person approves each; paid from the requester's own Stripe Link wallet | No group budget or reimbursement; the agent buys, not members |
| Splitwise / I Hate Money | Who owes whom among friends | No budget, confirmation or coordination |

No AI coordinator for group purchases found, and no PayPal AI Hackathon 2026 entry for groups, clubs or volunteers
(public repos only; Devpost's gallery isn't visible before judging).

### Scope: shared things the group can see
**In:** things bought **for the group** that **other members can see**, under a small cap.

| Category | Examples | Default |
|---|---|---|
| Supplies | gloves, trash bags, tape, markers | common items, auto-approve within cap |
| Food and drink | snacks and water for volunteers, coffee for the club room | common items, auto-approve within cap |
| Printing | flyers, posters, sign-up sheets | common items, auto-approve within cap |
| Cleaning and first aid | wipes, soap, plasters | common items, auto-approve within cap |
| Equipment | a folding table, a speaker, sports balls | uncommon: always a person approves |

**Funding:** the group's own funds (dues, grants, donations), added by the treasurer through PayPal Checkout.
Optional later: a public "chip in" link where donors add to the budget (unrestricted, not earmarked per item) and see
the feed of what it bought.

**Out:** travel, mileage and other personal expenses (members can't see them, so the feed and "missing" reports in §6
can't check them); donations earmarked to specific items (a different product, see the "one buyer?" test); anything
over the **hard ceiling** (default $100), which goes through the group's normal process.

### Core principle: AI never moves money
- The AI **understands and drafts**: matches free text to a list item, reads receipts, answers questions
  ("how much is left this month?").
- **Code decides**: caps, limits, budgets, duplicates, receipt checks against the approval.
- **People approve** anything unusual. Money only leaves after the waiting period.
- When the AI is unsure, the request goes to a person. A wrong AI answer costs a human a click, never money.

## 2. Judging criteria (equal weight)

| Criterion | Our answer |
|---|---|
| Tech: real, non-trivial PayPal + AI | AI matches requests and reads receipts; code enforces the group's settings. PayPal Payouts reimburses members after a waiting period that's held in the app, with cancellation of unclaimed payouts; Checkout tops up the budget. |
| Design: complete product | Treasurer setup → member request → purchase → feed → payout, plus the "something's wrong" path |
| Impact | Volunteer groups and clubs get safe self-service spending without anyone fronting money for weeks, and can show donors every receipt |
| Innovation | Chat-first requests; the group itself verifies purchases through the feed; trust built from history |
| Presentation | Coffee runs out → approved in seconds → feed post → payout; then a "coffee missing" report holds a payout |

## 3. Roles

- **Admin** (the treasurer or an organizer): sets the budget, the common-items list and the settings; approves unusual
  requests; handles alerts.
- **Member**: requests, buys, claims, confirms or reports in the feed.
- **Approver**: a member trusted to approve unusual requests (e.g. the chair); the admin is one too.
- One group per deployment for the prototype.

## 4. Workflow (reference, agreed 2026-10-13)

```
1. Message     "We're out of gloves, I'm grabbing some"                       (chat)
2. AI checks   which list item is it? is anyone already on it? (code answers both)
3a. On the list, nobody on it, within limits
               → it's yours, up to the item's budget (e.g. $25)
               → AI asks "when will you get it?" → that's your deadline (within the group's maximum)
               → the group sees "Priya is getting gloves (by 1 pm)"
3b. Already taken
               → "Marco is getting them (by 1 pm)" + something else that's needed
3c. Not on the list, or over a limit (item budget, weekly/monthly limit, bought too soon, budgets)
               → AI says why and asks for a justification → posted to the group → the PIC approves or rejects
4. Buy it      with your own money (reimbursement only, never paid in advance)
               can't make it? only you can release it → back to needed; past your deadline it's released anyway
5. Claim       receipt required (+ a photo of the item where it's kept, if the item asks for one)
               → checks (§4.2) → announced in the group: "Priya got gloves, $18.40"
6. 3-day hold  "I can't find the gloves" → AI says who got them (when, where, photo) and can ask them; no hold
               still not sorted → formal report (what's wrong + what you checked) → hold extended, treasurer told
7. Paid back   PayPal Payout after the hold, up to the item's budget
               over budget, with a justification → the extra is paid later, once the PIC approves it
```

### 4.1 Decisions

| Question | Decision |
|---|---|
| Who approves items not on the list? | The **PIC** (person in charge: the `approver` role, e.g. the chair; admins too). The group sees what's waiting. Nobody approves their own request. |
| Over the item's budget? | The claim is paid up to the budget after the hold; **the extra waits** for the PIC, with the member's justification. |
| Something seems wrong during the hold | **Two steps.** 1: the AI says **who was in charge** (who got it, when, the store, the photo) and can ask them; their answer goes back to whoever asked. No hold, nothing to the treasurer. 2: if it isn't sorted, a **formal report** saying what's wrong **and what was checked** (required): the **hold is extended** (default +4 days) and it goes **privately** to the treasurer (never a public call-out). The treasurer can pay or reject at any time. If nobody decided by the end of the extension, it's paid, and the report stays on record. *(End-of-extension rule proposed; to confirm.)* |
| A problem found after payout | Can't be pulled back (PayPal). **Recorded** against the member; the treasurer can lower their limits or make their purchases need approval. |
| Releasing a claim | **Only the member who claimed it** can release it, so nobody takes someone else's job. Past their deadline it's released automatically. |
| How long to buy it | The **member says** ("by 1 pm", "tomorrow"); the AI asks. Capped by a group maximum (default 72 h); a default (4 h) if they don't say. |
| Limits over time | A per-member spending limit **per week or per month** (setting). Going over needs a justification and the PIC. Plus "bought too soon" per item. |
| No PayPal email yet | Checked before the first claim is accepted: the AI asks for it. |
| The group's money runs low | Claims wait for a top-up (PayPal Checkout by the treasurer) rather than fail. |

### 4.2 Claims and receipts (step 4 of the build)

**Submitting:** the member picks the request (the AI does, if they only have one open), uploads the receipt photo
(required) and, for items that ask for it, a photo of the item where it's kept. Until the receipt reader exists
(week 3), the member types the store, date and total; afterwards the AI pre-fills them.

**Checks, by code, in order:**
1. **Linked:** the request is theirs and confirmed (on it). A receipt for a request that expired, or that someone
   else has since claimed, goes to the PIC instead.
2. **Dated right:** the receipt date is on or after the confirmation, and not in the future.
3. **Not a duplicate:** the same image (hash) → rejected; the same store + date + total as another accepted claim →
   to the PIC.
4. **Within budget:** total ≤ the item's budget → normal. Over it → the budget part continues; the extra needs a
   justification and waits for the PIC. Never above the hard ceiling.
5. **The right things** (once the receipt reader runs): only lines matching the item count, so personal items on
   the same receipt are left out; lines that don't match → to the PIC with the reading attached.
6. **Payable:** the member has a PayPal email.

**Accepted:** the request becomes `claimed`; the claim is `waiting` with its payout due in 3 days; the group sees
"Priya got gloves, $18.40" (amounts shown if the group allows); 👍 and "report a problem" sit under it.

**Questioned** (step 1): the buyer is asked where it is; the claim stays `waiting`, the hold unchanged.
*Open question:* if a question is still unanswered when the hold ends, should the payout wait (proposed: up to
24 h)? Not built yet.

**Reported** (step 2, formal, with what was checked): the claim is `held`, the hold is extended, the treasurer is
told privately, the buyer is told neutrally. Outcomes: pay, or reject (on the member's record).

### Request and claim states
- Request: `on_it` | `needs_approval` → (`on_it` | `rejected`) | `declined`; `on_it` → `claimed` | `released` |
  `expired`.
- Claim: `waiting` → `paid`; `waiting` → `held` → `paid` | `rejected`; an over-budget extra is `pending` →
  `approved` (paid later) | `rejected`.

## 5. Settings (closed set, enforced by code)

| Setting | Default |
|---|---|
| Monthly budget, and per category | $300 · supplies $120, food and drink $100, printing $50, cleaning and first aid $30 |
| Hard ceiling per purchase | $100 (above it: the group's normal process, see §1 Scope) |
| Common items: name, budget per purchase, usual restock interval, proof needed | Gloves · $25 · 14 days · receipt + photo |
| Spending limit per member, and its period | $100 per month (or per week) |
| Bought too soon | within 50% of the item's restock interval → the PIC decides |
| Hold before payout | 3 days (shorter for trusted members later, see §6) |
| A report extends the hold by | 4 days |
| Time to buy once confirmed | the member says; default 4 h, at most 72 h |
| PICs (approvers) | the members with the approver or admin role |
| Feed shows amounts | yes / no |
| Feed style | every purchase / daily summary |

- Defaults come pre-filled (a starter list of common items) so setup takes minutes.
- **The list grows itself:** when the PIC OKs an item not on the list, the bot offers "Add **Oat milk** to the
  common list (budget $8, weekly)?"
- Restock intervals start as set by the treasurer; later the app can suggest changes from history ("gloves usually
  last 10 days, not 14").

## 6. Fraud and mistakes

Receipts can't be proven real, so the design aims to make cheating not worth it and to catch it while the money is
still in the group's account.

| Risk | Guard (code, not AI) |
|---|---|
| Claim without asking first | Every claim must link to an approved request |
| Spend more than approved | Pay at most the cap; anything over goes to a person |
| Reuse a receipt / claim twice | Same image hash, or same store + date + total → rejected |
| Receipt older than the approval | Receipt date must be on or after the approval |
| Split purchases to stay under the cap | Monthly per-person limit; several claims in a day → review |
| Personal items on the receipt | Receipt items vs approved item; mismatch → person |
| Item never bought | Feed + waiting period: "Report a problem" or a "too soon" re-request holds the payout |
| Text in the receipt image that tells the AI to approve | AI only extracts fields; code makes every decision |
| Fake or edited receipt | Item photo, the feed, random spot checks (~1 in 10), record of who claimed what |

**Alerts aren't accusations.** Coffee can run out fast, or someone took it home. Reports go **privately to the
admin**, never into the feed. The bot asks both sides neutrally and the admin decides. Nobody is blocked
automatically.

**Trust:** members with a clean history can get a shorter waiting period, or be paid immediately for small amounts.

**"Too soon" check:** the same item requested well before its usual restock interval → no auto-approval; the bot
mentions the recent purchase ("Sam restocked coffee Monday: is it in the club room?").

## 7. PayPal

| Need | API | Notes |
|---|---|---|
| Reimburse members | **Payouts** (to the member's email) | Sent only after the waiting period. A payout can only be **cancelled while unclaimed**; if the recipient already has a PayPal account it normally lands immediately and can't be pulled back. So the "hold" lives in the app: money isn't sent until the window passes. |
| Top up the budget | **Orders (Checkout)** | Demo: the admin adds funds. Already used in this codebase. |
| Audit | Our ledger + PayPal ids | Every payout, top-up and cancellation recorded |

- **Prototype setup:** the group's PayPal business account is the account behind the app's REST credentials;
  payouts come from its balance; the app's ledger tracks the budget.
- **Real-world question (later):** a multi-group service would need each group to connect its own PayPal account
  (partner onboarding) rather than the platform holding the groups' money.
- **Payouts in live** needs PayPal to enable it on the account; sandbox is enough for the hackathon.
- [x] **Tested 2026-10-12 (`scripts/paypal/payouts.py`):** the sandbox app already has the Payouts scope.
  - $24.50 to a sandbox personal account: `PENDING → PROCESSING → SUCCESS` in **~28 s**. Fee **$0.53**, charged
    to the sender (batch `fees`), not deducted from the member.
  - $5.00 to an email with no PayPal account: the **batch says SUCCESS but the item is UNCLAIMED** (fee $0.11).
    Cancelled → item `RETURNED`. So **always check the item's status**, never just the batch's.
  - Cancelling the successful payout: refused, `ITEM_INCORRECT_STATUS: Only items in Unclaimed status can be
    cancelled.` Confirms the waiting period must live in our app.
  - Resending the same `sender_batch_id`: rejected (`Batch with given sender_batch_id already exists`, with a link
    to the original batch). So use the claim's id as `sender_batch_id`: a retry can never pay twice, and the error
    points to the batch that already went out.
  - Balance API (`/v1/reporting/balances`): 403, the app lacks the reporting scope. Not needed: our ledger tracks the
    budget. Unknown: whether the fee on a cancelled unclaimed payout is refunded.

## 8. AI agents

**Decided 2026-10-13:** one **coordinator** that talks to the member, and two **subagents** it asks: one looks
things up, one acts. They answer the coordinator (they're tools to it, not handovers), so the coordinator keeps the
conversation and is the only one that replies. The **receipt reader** is a separate pipeline: code calls it on an
upload, and it never chats. Every agent inherits `BaseAgent` (`app/ai/base.py`, on LangChain's `create_agent`):
versioned prompt, model, `register_tool`, `run` and `stream`; `as_tool()` makes it a subagent. None of them approves, pays, or writes to the feed: code does, through the core functions.

```
Member chat ─▶ Coordinator ──ask──▶ Lookup agent (read tools) ──▶ facts ──┐
                    ▲  │                                                  │
                    │  └──ask──▶ Action agent (action tools) ──▶ what code did, or what's missing
                    └─────────────────────────────────────────────────────┘ → the coordinator replies

Receipt upload ─▶ Receipt reader ─▶ ReceiptReading ─▶ code: the checks (§4.2) ─▶ hold starts, "got it" post

Treasurer setup ─▶ Setup assistant ─▶ draft items ─▶ the treasurer reviews and saves
```

| Agent | Triggered by | Tools | Returns |
|---|---|---|---|
| Coordinator (to build) | Every chat message | `lookup(request)`, `act(request)` | The reply to the member |
| Lookup agent (`app/ai/agents/lookup.py`) | The coordinator | Read tools (below) | `LookupResult`: found / not_found + the facts, with ids |
| Action agent (`app/ai/agents/action.py`) | The coordinator | Action tools (below) | `ActionResult`: done / needs_info / refused + message + what's missing |
| Receipt reader | A receipt upload (code) | None | `ReceiptReading` |
| Setup assistant | The treasurer pastes a list | None | Draft common items |

Every tool runs as **the member who's chatting** (the run's context), so the AI can't act for anyone else, and
every rule and permission is checked by code.

### 8.1 Coordinator
- **Does:** follows the workflow (§4) in conversation: works out what the member wants, asks the lookup agent,
  asks the member what's missing ("by when?", "why do you need it?"), then asks the action agent to do it.
- **Rules:** only states facts it got from the subagents; unknown → "ask the treasurer". Never promises approval
  or payment.
- **Context:** the last 20 messages of the conversation (§14.1); facts are always looked up again.

### 8.2 Lookup agent (read tools, `app/core/memory.py`)

| Tool | Answers |
|---|---|
| `find_items(query)` | Which list item it is: id, name, budget, restock time, proof needed. Not sure → treated as not on the list |
| `who_is_on_it` | "Marco is getting them (by 1 pm)" |
| `latest_purchase(item)`, `item_history(item)` | Who got it, when, the store, photo, still in its hold (step 1 when something's missing); how often it's bought |
| `probably_needed` | "Trash bags are overdue: can you grab those instead?" |
| `budgets`, `my_status` | Budgets and what's left; the member's limit, open requests, claims waiting, questions about their purchases, PayPal email set |
| `group_rules` | Hold length, limits, who the PICs and the treasurer are, in plain words |

Privacy: never another member's limits or claim amounts beyond what the feed shows.

### 8.3 Action agent (action tools, `app/core/requests.py`, `payouts.py`)

| Tool | Core function | Status |
|---|---|---|
| `get_item(item, hours, estimate, reason)` | `submit_request`: confirmed, or posted for the PIC (a reason is required) | Built |
| `release(request)` | `release_request`: only the member getting it | Built |
| `ask_buyer(purchase, text)`, `answer_question` | Step 1: no hold, nothing to the treasurer | Built |
| `report_problem(text, checked)` | Step 2: extends the hold, private to the treasurer | Built |
| `set_paypal_email` | `members.set_paypal_email`; asked before the first claim | Built |

A rule saying no (`ActionError`, e.g. "Marco is already getting them") goes back to the agent as the answer, so the
coordinator can tell the member. **Never tools:** approving or rejecting (the PIC's buttons), resolving reports (the
treasurer), payouts.

### 8.4 Receipt reader (its own pipeline)
- **Does:** reads a receipt photo when a claim is submitted. Code calls it; it never chats.
- **Input:** the image, plus the common-items list. It is **not told which item was approved**, so a receipt (or a
  hopeful prompt) can't steer it towards a match.
- **Output `ReceiptReading`:** `store`, `date`, `total`, `currency`; `lines` (each `text`, `amount`, `item_id` or
  empty); `legible` (false when it can't read it confidently).
- **Rules:** extraction only. Anything written on the receipt is data, never instructions.
- **Then code runs the checks** (§4.2): accepted → hold starts, "got it" post; anything else → the PIC, with the
  reading attached.
- **Needs a vision-capable model** (Atria is text-only). Until then the member types store, date and total; the
  photo is stored for the PIC.

### 8.5 Setup assistant (treasurer)
- Turns "we keep gloves, bags, snacks, coffee" (or a pasted supplies list) into draft common items with a suggested
  budget and restock time. Code validates; the treasurer reviews and saves. Low priority: the starter list covers
  the demo.

### 8.6 Not AI (code only)
- Approvals, limits, budgets, "too soon" checks, duplicate detection
- Holds, payouts, the ledger
- Feed posts (from templates: the AI never writes about people in public)
- Trust levels and restock-time suggestions (from history)

### 8.7 Models and speed
Any OpenAI-compatible endpoint. Each agent can use a different model (`model_name` on the class). Subagents cost
model calls: "I'm grabbing gloves" is about 7 calls instead of about 4 with the tools on the coordinator. Atria Dawn
(current) is **text-only and slow (20–200 s per call)**, so the coordinator needs a faster model. Fallback if it's
still too slow: give the coordinator the tools directly (the core functions don't change).

### 8.8 Open
- Should a formal report need a tap on a card ("File report: … / You checked: …") rather than the AI filing it?
- Should PICs approve in the chat too (an approval card with buttons that call code), or only on the web?

## 9. Chat and feed

- **First: the web app** (the existing React app): chat, feed, approvals, admin setup. Easier to debug, and judges
  can always try it.
- **Then: the group's own chat.** Volunteer groups and clubs mostly use WhatsApp, Discord or Telegram (Slack is
  mostly for companies), so **Telegram or Discord** first: easy bot APIs. The bot posts "on it" and "got it"
  updates, takes requests by DM or mention, shows approval buttons to approvers, takes receipts dropped in the chat,
  and the chat user id says who's talking. WhatsApp later (its business API is harder). Admin setup, the ledger and
  PayPal top-ups stay on the web.
- Adding a chat platform should take days, not weeks, because the core never depends on the web (§14).

## 10. From the current codebase

| Keep | Change or drop |
|---|---|
| LangChain + LangGraph for the AI (`BaseAgent` rewritten small on `create_agent`, 2026-10-13) | Front desk / order builder → coordinator + lookup and action subagents |
| `app/ai/onboarding.py`: extractor pattern | Product extractor → setup assistant; add the receipt reader |
| `app/ai/prompts.py`: versioned prompts | Order-builder prompts and shop tools |
| Closed-set settings pattern (`app/domain/rules.py`: normalize, describe, defaults) | Rule kinds → the settings in §5 |
| Products + CSV import + review table | → common-items list |
| `app/payments/paypal.py`: PayPal client, Orders | Add Payouts; holds / deposits / payment plans no longer needed |
| `Payment` audit table | → ledger (top-ups, payouts, cancellations) |
| React + HeroUI setup screens | Storefront → chat + feed + admin approvals |

## 11. Demo plan

1. The treasurer sets up a volunteer group (weekly cleanups + a community fridge): budget, starter common items
   (edit the gloves cap), tops up the budget with PayPal.
2. Member: "we're out of gloves for Saturday" → auto-approved → submits receipt → feed post → (fast-forward window) → payout
   lands in the sandbox account.
3. Uncommon item: "can we get a folding table for the stall?" → approver taps Approve → "add to common list?"
4. Problem path: someone reports "the gloves aren't in the supply box" → pending payout held → the treasurer sees
   the alert with both answers.

## 12. Out of scope

Mileage, travel and other personal expenses; purchases over the hard ceiling; corporate cards; paying in advance;
accounting / tax exports; multi-currency; inventory sensors; real money.

## 13. Open decisions

1. Product name
2. Vision-capable model for receipts (and the provider for the final demo, given Atria's speed)
3. How the feed is confirmed: 👍 reactions count as confirmation, or only reports matter?
4. Team size and skills
5. **Decided:** a coordinator with lookup and action subagents (§8); the receipt reader is its own pipeline.
6. **Decided:** web sign-in is a **"View as" switcher** built for testing, no passwords or PIN: a dropdown in the
   header lists the demo members (members, an approver, the admin); `?as=sam` in the URL opens the app as Sam, so
   two tabs can be two people; the browser sends the member id with each API call, and API tests set it directly.
   Demo-only (anyone can be anyone); the chat platform later gives real identity through its user id.

## 14. Architecture: a core that doesn't know about the web

Everything that decides or moves money lives in `app/core/`, as plain functions over the database. The web API
(and later Telegram / Discord) are thin layers that call them. Rules:

1. **Members, not sessions.** Core functions take a `Member`; members can carry external ids (`chat_user_id`).
2. **Replies are data.** The core returns text plus typed cards (`{"type": "approved", "item": ..., "cap": ...}`);
   React renders them now, chat messages with buttons later.
3. **Every action is a function by id:** `approve(request_id, by=member)`, `report(post_id, by=member, text)`. A
   web button and a chat button call the same thing.
4. **Events, not pages.** The core records notifications ("tell Sam his claim was paid") and feed posts; the web
   shows them in lists, the chat bot will send DMs and group posts.
5. **Files are bytes.** Receipts come in as bytes + filename, from a browser upload or a chat file download.
6. **Slow work runs in the background.** AI calls and payouts never block a request (chat platforms expect a quick answer).
7. **Conversations have ids.** A web chat tab and a chat DM or thread are both just a `conversation_id`.

```
app/
  core/          settings, decisions, claims, payouts, feed, notifications (later: conversation)
  ai/            BaseAgent (base.py) + the agents
  payments/      PayPal client: Orders (top-ups) + Payouts
  api/           FastAPI routes for the web app: thin, call app/core
  models.py      the tables below
frontend/        React: member pages + admin pages
```

### 14.1 Database

SQLite through SQLModel (`app/db.py`), one file at `data/app.db`, created and seeded by
`scripts/seed_demo.py --reset`. Times are aware UTC; money is integer cents.

**The memory system** (`app/core/memory.py`): the AI is the group's memory, but it never remembers facts itself.

| Layer | What | Where |
|---|---|---|
| Facts | Who's on what, what was bought when, budgets and what's left | The tables, looked up fresh on every turn through tools |
| Patterns | How long an item really lasts (average gap between purchases), what's probably run out (bought before, past its restock time, nobody on it) | Computed from the facts, never stored as AI opinions |
| Conversations | What was said, per member and channel; the last 20 messages are the context for the next reply | Conversation + Message (our own tables: work the same on web, Telegram and Discord) |
| Agent log | Every tool call, result and decision, with the prompt version | AgentLog |

Old tool results are never replayed from the conversation: if the AI needs a fact again, it looks it up again.

```
Group ───┬─ Member ──────────────┐
         ├─ Category ─ Item       │
         └─ Setting               │
                                  ▼
Request (member, item?) ─▶ Claim (receipt) ─▶ Payout (PayPal ids)
                              │
                              ├─ FeedPost ─ Reaction, Report
                              └─ LedgerEntry, Notification
```

| Table | Key fields | Answers |
|---|---|---|
| **Group** | name, currency, monthly budget | The budget everything draws from |
| **Member** | name, email, PayPal email, role (member / approver = PIC / admin), chat user id (later), problems on record, own limit (if the treasurer lowered it) | Who's who; where payouts go; trust |
| **Category** | name, monthly budget | "Pantry: $112 of $250 left" |
| **Item** | name, other names ("Nescafé", "coffee pods"), category, cap, usual restock days, proof needed, active | The common-items list |
| **Setting** | kind, params (closed set, §5) | Limits, waiting period, ceiling, feed options |
| **Request** | member, item (empty = uncommon), description, quantity, estimate, reason / justification, status (incl. **on_it**, released, expired), decision reasons, decided by + when, approved cap, deadline (the member's "by 1 pm") | Who asked for what, why it was confirmed, and **who is getting what right now** (no double buying) |
| **Claim** | request, member, receipt file + fingerprint (hash), store, receipt date, total, the AI's reading, item photo, status, payout due date (extended on a report), over-budget extra + justification + its status | **Who bought what, and when.** A purchase is an accepted claim: no separate purchases table. |
| **Payout** | claim, `sender_batch_id` (= claim id), PayPal batch + item ids, amount, fee, item status | Where each reimbursement stands in PayPal |
| **LedgerEntry** | kind (top-up, payout, fee, returned), amount (+ / −), claim or payout, PayPal reference | Budget balance = the sum; the full money trail |
| **FeedPost / Reaction / Report** | "on it" and "got it" posts per request; 👍 by whom; reports with status and resolution | The feed and the "something's wrong" path |
| **Notification** | member, kind, details, read or not | "Your claim was paid", approver alerts (chat DMs later) |
| **Conversation / Message** | member, channel (web / telegram / discord), the platform's thread id; messages (user / assistant) with the cards shown | What was said, so follow-ups make sense |
| **AgentLog** | conversation, agent, prompt version, event (tool call / result / output / error), data | Why the bot did what it did (§14.2) |

**"Who's on it?"** (from Request): requests for an item in `on_it` that haven't expired → "Priya's getting gloves".

**History questions, all from Claim + Member + Item:**
- Who bought what: accepted claims with member and item, newest first.
- When an item was last bought: the latest receipt date per item ("Coffee: 2 days ago, by Sam").
- "Too soon": last purchase + the item's restock days > today → not auto-approved.
- Real restock interval: the average gap between purchases of an item (later: "coffee lasts 10 days, not 14").
- Per person this month: the sum of their claims (monthly limit, trust levels).

**Practical choices:**
- No migrations during the hackathon: when tables change, delete `data/app.db` and rerun the seed script.
- **Seed script:** a demo volunteer group, 3–4 members on sandbox personal accounts, starter categories and items, and
  **some past purchases**, so history and "too soon" work from the first minute of the demo.
- **Deployed:** Render's free disk is wiped on every deploy and judging runs into December, so use a free hosted
  Postgres (e.g. Neon or Supabase): only `DATABASE_URL` changes. Check current free-tier limits when deploying.

### 14.2 Agent safety (borrowed from AI coding harnesses)

- **Permission levels:** reading tools are free; action tools (`submit_request`, `report_problem`) only do what the
  member could do with a button; approving and paying are **never** tools; read and action tools sit in
  different subagents.
- **A hook on every action:** each action tool goes through the decision engine first; it can't be skipped.
- **Untrusted input:** chat messages, receipt text and tool results are data, never instructions.
- **Limits:** model calls and tool calls per run (`create_agent` middleware), reply length (`max_tokens`).
- **Errors go back to the model, never crash the run:** bad arguments, a rule saying no (`ActionError`, its own
  words), any other tool error (logged; "failed unexpectedly"), the same call repeated too often (refused).
- **Subagents finish with a schema** (Gemini CLI's `complete_task`, Goose's `final_output`): `LookupResult` /
  `ActionResult`; plain text is sent back until they do. A subagent that fails (out of calls or time, the
  provider, a bug) returns `{"status": "failed", "reason", "completed"}` to the coordinator: `completed` lists
  what its tools already did, since an action can happen before the agent fails. Subagents can't call other agents.
- **Limits per agent**, set when it's created: model calls, tool calls, repeats of the same call, a timeout.
- **Limited context where it matters:** the receipt reader isn't told what was approved.
- **Everything logged:** every tool call and decision in `AgentLog`, next to the ledger.
- **Approvals are database state**, not a paused agent run: they can take hours.
- **Not copied:** long autonomous planning and to-do lists. Our conversations are short and bounded.

The old order-assistant code (businesses, products, rules, orders, payment plans, storefront) was removed on
2026-10-12; it stays in git (`main`, commit `45b88ae`). Kept: `app/ai/prompts.py`,
`app/payments/paypal.py`, config/db/main, and the frontend shell and UI components.

## 15. Build plan: web app first

Today is Oct 12; submissions close Nov 12. Code first, then AI, then the chat bot: the money flow must work without AI.

**Week 1 (Oct 13–18): the core, with tests (no UI)**
1. Remove the order-assistant code; new tables; default settings and a starter common-items list.
2. Decision engine: request → `auto_approved` / `needs_approval` / `declined`, with reasons (hard ceiling, item
   cap, monthly limit, category budget, "too soon").
3. Payouts: PayPal client methods (create, get batch, get item, cancel), the claim id as `sender_batch_id`, item
   status (not batch status), ledger entries. Tested with httpx's MockTransport, then once in the sandbox.
4. Claims (§4.2): linked to the member's confirmed request, dated on or after it, not a duplicate (image hash;
   store + date + total), within the item's budget (the extra waits for the PIC with a justification),
   the member has a PayPal email.
5. Waiting period: payouts become due after N days; a background loop sends due payouts; a report holds a pending
   payout; notifications are recorded.

*Done when:* tests cover every rule in §6, and a script runs request → claim → payout against the sandbox.

*Also from the agreed workflow (§4), in steps 2 and 5:* release (by the claimer only); the member's own deadline
(capped); a justification required whenever the PIC decides; a weekly or monthly member limit; a report extends the
hold; problems on record; the PIC label for approvers.

*Progress (2026-10-13):* steps 1–2 done: tables (`app/models.py`), settings, the decision engine and request actions
(`app/core/`), the starter list, `scripts/seed_demo.py` with past purchases. Also the memory system
(`app/core/memory.py`, conversation and agent-log tables).
Steps 3 and 5 done (`app/core/payouts.py`): the ledger and top-ups; the 3-day hold; reports (private to the treasurer,
hold extended, resolved as no problem / problem on record); over-budget extras approved by a PIC and paid separately;
`run_due` pays what's due (claim id in `sender_batch_id`, item status not batch status), retries payouts that never
reached PayPal, follows up PENDING/UNCLAIMED, returns failed payouts to the balance; cancelling unclaimed payouts to pay
again. 37 core tests against a fake PayPal, and `scripts/paypal/core_payout_check.py` passed against the sandbox
($18.40 paid, $0.40 fee, nothing paid during the hold or twice). Not wired to a background loop yet (with the web app).
Next: claims (step 4) and the request changes from §4.

**Week 2 (Oct 19–25): the web app**
- **Sign in:** the "View as" switcher and `?as=<member>` links (§13, decision 6).
- **Member pages:** home (budget left, my requests and claims, notifications), new request (a form, replaced by
  chat in week 3), claim (receipt photo + store/date/total), feed (👍, report a problem).
- **Admin pages:**
  - setup: group, members, common items, settings, reusing the review-table pattern;
  - queue: approvals and alerts;
  - ledger: top-ups, payouts, pending ones, and a "fast-forward" button for the demo;
  - top-up with PayPal Checkout.
- **Demo seed script:** a volunteer group, 3 members on sandbox personal accounts, a starter item list.

*Done when:* the whole §11 demo works through the web forms, in the sandbox.

**Week 3 (Oct 26–Nov 1): AI**
- **Group desk agent:**
  - reading tools: budget, my status, common items, recent purchases, settings;
  - action tools: `submit_request` and `report_problem`, which call the week-1 core;
  - decision cards built by code.
- **Receipt reader:** pre-fills the claim form; it isn't told what was approved, and code compares.
- **Setup assistant:** pasted list → draft common items.
- **Chat page** with streaming. The forms stay as a fallback.

*Done when:* the demo works by chatting.

**Week 4 (Nov 2–8): chat bot, reliability, deploy**
- Chat bot layer (2–4 days): **Telegram or Discord** (where volunteer groups and clubs already are); requests
  by DM or mention, approval buttons, "on it" / "got it" posts in the group, reactions, receipt photos.
- Live reliability script: about 15 realistic requests and receipts; how often the AI is wrong, and whether code
  catches it every time.
- Check-in follow-up (neutral questions to both sides, summary for the admin).
- Deploy to Render.

**Nov 9–12: submission.** Demo video, Devpost write-up, README; at least 2 days spare.

**Cut first if behind:** trust levels, restock suggestions, "add to common list?", random spot checks, daily
summary, setup assistant, then the chat bot (the web app is the fallback). **Never cut:** the decision engine, the
waiting period with holds, Payouts, the feed with its report button.

**Needed from us:** 2–3 more sandbox personal accounts (members); a fast, vision-capable model with an API key
(week 3); a Telegram or Discord bot and a test group (week 4).
