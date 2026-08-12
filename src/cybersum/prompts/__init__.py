"""Prompts as versioned data files.

Keeping prompt text in ``.txt`` files rather than string literals makes it
reviewable and diffable -- and also easier to reword by accident, which is a
problem when the wording is what an experiment measured. ``PROMPTS.lock.json``
pins a sha256 per file; ``tests/unit/test_prompt_lock.py`` fails if one moves.

Two report prompts exist on purpose and must not be merged. The production
prompt carries formatting rules for the dashboard and the instruction that
produces the machine-readable ``###DATA_START###`` block. The evaluation prompt
does not, and the published three-group results were produced with the
evaluation one. Merging them would silently change what those numbers mean. See
docs/prompts.md.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
LOCKFILE = PROMPT_DIR / "PROMPTS.lock.json"


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    sha256: str


@lru_cache(maxsize=None)
def load_prompt(name: str) -> Prompt:
    path = PROMPT_DIR / f"{name}.txt"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in PROMPT_DIR.glob("*.txt")))
        raise KeyError(f"No prompt named {name!r}. Available: {available}")
    text = path.read_text()
    return Prompt(name=name, text=text, sha256=hashlib.sha256(text.encode()).hexdigest())


@lru_cache(maxsize=1)
def load_lockfile() -> dict[str, dict]:
    return json.loads(LOCKFILE.read_text())


def prompt_names() -> list[str]:
    return sorted(p.stem for p in PROMPT_DIR.glob("*.txt"))
