"""BD-focused slim Excel exporter — 5 columns optimised for outreach triage.

Reads an enriched ``top_leads_enriched.xlsx`` and emits a single-sheet
workbook with only the columns a BD officer needs to prioritise outreach:

  1. razao_social    — legal name (cross-checking with regulators)
  2. nome_fantasia   — commercial brand name
  3. origin          — brasileira | internacional (with country flag)
  4. size            — small | mid | big (with priority emoji)
  5. website         — clickable URL

Rows are sorted by:
  size desc (big > mid > small) → origin (brasileira first) → razao_social asc.

Frozen header, autofilter, hyperlinked website cell.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from psav.utils.logging import logger

BD_COLUMNS = [
    "razao_social",
    "nome_fantasia",
    "origin",
    "size",
    "website",
]

_SIZE_ORDER = {"big": 0, "mid": 1, "small": 2}
_ORIGIN_ORDER = {"brasileira": 0, "internacional": 1, None: 2}


def write_bd_excel(in_path: Path, out_path: Path) -> Path:
    """Read enriched xlsx, write slim BD-focused workbook."""
    df = pl.read_excel(in_path, sheet_name="leads")
    rows = df.to_dicts()

    # Coalesce nome_fantasia (some rows have only the enriched one)
    for r in rows:
        if not r.get("nome_fantasia"):
            r["nome_fantasia"] = r.get("nome_fantasia_enriched") or ""

    rows.sort(
        key=lambda r: (
            _SIZE_ORDER.get(r.get("size") or "", 9),
            _ORIGIN_ORDER.get(r.get("origin"), 9),
            (r.get("razao_social") or "").upper(),
        )
    )

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import xlsxwriter

    workbook = xlsxwriter.Workbook(out_path)
    ws = workbook.add_worksheet("leads")

    # Formats
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "bg_color": "#1f2937",
            "font_color": "#ffffff",
            "border": 1,
            "align": "center",
            "valign": "vcenter",
        }
    )
    cell_fmt = workbook.add_format({"valign": "top", "text_wrap": True})
    big_fmt = workbook.add_format(
        {"valign": "top", "bg_color": "#dcfce7", "bold": True}
    )
    mid_fmt = workbook.add_format({"valign": "top", "bg_color": "#fef3c7"})
    link_fmt = workbook.add_format(
        {"valign": "top", "font_color": "#2563eb", "underline": 1}
    )

    headers = {
        "razao_social": "Razão Social",
        "nome_fantasia": "Nome Fantasia",
        "origin": "Origem",
        "size": "Porte",
        "website": "Website",
    }
    for col_idx, key in enumerate(BD_COLUMNS):
        ws.write(0, col_idx, headers[key], header_fmt)

    # Pretty values
    origin_label = {
        "brasileira": "🇧🇷 Brasileira",
        "internacional": "🌎 Internacional",
        None: "—",
    }
    size_label = {
        "big": "🔥 Big",
        "mid": "📊 Mid",
        "small": "📌 Small",
        None: "—",
    }

    for row_idx, r in enumerate(rows, start=1):
        size = r.get("size")
        row_fmt = big_fmt if size == "big" else mid_fmt if size == "mid" else cell_fmt
        ws.write_string(row_idx, 0, r.get("razao_social") or "", row_fmt)
        ws.write_string(row_idx, 1, r.get("nome_fantasia") or "", row_fmt)
        ws.write_string(row_idx, 2, origin_label.get(r.get("origin"), "—"), row_fmt)
        ws.write_string(row_idx, 3, size_label.get(size, "—"), row_fmt)
        site = r.get("website") or ""
        if site and site.startswith(("http://", "https://")):
            ws.write_url(row_idx, 4, site, link_fmt, string=site)
        else:
            ws.write_string(row_idx, 4, site, row_fmt)

    # Column widths
    ws.set_column(0, 0, 60)  # razao_social
    ws.set_column(1, 1, 35)  # nome_fantasia
    ws.set_column(2, 2, 20)  # origem
    ws.set_column(3, 3, 14)  # porte
    ws.set_column(4, 4, 50)  # website

    ws.freeze_panes(1, 0)
    if rows:
        ws.autofilter(0, 0, len(rows), len(BD_COLUMNS) - 1)

    workbook.close()
    logger.info(f"BD Excel written: {out_path} ({len(rows)} leads)")
    return out_path
