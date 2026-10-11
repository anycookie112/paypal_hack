# Order Assistant: AI Ordering for Small Businesses

_Working name · Last updated: 2026-10-11 · PayPal AI Hackathon (Devpost), submissions close **Nov 12, 2026**_

## 1. Concept

**Custom orders for small businesses, without the DMs.** Orders whose details and final price aren't known up front ("cake for a 20-kid party, one nut allergy, about $120", "catering for 40 next Friday", "BBQ meat for 15") are handled today by phone, Instagram DMs and WhatsApp: back-and-forth, hand-written quotes, chasing deposits, adjusting the price at the end. Bakeries, caterers, florists, butchers.

The owner adds **products** and picks **settings** once. A **customer** describes what they need and their budget; the AI turns it into a concrete order from that shop's products only; **code** checks it against the settings and the budget; **PayPal** handles the money the way these orders work (deposit + balance, or hold → exact charge).

> Example (bakery): "Kid's party Saturday, 20 kids, $120, one nut allergy" → 24 nut-free cupcakes + a 6-inch custom cake = $114, ready Saturday 10am. Custom cakes take a 50% deposit (the owner's setting).

**Focus:** the customer's experience, while saving the owner time. Not a delivery app: simple fixed-price checkout is a side option.

**Two front doors, one core:** customers order on our web page first; later, an **MCP server** over the same core lets a customer's own AI assistant order from the shop. Every order, from either door, goes through the same code checks.

### Reliability principle: nothing for the owner to tune
Owners who don't know how AI behaves can't be expected to write prompts. So by default the owner writes **no free text for the AI**:
- The assistant works only from **products** (name, description, tags, price) and **fixed settings** chosen from a closed set, pre-filled with defaults for the business type.
- The AI proposes a **structured** order (items, quantities, date). Code checks every item exists, computes all prices and totals itself, and enforces budget and settings. A wrong AI answer can't reach PayPal.
- The customer sees and edits a **cart**, not a chat promise, and approves the actual amount in PayPal.
- Questions not answerable from product data get "not sure, contact the shop", never a guess.
- Plain-English policies and notes for the assistant exist only under **Advanced**, for owners comfortable with AI.

## 2. Judging criteria (equal weight)

| Criterion | Our answer |
|---|---|
| Tech: real, non-trivial PayPal + AI | AI turns menus and plain-English policies into validated rules, and builds budget-aware orders. PayPal: three payment styles (pay now; hold → exact charge; deposit + balance) with partial captures, releases, refunds, webhooks; payments routed to each business. |
| Design: complete product | Owner onboarding → customer ordering → payment → fulfilment, for any business |
| Impact | Small businesses get ordering, deposits and flexible payments that normally need custom software |
| Innovation | Plain-English rules enforced by code; budget-first ordering; payment style per product type |
| Presentation | Set up a bakery in minutes, then a customer orders a party within budget |

## 3. Owner onboarding (built)

1. **Business profile:** name, type, description, PayPal email (where payments go).
2. **Products:** CSV upload (template provided), paste text for the AI to read, or manual entry. Every import goes to a **review table**; nothing is saved until the owner confirms. Invalid rows are flagged and block saving.
3. **Rules & payments:** the business starts with **default settings for its type** (below). One card per setting: change it, turn it off, or make it different for a category, using fixed fields only. Code validates every value and writes the summary text. "Reset to defaults" is available. A hint flags categories with by-weight or on-request products that still charge in full up front.
   - **Advanced (hidden by default):** plain-English policies → AI proposes settings → owner ticks each one (code-written summary) → saving replaces the matching setting. Free-text notes for the assistant (guidance only, not enforced).
4. **Done:** summary, payment style per category, customer link.

The app starts with an **empty database**; there is no seed data.

### Product fields
name, category, price, pricing (`fixed` | `per_weight` | `quote`), unit, tags (dietary/allergen), notice needed (hours), description.

### Rule kinds (closed set, enforced by code)

| Kind | Params |
|---|---|
| payment | style: `pay_now` / `hold_exact` (+ buffer %) / `deposit_balance` (+ deposit %) |
| lead_time | hours of notice |
| min_order | minimum for every order |
| delivery | radius, fee, free-over amount, minimum for delivery |
| capacity | max per day |
| substitution | allowed yes/no |
| note | guidance for the assistant, not enforced |

A rule applies to the whole business or to **one or more categories**. There is **one setting per kind and scope**: saving a new one replaces the old (notes can repeat). Category-specific payment rules override the business-wide one.

### Defaults by business type
Every business: pay in full when ordering; no substitutions. Plus:

| Type | Defaults |
|---|---|
| bakery | 1 day's notice |
| grocer | hold +15% → exact charge; substitutions allowed |
| butcher | hold +10% → exact charge; 1 day's notice |
| florist | 1 day's notice; substitutions allowed |
| caterer | 50% deposit + balance; 3 days' notice; $100 minimum |

**Live test (Atria, 2026-10-11):** a messy bakery menu → 11 correct products in 72 s (dozen pricing, nut-free tags, "3 days notice" → 72 h, "ask us" → price on request). Seven plain-English policies → 7 correct rules in 44–198 s, after fixing two format gaps the review step exposed (rules for several categories; delivery-only minimum).

## 4. Customer ordering (next)

**First: an AI reliability test** (script, like the PayPal ones in `scripts/`). Load a bakery catalog and settings, send ~15 realistic custom-order requests (tight budget, allergy, too-short notice, product that doesn't exist, vague request), and measure how often the AI's proposal is wrong and **whether code catches every wrong one**. Also measures speed.

- Each business gets a link / QR code: `/b/<business>`.
- Customer describes need + budget + constraints → AI proposes an order **only from this catalog**, checked by code against the rules (notice, capacity, minimums, delivery area) and the budget.
- Customer approves in PayPal → owner prepares → actuals recorded (weights, substitutions) → exact charge.

## 5. Payment styles (tested in sandbox)

| Style | Fits | PayPal flow |
|---|---|---|
| Pay now | Fixed-price items | Charge immediately |
| Hold → exact charge | By weight, substitutions, quotes | Hold (+ buffer) → capture exact amount → rest released |
| Deposit + balance | Custom / made-to-order | Hold → capture deposit (`final_capture=false`) → capture balance at pickup |

**Routing money to each business:** the platform creates orders with `purchase_units[].payee` = the business's PayPal email (collected at onboarding).
- [x] **Tested 2026-10-11 (`scripts/paypal/third_party_payee.py`):** with a separate US sandbox business as payee, a $60 hold → $18 deposit (`final_capture=false`) → $42 balance (`final_capture=true`) all succeeded, and both captures were received by the business's account (its own `merchant_id`), not the platform's. PayPal's approval page shows the business's name.
- Fees on that US business were 3.49% + $0.49 (standard US rate), so demo businesses should be US sandbox accounts.
- No fallback needed for the prototype. "Connect with PayPal" partner onboarding remains the production path (verifies the business owns the account).

## 6. AI provider

Any OpenAI-compatible endpoint, set by `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`. Currently **Atria Dawn Preview**: good quality, free, text-only, but **slow (20–200 s per call)** and a preview (availability during judging Dec 1–15 is a risk). Plan: keep for development, design the UI for slow calls, and switch provider by config if needed before the demo.

## 7. Out of scope

Photo/menu-image onboarding, delivery logistics, inventory syncing, POS integrations, real money, multi-currency. MCP server is planned **after** the web ordering flow works.

## 8. Demo plan

1. Set up a bakery: upload or paste the menu, check the default settings, make custom cakes take a 50% deposit.
2. A customer orders a party within budget; the custom cake takes a 50% deposit; balance charged at pickup.
3. Quick second business (e.g., a small grocer with items sold by weight: hold → exact charge) to show it's general.

## 9. Sandbox and API results

**PayPal (2026-10-10, `scripts/paypal/holds_and_captures.py`):** hold $720 → partial capture $160 (`final_capture=false`) → second hold $150 → captures $515 + $120 with `final_capture=true` (remainders released) → partial refund $18.50. All succeeded.
- Fees ≈ 4.4% (cross-border: Malaysian sandbox business, US buyer). Use actual fees from captures.
- Refunds don't return the PayPal fee: charge after problems are resolved rather than over-charging and refunding.

**Kroger (2026-10-11, `scripts/kroger/explore.py`; real test catalog: `scripts/kroger/build_catalog.py`):** production API works (real prices, promos, `soldBy=WEIGHT`, stock levels). No longer core since businesses upload their own catalogs; optional data source for a grocer demo.

## 10. Open decisions

1. Product name
2. Demo businesses (bakery + small grocer?)
3. AI provider for the final demo (speed)
4. Team size and skills
