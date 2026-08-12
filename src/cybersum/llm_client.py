"""One seam over three ways of getting a completion.

The deployed system called Azure OpenAI; the evaluation harness called the
OpenAI API directly; the demo has to work for someone who just cloned the
repository and has no key at all. All three arrive here as a client exposing
``chat.completions.create``, so nothing downstream branches on which one is in
use.

``replay`` returns a response recorded from a real call. It is not a stub: the
text it yields went through the same model, so the demo exercises the real
parsing, formatting and status-extraction path rather than a hand-written
approximation.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .config import LLMSettings

logger = logging.getLogger(__name__)


class ChatClient(Protocol):
    """The sliver of the OpenAI client surface this project uses."""

    @property
    def chat(self) -> Any: ...


# ── Replay ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Message:
    content: str


@dataclass(frozen=True)
class _Choice:
    message: _Message


@dataclass(frozen=True)
class _Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class _RecordedResponse:
    """Shaped like an OpenAI ChatCompletion in the attributes we read."""

    choices: list[_Choice]
    usage: _Usage
    model: str


class ReplayCompletions:
    def __init__(self, cassette: Path) -> None:
        self._cassette = cassette

    def create(self, **kwargs: Any) -> _RecordedResponse:
        if not self._cassette.is_file():
            raise FileNotFoundError(
                f"No recorded response at {self._cassette}. Either record one with "
                f"`cybersum record`, or set OPENAI_API_KEY to make a live call."
            )
        recorded = json.loads(self._cassette.read_text())
        logger.info(
            "Replaying a response recorded on %s from %s. No network call was made.",
            recorded.get("recorded_at", "an unknown date"),
            recorded.get("model", "an unknown model"),
        )
        usage = recorded.get("usage", {})
        return _RecordedResponse(
            choices=[_Choice(_Message(recorded["content"]))],
            usage=_Usage(
                prompt_tokens=usage.get("prompt_tokens", 0),
                completion_tokens=usage.get("completion_tokens", 0),
                total_tokens=usage.get("total_tokens", 0),
            ),
            model=recorded.get("model", "replay"),
        )


class ReplayChat:
    def __init__(self, cassette: Path) -> None:
        self.completions = ReplayCompletions(cassette)


class ReplayClient:
    """Serves a recorded completion. Never touches the network."""

    def __init__(self, cassette: Path) -> None:
        self.chat = ReplayChat(cassette)


DEFAULT_CASSETTE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "cassettes" / "daily_report.json"
)


# ── Factory ──────────────────────────────────────────────────────────────────

def make_client(settings: LLMSettings) -> ChatClient:
    if settings.provider == "replay":
        return ReplayClient(settings.cassette or DEFAULT_CASSETTE)

    if settings.provider == "azure":
        from openai import AzureOpenAI

        if not settings.endpoint or not settings.deployment:
            raise ValueError(
                "Azure requires OPENAI_API_ENDPOINT and OPENAI_DEPLOYMENT_NAME."
            )
        return AzureOpenAI(
            api_key=settings.api_key,
            azure_endpoint=settings.endpoint,
            api_version=settings.api_version,
        )

    from openai import OpenAI

    if not settings.api_key:
        raise ValueError("OPENAI_API_KEY is required for the openai provider.")
    return OpenAI(api_key=settings.api_key)


def model_name(settings: LLMSettings) -> str:
    """What to pass as ``model=``.

    Azure routes by deployment name rather than model name, which is why the
    two are separate settings.
    """
    if settings.provider == "azure":
        return settings.deployment or settings.model
    return settings.model
