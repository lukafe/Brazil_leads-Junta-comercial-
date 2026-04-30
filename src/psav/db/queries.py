"""Reusable database queries."""

from typing import Any, cast

from postgrest.types import CountMethod

from psav.db.client import get_supabase
from psav.models.company import CompanyCreate
from psav.models.partner import PartnerCreate
from psav.models.signal import SignalCreate

UPSERT_BATCH = 500


def upsert_companies(companies: list[CompanyCreate]) -> int:
    """Upsert in chunks of 500 (PostgREST limit)."""
    if not companies:
        return 0
    sb = get_supabase()
    payload = [c.model_dump(exclude_none=True, mode="json") for c in companies]
    total = 0
    for i in range(0, len(payload), UPSERT_BATCH):
        chunk = payload[i : i + UPSERT_BATCH]
        sb.table("companies").upsert(chunk, on_conflict="cnpj").execute()
        total += len(chunk)
    return total


def upsert_partners(partners: list[PartnerCreate]) -> int:
    """Upsert by (cnpj, nome) — uses the uq_partners_cnpj_nome unique index."""
    if not partners:
        return 0
    sb = get_supabase()
    payload = [p.model_dump(exclude_none=True, mode="json") for p in partners]
    total = 0
    for i in range(0, len(payload), UPSERT_BATCH):
        chunk = payload[i : i + UPSERT_BATCH]
        sb.table("partners").upsert(chunk, on_conflict="cnpj,nome").execute()
        total += len(chunk)
    return total


def insert_signals(signals: list[SignalCreate]) -> int:
    """Insert signals in batches (no dedup — every run appends)."""
    if not signals:
        return 0
    sb = get_supabase()
    payload = [s.model_dump(exclude_none=True, mode="json") for s in signals]
    total = 0
    for i in range(0, len(payload), UPSERT_BATCH):
        chunk = payload[i : i + UPSERT_BATCH]
        sb.table("signals").insert(chunk).execute()
        total += len(chunk)
    return total


def fetch_priority_leads(limit: int = 30) -> list[dict[str, Any]]:
    sb = get_supabase()
    res = sb.table("v_priority_leads").select("*").limit(limit).execute()
    return cast(list[dict[str, Any]], res.data or [])


def stats_summary() -> dict[str, Any]:
    """Quick counters for the `psav stats` command."""
    sb = get_supabase()
    exact = CountMethod.exact
    total = sb.table("companies").select("cnpj", count=exact).execute().count or 0
    by_group_a = (
        sb.table("companies").select("cnpj", count=exact).eq("group_type", "A").execute().count
        or 0
    )
    by_group_b = (
        sb.table("companies").select("cnpj", count=exact).eq("group_type", "B").execute().count
        or 0
    )
    signals = sb.table("signals").select("id", count=exact).execute().count or 0
    partners = sb.table("partners").select("id", count=exact).execute().count or 0
    return {
        "companies_total": total,
        "group_a": by_group_a,
        "group_b": by_group_b,
        "signals": signals,
        "partners": partners,
    }
