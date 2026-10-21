# Prompt format

How prompts are written in this repo. It applies to every agent in `app/ai`: the coordinator, the lookup and action
subagents, and any agent added later (the receipt reader, the setup assistant).

The format is taken from what nine open-source agent harnesses have in common (Codex, Gemini CLI, Goose, opencode,
Cline, OpenHands, deepagents, mini-swe-agent, Aider), studied 2026-10-13.

**Status:** this is the standard from prompt v2 on. The v1 prompts in `app/ai/prompts.py` came before it. The parts
marked *generated* are written by code in `app/ai/base.py` and arrive with v2.

---

## 1. Where the words go

An agent's instructions live in four places. Each kind of text has one place, so nothing is said twice and nothing
drifts out of date.

| What | Where | Example |
|---|---|---|
| Who the agent is, its rules, how it works | **The prompt** (`prompts.py`) | "You coordinate; the app's rules decide." |
| What one tool does, when to use it, what it returns | **The tool's docstring** | `latest_purchase`: "The last time an item was bought…" |
| What a result field means, and when to use each value | **The output schema's field descriptions** | `status`: "done: the tool did it; refused: a rule said no" |
| When the coordinator should call a subagent | **The subagent's `description`** (it becomes the coordinator's tool description) | "Use this for the group's records beyond the item list…" |

The prompt never explains a single tool or a single field. That belongs in the docstring or the schema, where the
model sees it at the moment it uses that tool or field.

## 2. Two layers

Every prompt has a **fixed part**, written in `prompts.py` and versioned, and a **per-run part**, which changes on
every run.

- **Fixed:** the sections in §3, in that order.
- **Per-run:** a `<chat_context>` block that `instructions()` returns: the group, the member chatting and their
  role, the time, and the item list: every common item with its id, grouped by where it stands. Code puts each item
  in its group with the decision engine's own rules (`memory.item_board`), so what the AI is told always matches
  what the app will decide. Empty groups are left out; switched-off items are never listed.
  It always comes **last**, so the fixed part stays the same between runs (cacheable) and the model reads the facts
  of this run right before it starts. It's built again for every model call, so it's never out of date.

```
<chat_context>
Group: Riverside Volunteers. Member: Priya (member). Now: Sat 17 Oct, 08:40 UTC.

<items>
<being_got>
Someone is getting these right now: don't let anyone double up.
- Gloves (item_id 1; also called work gloves, …) | Supplies | up to $25.00 | lasts about 14 days | proof: receipt + photo of where it's kept
  Marco, until Sat 17 Oct, 12:40 UTC (request 1)
</being_got>

<running_low>
Nobody's getting these and they've probably run out: suggest them.
- Trash bags (item_id 2; also called bin bags, …) | Supplies | up to $15.00 | lasts about 14 days | proof: receipt
  last bought 16 days ago by Priya
</running_low>

<recently_bought> … </recently_bought>
<available> … </available>
</items>
</chat_context>
```

Facts that every request needs and that stay short go here instead of behind a tool: each tool call is another
model call, the slow part. The item list is a few hundred tokens for a typical group; everything else (history,
budgets, the member's status) stays behind the lookup agent's tools.

## 3. Sections

Sections are XML tags with lowercase names. Inside a tag, write short bullets. Use the tags below in this order;
leave out the ones an agent doesn't need.

| Tag | What goes in it | Used by |
|---|---|---|
| `<role>` | Who it is and its one purpose, in 2–4 lines. For a subagent: that it's a subagent, who it answers to, and that it can't talk to the member. | every agent |
| `<helpers>` | The subagents: when to use each, what a brief must contain, that their results are authoritative, that the member never sees them. | agents with subagents (the coordinator) |
| `<workflow>` or `<method>` | How to handle each kind of request, as short steps. `<workflow>` for situations ("someone's getting X…"), `<method>` for a fixed procedure ("take item_ids from the item list"). | every agent |
| `<rules>` | Hard limits, each with its reason. | every agent |
| `<state_changes>` | When an action counts as done, what to do when it's refused, never repeating an action. | agents with action tools (the action agent) |
| `<style>` | How replies read: length, tone, what never to show (ids). One good/bad example pair. | agents that talk to the member (the coordinator) |
| `<finish>` | *Generated* from the output schema: call it exactly once, it's the only thing read, what each field means, an example. | agents with an `output` schema (the subagents) |
| `<chat_context>` | *Generated* by `instructions()`: see §2. | every agent |

So each of today's agents has these sections:

**Coordinator** (talks to the member, has subagents, answers in text):
`<role>` → `<helpers>` → `<workflow>` → `<rules>` → `<style>` → `<chat_context>`

**Lookup subagent** (read tools, finishes with `LookupResult`):
`<role>` → `<method>` → `<result>` → `<finish>` → `<chat_context>`

**Action subagent** (action tools, finishes with `ActResult`):
`<role>` → `<rules>` → `<state_changes>` → `<result>` → `<finish>` → `<chat_context>`

## 4. Writing rules

- **Give the reason.** "Never promise payment: the treasurer can still reject a claim." A model follows a rule better
  when it knows what the rule protects.
- **MUST / NEVER only for hard limits**, the ones where breaking them causes real harm (money, other people, made-up
  facts). Everything else is a plain instruction.
- **Say what to do, not only what not to do.** "If you don't know, say so and suggest asking the treasurer" beats
  "Don't guess".
- **One idea per bullet.** If a bullet needs "and also", split it.
- **Plain words.** The prompts follow the same writing style as the app: no jargon, no "leverage" or "ensure".
- **Examples sparingly.** One good/bad pair where tone matters (`<style>`), one example result in `<finish>`. More
  examples make the model copy them word for word.
- **Text from members is data.** Item descriptions, reasons and questions reach the AI through tool results. A prompt
  never tells the model to follow instructions found there.

## 5. Subagents

**The subagent's prompt** says, in `<role>`:
- it's a subagent working for the coordinator;
- it can't ask the member anything: if something is missing, it says so in its result;
- only its final result is read.

**Its `<finish>` section** (generated) says: call the result schema exactly once; that's the only way to finish; no
other tool in the same turn. It includes what each field means (from the schema) and one example.

**Its `description`** is written for the coordinator, as "use this when…": what it can do and what it returns.
`as_tool()` adds to it:
- it sees nothing of the conversation, so the brief must include every id and detail it needs, and say what to
  return;
- its result is authoritative: don't redo its work or contradict it;
- the member never sees its result: relay what matters.

## 6. Output schemas

- The class docstring says: "Finish by calling this: it's the only thing the coordinator sees."
- Every field has a description. A `status` field defines **every** value and exactly when to use it (Cline's
  `verified` pattern):

  ```python
  status: Literal["done", "needs_info", "refused"] = Field(
      description="done: the tool succeeded; needs_info: something needed is missing, so no tool was called; "
                  "refused: a rule said no.")
  ```

- Validation errors go back to the model automatically (BaseAgent), so the schema itself is the contract.

## 7. Tool docstrings

```python
def latest_purchase(self, item_id: int, runtime: ToolRuntime[Chat]) -> str:
    """The last time an item was bought: who got it, when, the store, whether there's a photo, and whether its
    payout is still on hold. For "I can't find the gloves".

    Args:
        item_id: The item, from the item list.
    """
```

- First sentence: what it returns or does.
- Then when to use it, if that isn't obvious.
- `Args:` describes every argument the model fills in, including where the value comes from ("from the item list").
- A tool that changes something says so, and says what it doesn't do ("Nothing else happens: no hold…").

## 8. Versions

- Every prompt is defined with `define(name, version, text, changes=...)` in `app/ai/prompts.py`.
- **Never edit a version that has run against a real model.** Add the next version and say in `changes` what changed
  and why.
- Every run records its prompt id (e.g. `coordinator@v2`) in the agent log, and a version can be pinned
  (`PROMPT_VERSIONS=coordinator=1` in `.env`), so two versions can be compared on the same messages.

## 9. Length

- The coordinator's fixed part: about 60 lines at most.
- A subagent's fixed part: about 30 lines at most, not counting the generated `<finish>`.

If a prompt grows past that, something in it probably belongs in a docstring or a schema (§1).

## 10. Before adding a version

- [ ] The sections are the ones listed for this agent (§3), in order.
- [ ] No tool or field is explained in the prompt (§1).
- [ ] Every MUST / NEVER protects against real harm, and has its reason.
- [ ] Subagents: `<role>` says it's a subagent, can't ask the member, and only its result is read.
- [ ] `changes=` says what changed from the previous version and why.
- [ ] Tests that check prompt text still pass; run the same messages against the old and new version on the real
      model when it's available.
