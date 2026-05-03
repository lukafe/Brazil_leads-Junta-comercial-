"""Excel exporter — output ready for manual review and outreach.

Generated workbook structure:
  • sheet "leads"          — one row per SPSAV, ranked by capital_social desc
  • sheet "socios"         — one row per partner, joinable via cnpj
  • sheet "watchlist_only" — known players that did NOT appear in the dump
  • sheet "partner_matches"— stealth subsidiaries (known executive in new CNPJ)
  • sheet "metadata"       — generation timestamp, counts, source
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from psav.utils.logging import logger

# Columns of the "leads" sheet — order matters (this is the visual order in Excel).
LEADS_COLUMNS = [
    "match_source",
    "match_details",
    "cnpj",
    "razao_social",
    "nome_fantasia",
    "website",
    "origin",
    "origin_parent_company",
    "size",
    "size_score",
    "tech_profile",
    "estimated_headcount",
    "capital_social",
    "data_constituicao",
    "data_ultima_alteracao",
    "cnae_principal",
    "cnae_secundarios",
    "endereco_uf",
    "endereco_municipio",
    "telefone",
    "email_contato",
    "enrichment_evidence",
]

WATCHLIST_ONLY_COLUMNS = [
    "razao_social_aliases",
    "cnpj_esperado",
    "category",
    "priority",
    "notes",
]

PARTNER_MATCH_COLUMNS = [
    "cnpj",
    "partner_name",
    "matched_known",
    "associations",
    "category",
    "priority",
]

SOCIOS_COLUMNS = [
    "cnpj",
    "nome",
    "qualificacao",
    "cpf_cnpj",
    "data_entrada",
]


def _format_cnpj(cnpj: str) -> str:
    """12345678000190 → 12.345.678/0001-90."""
    if not cnpj or len(cnpj) != 14:
        return cnpj or ""
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def _enrich_company_row(c: dict[str, Any]) -> dict[str, Any]:
    """Format CNPJ and convert cnae_secundarios to a human-readable string.

    Reads enrichment fields when present (Gemini-powered post-extraction
    pass); leaves them ``None`` otherwise.
    """
    cnaes = c.get("cnae_secundarios") or []
    nome_fantasia = c.get("nome_fantasia") or c.get("nome_fantasia_enriched")
    return {
        "match_source": c.get("match_source", "spsav_regex"),
        "match_details": c.get("match_details", ""),
        "cnpj": _format_cnpj(c["cnpj"]),
        "razao_social": c.get("razao_social"),
        "nome_fantasia": nome_fantasia,
        "website": c.get("website"),
        "origin": c.get("origin"),
        "origin_parent_company": c.get("origin_parent_company"),
        "size": c.get("size"),
        "size_score": c.get("size_score"),
        "tech_profile": c.get("tech_profile"),
        "estimated_headcount": c.get("estimated_headcount"),
        "capital_social": float(c["capital_social"]) if c.get("capital_social") else None,
        "data_constituicao": c.get("data_constituicao"),
        "data_ultima_alteracao": c.get("data_ultima_alteracao"),
        "cnae_principal": c.get("cnae_principal"),
        "cnae_secundarios": ", ".join(cnaes) if cnaes else None,
        "endereco_uf": c.get("endereco_uf"),
        "endereco_municipio": c.get("endereco_municipio"),
        "telefone": c.get("telefone"),
        "email_contato": c.get("email_contato"),
        "enrichment_evidence": c.get("enrichment_evidence"),
    }


def _enrich_watchlist_only_row(w: Any) -> dict[str, Any]:
    return {
        "razao_social_aliases": ", ".join(getattr(w, "razao_social_aliases", [])),
        "cnpj_esperado": _format_cnpj(getattr(w, "cnpj", None) or ""),
        "category": getattr(w, "category", ""),
        "priority": getattr(w, "priority", ""),
        "notes": getattr(w, "notes", ""),
    }


def _enrich_partner_match_row(m: dict[str, Any]) -> dict[str, Any]:
    return {
        "cnpj": _format_cnpj(m.get("cnpj", "")),
        "partner_name": m.get("partner_name"),
        "matched_known": m.get("matched_known"),
        "associations": ", ".join(m.get("associations") or []),
        "category": m.get("category"),
        "priority": m.get("priority"),
    }


def _fmt_currency(val: Any) -> str:
    """Format float as '#,##0.00'; return 'n/a' for None or invalid input."""
    if val is None:
        return "n/a"
    try:
        return f"{float(val):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _enrich_partner_row(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "cnpj": _format_cnpj(p["cnpj"]),
        "nome": p.get("nome"),
        "qualificacao": p.get("qualificacao"),
        "cpf_cnpj": p.get("cpf_cnpj"),
        "data_entrada": p.get("data_entrada"),
    }


def write_excel(
    companies: list[dict[str, Any]],
    partners: list[dict[str, Any]],
    out_path: Path,
    source_label: str = "receita_dump",
    watchlist_only: list[Any] | None = None,
    partner_matches: list[dict[str, Any]] | None = None,
) -> Path:
    """Write a .xlsx with leads + partners + metadata + (optional) watchlist_only / partner_matches.

    `companies` and `partners` are the raw dicts returned by `extract_spsavs()`.
    `watchlist_only` is the list of WatchedCompany entries not found in the dump.
    `partner_matches` is the list of partner ↔ known-person matches.
    """
    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    watchlist_only = watchlist_only or []
    partner_matches = partner_matches or []

    # Leads ranked by capital_social desc (NULLs last).
    enriched_companies = [_enrich_company_row(c) for c in companies]
    leads_df = pl.DataFrame(enriched_companies, schema=LEADS_COLUMNS).sort(
        "capital_social", descending=True, nulls_last=True
    )

    enriched_partners = [_enrich_partner_row(p) for p in partners]
    socios_df = (
        pl.DataFrame(enriched_partners, schema=SOCIOS_COLUMNS)
        if enriched_partners
        else pl.DataFrame(schema={col: pl.Utf8 for col in SOCIOS_COLUMNS})
    )

    enriched_watchlist_only = [_enrich_watchlist_only_row(w) for w in watchlist_only]
    watchlist_only_df = (
        pl.DataFrame(enriched_watchlist_only, schema=WATCHLIST_ONLY_COLUMNS)
        if enriched_watchlist_only
        else pl.DataFrame(schema={col: pl.Utf8 for col in WATCHLIST_ONLY_COLUMNS})
    )

    enriched_partner_matches = [_enrich_partner_match_row(m) for m in partner_matches]
    partner_matches_df = (
        pl.DataFrame(enriched_partner_matches, schema=PARTNER_MATCH_COLUMNS)
        if enriched_partner_matches
        else pl.DataFrame(schema={col: pl.Utf8 for col in PARTNER_MATCH_COLUMNS})
    )

    metadata_df = pl.DataFrame(
        {
            "field": [
                "generated_at",
                "source",
                "leads_total",
                "partners_total",
                "distinct_states",
                "capital_social_median",
                "capital_social_p90",
            ],
            "value": [
                datetime.now(UTC).isoformat(timespec="seconds"),
                source_label,
                str(len(enriched_companies)),
                str(len(enriched_partners)),
                str(leads_df["endereco_uf"].n_unique()) if enriched_companies else "0",
                _fmt_currency(leads_df["capital_social"].median()) if enriched_companies else "n/a",
                _fmt_currency(leads_df["capital_social"].quantile(0.90))
                if enriched_companies
                else "n/a",
            ],
        }
    )

    import xlsxwriter

    workbook = xlsxwriter.Workbook(out_path)
    _write_sheet(workbook, "leads", leads_df, currency_cols={"capital_social"})
    _write_sheet(workbook, "socios", socios_df)
    _write_sheet(workbook, "watchlist_only", watchlist_only_df)
    _write_sheet(workbook, "partner_matches", partner_matches_df)
    _write_sheet(workbook, "metadata", metadata_df, autosize=True)
    workbook.close()

    logger.info(f"Excel written: {out_path}")
    return out_path


def _write_sheet(
    workbook: Any,
    name: str,
    df: pl.DataFrame,
    currency_cols: set[str] | None = None,
    autosize: bool = True,
) -> None:
    ws = workbook.add_worksheet(name)
    header_fmt = workbook.add_format(
        {"bold": True, "bg_color": "#1f2937", "font_color": "#ffffff", "border": 1}
    )
    currency_fmt = workbook.add_format({"num_format": "#,##0.00"})
    date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd"})

    cols = df.columns
    for col_idx, col_name in enumerate(cols):
        ws.write(0, col_idx, col_name, header_fmt)

    rows = df.to_dicts()
    for row_idx, row in enumerate(rows, start=1):
        for col_idx, col_name in enumerate(cols):
            val = row.get(col_name)
            if val is None:
                continue
            fmt = None
            if currency_cols and col_name in currency_cols:
                fmt = currency_fmt
            elif col_name.startswith("data_"):
                fmt = date_fmt
            if isinstance(val, (int, float)):
                ws.write_number(row_idx, col_idx, val, fmt)
            else:
                ws.write_string(row_idx, col_idx, str(val), fmt)

    if autosize:
        for col_idx, col_name in enumerate(cols):
            sample_vals = [str(r.get(col_name) or "") for r in rows[:200]]
            max_len = max([len(col_name)] + [len(v) for v in sample_vals])
            ws.set_column(col_idx, col_idx, min(max_len + 2, 60))

    ws.freeze_panes(1, 0)
    if rows:
        ws.autofilter(0, 0, len(rows), len(cols) - 1)
