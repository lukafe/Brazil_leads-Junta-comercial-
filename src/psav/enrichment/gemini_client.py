"""Async wrapper around the google-genai SDK.

Single public surface: ``GeminiClient.ask_json(prompt, schema, use_search)``.

Implementation note — the Gemini API does NOT allow ``response_schema``
together with ``tools=[google_search]`` in a single request. When grounding
is requested, we make two calls:

1. **Grounded research** — free-form text answer with citations.
2. **Schema extraction** — feeds the answer back, asks Gemini to emit
   strict JSON matching the schema.

Both calls share the same model. Grounding URLs are extracted from
``response.candidates[0].grounding_metadata`` of the first call.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from google import genai
from google.genai import types as genai_types

from psav.exceptions import (
    GeminiQuotaExceeded,
    GeminiSchemaError,
    GeminiTransientError,
)
from psav.utils.logging import logger
from psav.utils.retry import gemini_retry

# Per-call wall-clock cap. The google-genai SDK does NOT enforce a network
# timeout by default; without this, "Server disconnected" can hang up to ~10
# minutes per attempt before bubbling up. 90s is plenty for a grounded search
# on Pro and stops a hung TCP from poisoning the batch.
PER_CALL_TIMEOUT_S = 90


class GeminiClient:
    """Thin async wrapper. Use as a singleton — instantiate once per pipeline."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        enable_search: bool = True,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._enable_search = enable_search

    @property
    def model(self) -> str:
        return self._model

    # =====================================================================
    # Public surface
    # =====================================================================
    async def ask_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        use_search: bool = True,
    ) -> tuple[dict[str, Any], list[str]]:
        """Run a (research → schema) two-call cycle.

        Returns ``(parsed_dict, grounding_urls)``. ``parsed_dict`` is JSON
        validated against ``schema``; ``grounding_urls`` is the list of source
        URLs cited by the search tool (empty when ``use_search=False``).
        """
        if use_search and self._enable_search:
            research_text, grounding_urls = await self._grounded_research(prompt)
            parsed = await self._extract_with_schema(prompt, research_text, schema)
            return parsed, grounding_urls
        # Schema-only call (no grounding).
        parsed = await self._extract_with_schema(prompt, "", schema)
        return parsed, []

    # =====================================================================
    # Internals
    # =====================================================================
    @gemini_retry
    async def _grounded_research(self, prompt: str) -> tuple[str, list[str]]:
        """Free-form research call with Google Search grounding."""
        config = genai_types.GenerateContentConfig(
            tools=[genai_types.Tool(google_search=genai_types.GoogleSearch())],
            temperature=0.0,
        )
        resp = await self._call_with_timeout(prompt=prompt, config=config)
        text = (resp.text or "").strip()
        urls = _extract_grounding_urls(resp)
        return text, urls

    @gemini_retry
    async def _extract_with_schema(
        self,
        original_prompt: str,
        research_text: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        """Second call: feeds the research answer back, returns strict JSON."""
        if research_text:
            full = (
                "You answered an earlier research question. Convert your answer "
                "into strict JSON matching the schema below. Use ONLY information "
                "from your earlier answer — do NOT invent values.\n\n"
                f"--- EARLIER PROMPT ---\n{original_prompt}\n\n"
                f"--- YOUR ANSWER ---\n{research_text}\n\n"
                "Respond with ONLY the JSON object, no prose."
            )
        else:
            full = original_prompt + (
                "\n\nRespond with ONLY a single JSON object matching the schema, "
                "no prose, no Markdown fences."
            )

        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
        )
        resp = await self._call_with_timeout(prompt=full, config=config)
        raw = (resp.text or "").strip()
        return _safe_json_parse(raw)

    async def _call_with_timeout(
        self,
        *,
        prompt: str,
        config: genai_types.GenerateContentConfig,
    ) -> Any:
        """Run a generate_content call with a wall-clock cap and translate errors."""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    self._client.models.generate_content,
                    model=self._model,
                    contents=prompt,
                    config=config,
                ),
                timeout=PER_CALL_TIMEOUT_S,
            )
        except TimeoutError as e:
            raise GeminiTransientError(
                f"Gemini call exceeded {PER_CALL_TIMEOUT_S}s wall clock"
            ) from e
        except Exception as e:
            self._raise_translated(e)
            raise  # unreachable, mypy hint

    @staticmethod
    def _raise_translated(exc: Exception) -> None:
        msg = str(exc)
        lowered = msg.lower()
        if (
            "resource_exhausted" in lowered
            or "rate limit" in lowered
            or "429" in lowered
            or "quota" in lowered
        ):
            raise GeminiQuotaExceeded(msg) from exc
        if (
            "server disconnected" in lowered
            or "remoteprotocolerror" in lowered
            or "503" in lowered
            or "502" in lowered
            or "504" in lowered
            or "unavailable" in lowered
            or "deadline" in lowered
            or "internal error" in lowered
            or "connection reset" in lowered
            or "incompletread" in lowered
        ):
            raise GeminiTransientError(msg) from exc
        raise


def _safe_json_parse(raw: str) -> dict[str, Any]:
    """Parse Gemini's JSON output, tolerating accidental Markdown fencing."""
    if not raw:
        raise GeminiSchemaError("Gemini returned empty body")
    # Strip ```json ... ``` fences if present.
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"Gemini response is not valid JSON: {raw[:200]!r}")
        raise GeminiSchemaError(f"Invalid JSON: {e}") from e
    if not isinstance(parsed, dict):
        raise GeminiSchemaError(f"Expected JSON object, got {type(parsed).__name__}")
    return parsed


def _extract_grounding_urls(resp: Any) -> list[str]:
    """Pull source URLs from grounding metadata (best-effort, never throws)."""
    urls: list[str] = []
    try:
        candidates = getattr(resp, "candidates", None) or []
        for cand in candidates:
            metadata = getattr(cand, "grounding_metadata", None)
            if not metadata:
                continue
            chunks = getattr(metadata, "grounding_chunks", None) or []
            for chunk in chunks:
                web = getattr(chunk, "web", None)
                if web is None:
                    continue
                uri = getattr(web, "uri", None)
                if uri:
                    urls.append(uri)
    except Exception:
        return urls
    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped
