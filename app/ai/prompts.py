"""Every prompt the AI uses, versioned.

Don't edit a prompt once it has been used: add the next version and say what changed and why.
Agents use the latest version unless one is pinned, either for the whole server
(`PROMPT_VERSIONS=office_desk=1,receipt_reader=2` in .env) or per agent (`OfficeDesk(prompt_version=1)`).
Every result records the prompt it ran with (`result.prompt_id`, e.g. "office_desk@v2"), so the
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
    """Versions pinned in the PROMPT_VERSIONS env var, e.g. "office_desk=1,receipt_reader=2"."""
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
