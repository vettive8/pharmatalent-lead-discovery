"""Shared HTTP retry policy for all provider clients.

Bounded exponential backoff on transient failures (timeouts, connection errors,
429, 5xx). Bounded — never infinite — so a dead provider fails the run instead of
hanging it. 4xx other than 429 is a caller/credential error and is NOT retried.
"""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS
    return False


# Decorator: up to 4 attempts, 1s -> 2s -> 4s (capped at 10s) backoff.
with_retry = retry(
    reraise=True,
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1, max=10),
    retry=retry_if_exception(_is_retryable),
)
