"""Round-trip helpers for ``top_leads.xlsx``.

The BD-facing CLI (``psav enrich excel``) reads the existing workbook, runs
enrichment, and rewrites a new workbook with the same five sheets plus the
extra columns. This avoids having to keep the 26 GB of raw Receita data on
disk after a one-time ``--cleanup`` ingestion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from psav.utils.logging import logger
from psav.utils.text import clean_cnpj


def read_leads_sheet(path: Path) -> list[dict[str, Any]]:
    """Read the ``leads`` sheet from a previously-generated ``top_leads.xlsx``.

    Returns a list of dicts with raw (digits-only) CNPJs plus all original
    columns, ready to feed the enrichment orchestrator.
    """
    if not path.exists():
        raise FileNotFoundError(f"Input Excel not found: {path}")
    df = pl.read_excel(path, sheet_name="leads")
    rows = df.to_dicts()
    out: list[dict[str, Any]] = []
    for r in rows:
        cnpj_raw = r.get("cnpj")
        if not cnpj_raw:
            continue
        r["cnpj"] = clean_cnpj(str(cnpj_raw))
        out.append(r)
    logger.info(f"Loaded {len(out)} leads from {path}")
    return out


def read_other_sheets(path: Path) -> dict[str, pl.DataFrame]:
    """Load every non-leads sheet so we can re-emit them unchanged."""
    sheets = ["socios", "watchlist_only", "partner_matches", "metadata"]
    out: dict[str, pl.DataFrame] = {}
    for name in sheets:
        try:
            out[name] = pl.read_excel(path, sheet_name=name)
        except Exception as e:
            logger.warning(f"Skipping sheet '{name}': {e}")
    return out
