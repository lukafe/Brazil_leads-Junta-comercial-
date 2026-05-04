"""BD-focused slim Excel exporter — clean English layout for outreach triage.

Reads an enriched ``top_leads_enriched.xlsx`` and emits a single-sheet
workbook with the columns a BD officer actually needs:

  Legal Name | Trade Name | Origin | Size | Website | Source | Research

Design choices:
  * No emojis anywhere (per BD-team request).
  * No row background colours — only the header band is styled.
  * Trade-name and website cells are filled when verified or, when no
    verified value exists, blank — so the BD never wastes a click on a
    fabricated guess.
  * The Research column always carries a ``cnpj.biz/<digits>`` link the
    BD can use to do their own quick lookup on blanks.
  * The Source column shows ``email_domain``, ``cnpj_biz``, ``gemini``
    (with a ``(unverified)`` suffix when the URL was not cross-checked).
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
    "website_source",
    "research_link",
]

_SIZE_ORDER = {"big": 0, "mid": 1, "small": 2}
_ORIGIN_ORDER = {"brasileira": 0, "internacional": 1, None: 2}

_ORIGIN_LABEL = {
    "brasileira": "Brazilian",
    "internacional": "International",
    None: "",
}
_SIZE_LABEL = {
    "big": "Big",
    "mid": "Mid",
    "small": "Small",
    None: "",
}


def _format_source(source: str | None, verified: bool | None) -> str:
    if not source:
        return ""
    label = {
        "email_domain": "Receita email domain",
        "cnpj_biz": "cnpj.biz",
        "gemini": "Gemini search",
    }.get(source, source)
    if verified is False:
        return f"{label} (unverified)"
    return label


def write_bd_excel(in_path: Path, out_path: Path) -> Path:
    """Read enriched xlsx, write the slim BD workbook (English, no colours)."""
    df = pl.read_excel(in_path, sheet_name="leads")
    rows = df.to_dicts()

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

    # Formats — header is the only styled band.
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
    link_fmt = workbook.add_format(
        {"valign": "top", "font_color": "#2563eb", "underline": 1}
    )

    headers = [
        "Legal Name",
        "Trade Name",
        "Origin",
        "Size",
        "Website",
        "Source",
        "Research",
    ]
    for col_idx, h in enumerate(headers):
        ws.write(0, col_idx, h, header_fmt)

    for row_idx, r in enumerate(rows, start=1):
        ws.write_string(row_idx, 0, r.get("razao_social") or "", cell_fmt)
        ws.write_string(row_idx, 1, r.get("nome_fantasia") or "", cell_fmt)
        ws.write_string(row_idx, 2, _ORIGIN_LABEL.get(r.get("origin"), ""), cell_fmt)
        ws.write_string(row_idx, 3, _SIZE_LABEL.get(r.get("size"), ""), cell_fmt)

        site = r.get("website") or ""
        if site and site.startswith(("http://", "https://")):
            ws.write_url(row_idx, 4, site, link_fmt, string=site)
        else:
            ws.write_string(row_idx, 4, site, cell_fmt)

        ws.write_string(
            row_idx,
            5,
            _format_source(r.get("website_source"), r.get("website_verified")),
            cell_fmt,
        )

        research = r.get("research_link") or ""
        if research and research.startswith(("http://", "https://")):
            ws.write_url(row_idx, 6, research, link_fmt, string="Open cnpj.biz")
        else:
            ws.write_string(row_idx, 6, "", cell_fmt)

    # Column widths.
    ws.set_column(0, 0, 60)  # Legal Name
    ws.set_column(1, 1, 35)  # Trade Name
    ws.set_column(2, 2, 16)  # Origin
    ws.set_column(3, 3, 10)  # Size
    ws.set_column(4, 4, 50)  # Website
    ws.set_column(5, 5, 28)  # Source
    ws.set_column(6, 6, 16)  # Research

    ws.freeze_panes(1, 0)
    if rows:
        ws.autofilter(0, 0, len(rows), len(headers) - 1)

    workbook.close()
    logger.info(f"BD Excel written: {out_path} ({len(rows)} leads)")
    return out_path
