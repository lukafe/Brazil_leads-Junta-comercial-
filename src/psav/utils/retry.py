"""tenacity wrappers with sane defaults."""

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from psav.exceptions import GeminiQuotaExceeded, GeminiTransientError

http_retry = retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)


# Gemini retry covers (a) the per-minute rate-limit window (~60s), (b) Pro
# infra hiccups (503/disconnect — common on gemini-2.5-pro paid tier).
gemini_retry = retry(
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type(
        (GeminiQuotaExceeded, GeminiTransientError, ConnectionError, TimeoutError)
    ),
    reraise=True,
)
