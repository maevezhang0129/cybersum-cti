"""Retry policy for model calls.

Failures are classified rather than blanket-retried: throttling and transport
faults are worth another attempt, a rejected key or a malformed request is not.
Retrying the latter burns the backoff budget to arrive at the same error.
"""

from __future__ import annotations

import random
from typing import Final

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    RateLimitError,
)

MAX_RETRIES: Final = 3
INITIAL_BACKOFF_SECONDS: Final = 1.0
MAX_BACKOFF_SECONDS: Final = 8.0

#: Transient by nature: the same request may well succeed shortly.
RETRYABLE = (RateLimitError, APIConnectionError, APITimeoutError)


def should_retry(exception: Exception, attempt: int, max_retries: int = MAX_RETRIES) -> bool:
    if attempt >= max_retries:
        return False
    if isinstance(exception, RETRYABLE):
        return True
    # A 5xx is the server's problem; a 4xx is ours and will not change by repeating.
    return isinstance(exception, APIError) and getattr(exception, "status_code", 0) >= 500


def backoff_seconds(attempt: int, *, jitter: bool = True) -> float:
    """Exponential backoff with full jitter, capped.

    Jitter matters when several instances hit the same rate limit: without it
    they retry in lockstep and trip it again together.
    """
    base = min(INITIAL_BACKOFF_SECONDS * (2 ** (attempt - 1)), MAX_BACKOFF_SECONDS)
    return base + random.uniform(0, base) if jitter else base
