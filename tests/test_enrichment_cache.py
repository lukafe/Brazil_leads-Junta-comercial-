"""Tests for the enrichment disk cache."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from psav.enrichment import cache as cache_mod


def test_load_missing_returns_empty(tmp_path: Path) -> None:
    cache = cache_mod.load(tmp_path / "absent.json")
    assert cache == {"version": cache_mod.CACHE_VERSION, "entries": {}}


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache = cache_mod.load(cache_path)
    cache_mod.set_entry(
        cache,
        cnpj="12.345.678/0001-90",
        enrichment_data={"origin": "brasileira", "size": "mid"},
        raw_responses={"origin": {"answer": "..."}},
        model="gemini-2.5-flash",
    )
    cache_mod.save(cache_path, cache)
    reloaded = cache_mod.load(cache_path)
    entry = cache_mod.get(reloaded, "12345678000190")
    assert entry is not None
    assert entry["data"]["origin"] == "brasileira"
    assert entry["model"] == "gemini-2.5-flash"


def test_is_stale_when_model_changes() -> None:
    entry = {
        "enriched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": "gemini-2.0-flash",
        "data": {},
    }
    assert cache_mod.is_stale(entry, model="gemini-2.5-flash") is True


def test_is_stale_when_old() -> None:
    old_ts = datetime.now(UTC) - timedelta(days=45)
    entry = {
        "enriched_at": old_ts.isoformat(timespec="seconds"),
        "model": "gemini-2.5-flash",
        "data": {},
    }
    assert cache_mod.is_stale(entry, model="gemini-2.5-flash", ttl_days=30) is True


def test_is_fresh() -> None:
    fresh_ts = datetime.now(UTC) - timedelta(days=1)
    entry = {
        "enriched_at": fresh_ts.isoformat(timespec="seconds"),
        "model": "gemini-2.5-flash",
        "data": {},
    }
    assert cache_mod.is_stale(entry, model="gemini-2.5-flash", ttl_days=30) is False


def test_is_stale_missing_data() -> None:
    assert cache_mod.is_stale({}, model="gemini-2.5-flash") is True
    assert cache_mod.is_stale(
        {"enriched_at": "2026-01-01T00:00:00+00:00", "model": "gemini-2.5-flash"},
        model="gemini-2.5-flash",
    ) is True  # missing 'data' key


def test_version_mismatch_resets(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text('{"version": 999, "entries": {"12345678000190": {}}}')
    reloaded = cache_mod.load(cache_path)
    assert reloaded["entries"] == {}


def test_save_atomic_no_temp_left_behind(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_mod.save(cache_path, {"version": 1, "entries": {}})
    # No .cache_*.tmp files should remain.
    leftovers = list(tmp_path.glob(".cache_*"))
    assert leftovers == []
