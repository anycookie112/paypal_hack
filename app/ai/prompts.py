"""Every prompt the AI uses, versioned.

Don't edit a prompt once it has been used: add the next version and say what changed and why.
Agents use the latest version unless one is pinned, either for the whole server
(`PROMPT_VERSIONS=coordinator=1,receipt_reader=2` in .env) or per call (`prompts.get("coordinator", 1)`).
Every result records the prompt it ran with (`prompt.id`, e.g. "coordinator@v2", kept in the agent log), so the
reliability test can compare versions on the same requests.

Prompts are defined below with `define(name, version, text, changes=...)`.
"""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Prompt:
    name: str
    version: int
    text: str
    changes: str  # what's different from the previous version, and why

    @property
    def id(self) -> str:
        return f"{self.name}@v{self.version}"


_PROMPTS: dict[str, dict[int, Prompt]] = {}


def define(name: str, version: int, text: str, changes: str) -> Prompt:
    versions = _PROMPTS.setdefault(name, {})
    if version in versions:
        raise ValueError(f"{name} v{version} is already defined; add v{max(versions) + 1} instead")
    if versions and version != max(versions) + 1:
        raise ValueError(f"{name}: next version is v{max(versions) + 1}, not v{version}")
    versions[version] = prompt = Prompt(name, version, text.strip(), changes)
    return prompt


def _pinned(name: str) -> int | None:
    """Versions pinned in the PROMPT_VERSIONS env var, e.g. "coordinator=1,receipt_reader=2"."""
    for pair in os.environ.get("PROMPT_VERSIONS", "").split(","):
        key, _, value = pair.partition("=")
        if key.strip() == name and value.strip().isdigit():
            return int(value)
    return None


def get(name: str, version: int | None = None) -> Prompt:
    """The requested version, else the pinned one, else the latest."""
    if name not in _PROMPTS:
        raise KeyError(f"No prompt named {name!r}")
    versions = _PROMPTS[name]
    wanted = version or _pinned(name) or max(versions)
    if wanted not in versions:
        raise KeyError(f"{name} has no v{wanted} (versions: {', '.join(f'v{v}' for v in versions)})")
    return versions[wanted]


def history(name: str) -> list[Prompt]:
    return [p for _, p in sorted(_PROMPTS[name].items())]


# --- the group fund's agents (docs/SPEC.md §8) ---------------------------------------------------------

define("lookup", 1, """
You look things up for the coordinator of Group Fund, an app where members of a volunteer group tell the AI before
they buy something for the group, then get paid back.

- Answer the coordinator's request using only what your tools return. If the tools don't say, say you don't know.
- To work out which item someone means, use find_items. Only call it a list item when one clearly matches (gloves →
  Gloves). If none matches, or you can't tell between two, say it's not on the list and name the closest ones.
- Always include the ids the coordinator will need: item_id, request_id, claim_id, question_id.
- Report facts plainly and briefly: who, what, when, how much. No advice and no guesses.
- Never say whether something will be approved: the app's rules decide that when the member asks for it.
- The coordinator only sees your final result: finish by calling LookupResult.
""", changes="First version.")

define("action", 1, """
You carry out actions for one member of a volunteer group in Group Fund, as the coordinator asks. Every tool acts as
that member, and the app checks every rule and permission.

- Do exactly what the request asks, with the ids and details given. Never guess an id, an amount or a time: if
  something needed is missing, don't call a tool; say what's missing.
- One tool call per action. If a tool says no, report its answer as it is; don't look for a way around the rule.
- The coordinator only sees your final result: finish by calling ActionResult, with what happened in the
  tool's own words.
""", changes="First version.")
