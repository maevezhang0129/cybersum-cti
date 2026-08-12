"""Pulling the two outputs of one model call apart.

A single completion carries a human-readable briefing and, appended to it, a
small JSON object holding the fields the dashboard needs. This module separates
them and tidies the prose for a renderer that only understands plain text.

The ``###DATA_START###`` delimiters are a contract spanning four places that no
tool links together: the prompt that asks for the block, the regex here, the
``status_code``/``top_5_origins`` columns, and the email subject line. Changing
any one of them breaks the others silently. See docs/contracts.md.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

DATA_BLOCK_RE = re.compile(r"###DATA_START###\s*({.*?})\s*###DATA_END###", re.DOTALL)

DEFAULT_STATUS = "STABLE"


@dataclass(frozen=True)
class ParsedReport:
    """Prose plus whatever structured data came with it.

    ``extraction_failed`` exists because the original code degraded a missing or
    malformed block into ``status_code='STABLE'`` and ``top_5_origins={}`` --
    producing a briefing that looks entirely normal and reads as "all clear",
    including on the day the parser breaks. The fallback is kept, because a
    partial report beats no report, but it is no longer silent: callers can see
    that the status was defaulted rather than reported.
    """

    text: str
    data: dict[str, Any] | None
    extraction_failed: bool
    failure_reason: str | None = None

    @property
    def status_code(self) -> str:
        if not self.data:
            return DEFAULT_STATUS
        value = self.data.get("status_code")
        return value if isinstance(value, str) and value else DEFAULT_STATUS

    @property
    def top_5_origins(self) -> dict[str, Any]:
        if not self.data:
            return {}
        value = self.data.get("top_5_origins")
        if not isinstance(value, dict):
            if value is not None:
                logger.warning(
                    "top_5_origins was %s, not an object; using {}.", type(value).__name__
                )
            return {}
        return value


def extract_json_data(raw_response: str) -> ParsedReport:
    """Split the completion into prose and the appended JSON block.

    The block is stripped from the prose whether or not it parsed, so a
    malformed block never leaks delimiters into a briefing someone reads.
    """
    match = DATA_BLOCK_RE.search(raw_response)
    text = DATA_BLOCK_RE.sub("", raw_response).strip()

    if match is None:
        logger.warning("No ###DATA_START###...###DATA_END### block in the response.")
        return ParsedReport(text, None, True, "no data block found")

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("Data block is not valid JSON: %s", exc)
        return ParsedReport(text, None, True, f"malformed JSON: {exc}")

    if not isinstance(data, dict):
        logger.warning("Data block parsed to %s, expected an object.", type(data).__name__)
        return ParsedReport(text, None, True, "data block is not a JSON object")

    return ParsedReport(text, data, False)


def format_report_for_dashboard(raw_report: str) -> str:
    """Normalise whitespace for a renderer that shows plain text verbatim.

    The dashboard this was built for does no Markdown and no reflow, so section
    headings and bullets have to arrive already separated by real newlines.
    """
    formatted = raw_report.strip()

    # Section headings begin their own block.
    formatted = re.sub(
        r"(?<![\n\s])(1:\s*EXECUTIVE\s+SUMMARY)", r"\n\n\1", formatted, flags=re.IGNORECASE
    )
    formatted = re.sub(
        r"(?<![\n\s])(2:\s*(?:TECHNICAL\s+)?BRIEF)", r"\n\n\1", formatted, flags=re.IGNORECASE
    )

    # Bullets begin their own line. A bullet hyphen is surrounded by spaces; a
    # hyphenated word is not. The original rule tested the opposite condition --
    # any hyphen preceded by a non-space -- which meant it never once split the
    # space-separated bullets it was written for, and did split every
    # hyphenated word, rendering "high-intensity attack" as "high\n-intensity
    # attack" in any briefing that used the phrase.
    formatted = re.sub(r"[ \t]+-[ \t]+", "\n- ", formatted)

    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return formatted.lstrip("\n").rstrip("\n") + "\n"
