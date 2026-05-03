"""Disk-backed JSON cache for enrichment results.

Single file at ``data/enrichment_cache.json`` (configurable). Atomic writes
via tempfile + ``os.replace``. Designed for re-runs to skip companies that
were already enriched within the TTL.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from psav.utils.logging import logger
from psav.utils.text import clean_cnpj

CACHE_VERSION = 1
DEFAULT_TTL_DAYS = 30


def _empty() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "entries": {}}


def load(path: Path) -> dict[str, Any]:
    """Read the cache file. Returns an empty cache if missing or unreadable."""
    if not path.exists():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not parse cache at {path} ({e}); starting fresh")
        return _empty()
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        logger.info(
            f"Cache version mismatch (file={raw.get('version') if isinstance(raw, dict) else None} "
            f"vs code={CACHE_VERSION}); wiping cache"
        )
        return _empty()
    raw.setdefault("entries", {})
    return cast("dict[str, Any]", raw)


def save(path: Path, cache: dict[str, Any]) -> None:
    """Atomically write the cache to disk (tempfile + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".cache_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_name, path)
    except Exception:
        # Make sure we don't leave a stray temp file behind.
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def get(cache: dict[str, Any], cnpj: str) -> dict[str, Any] | None:
    entry = cache.get("entries", {}).get(clean_cnpj(cnpj))
    return cast("dict[str, Any] | None", entry)


def set_entry(
    cache: dict[str, Any],
    cnpj: str,
    enrichment_data: dict[str, Any],
    raw_responses: dict[str, Any] | None,
    model: str,
) -> None:
    cache.setdefault("entries", {})[clean_cnpj(cnpj)] = {
        "enriched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "data": enrichment_data,
        "raw_responses": raw_responses or {},
    }


def is_stale(
    entry: dict[str, Any],
    *,
    model: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
    now: datetime | None = None,
) -> bool:
    """Return True when the entry should be re-queried.

    Reasons (in priority order):
      1. Model identifier differs from current settings.
      2. Older than ``ttl_days`` days.
      3. Missing ``enriched_at``/``data`` fields.
    """
    if not entry or "data" not in entry:
        return True
    if entry.get("model") != model:
        return True
    enriched_at = entry.get("enriched_at")
    if not enriched_at:
        return True
    try:
        ts = datetime.fromisoformat(enriched_at)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    now = now or datetime.now(UTC)
    return (now - ts) > timedelta(days=ttl_days)
