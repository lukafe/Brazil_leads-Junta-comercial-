"""Main CLI — entrypoint `psav` (configured in pyproject.toml)."""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="PSAV Leads — CLI", no_args_is_help=True)
console = Console()

# Sub-apps
ingest_app = typer.Typer(help="Data ingestion (Receita Federal, Casa dos Dados, BCB)")
app.add_typer(ingest_app, name="ingest")

watchlist_app = typer.Typer(help="Manage watchlists (known companies + people)")
app.add_typer(watchlist_app, name="watchlist")

enrich_app = typer.Typer(
    help="Gemini-powered enrichment (origin / size / tech / nome_fantasia / website)"
)
app.add_typer(enrich_app, name="enrich")


# =========================================================================
# health
# =========================================================================
@app.command()
def hello() -> None:
    """CLI sanity check."""
    console.print("[bold green]PSAV Leads CLI ready[/bold green]")


# =========================================================================
# ingest receita-dump
# =========================================================================
@ingest_app.command("receita-dump")
def ingest_receita_dump(
    skip_download: bool = typer.Option(
        False,
        "--skip-download",
        help="Reuse files already extracted under data/receita/raw/",
    ),
    no_persist: bool = typer.Option(
        False,
        "--no-persist",
        help="Extract and log only; do not write to Supabase.",
    ),
    to_excel: Path | None = typer.Option(
        None,
        "--to-excel",
        help="Instead of Supabase, write the result to a .xlsx file (5 sheets: leads, socios, watchlist_only, partner_matches, metadata).",
    ),
    skip_partners: bool = typer.Option(
        False,
        "--skip-partners",
        help="Skip downloading and reading the Sócios (partners) files — focus on corporate names only.",
    ),
    cleanup: bool = typer.Option(
        False,
        "--cleanup",
        help="After writing the Excel, delete data/receita/zips and data/receita/raw (~30 GB).",
    ),
    use_watchlist: bool = typer.Option(
        False,
        "--use-watchlist",
        help="Include known companies (data/watchlist/companies.yaml) in the result.",
    ),
    cnae_expansive: bool = typer.Option(
        False,
        "--cnae-expansive",
        help="Extra filter by crypto-adjacent CNAE codes + crypto keywords in corporate name.",
    ),
    check_known_partners: bool = typer.Option(
        False,
        "--check-known-partners",
        help="Cross-reference Receita partners against data/watchlist/people.yaml (requires Sócios downloaded).",
    ),
) -> None:
    """Download the latest Receita Federal CNPJ dump, extract SPSAVs, and persist the result.

    Default mode: persists to Supabase. Use `--to-excel leads.xlsx` to run
    without a configured Supabase and open the result in Excel.
    """
    from psav.ingestion.receita_dump import run_full_pipeline

    summary = asyncio.run(
        run_full_pipeline(
            skip_download=skip_download,
            persist_to_db=not no_persist and to_excel is None,
            excel_out=to_excel,
            skip_partners=skip_partners,
            cleanup=cleanup,
            use_watchlist=use_watchlist,
            cnae_expansive=cnae_expansive,
            check_known_partners=check_known_partners,
        )
    )
    table = Table(title="Receita dump — summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for k, v in summary.items():
        table.add_row(k, str(v))
    console.print(table)


# =========================================================================
# stats
# =========================================================================
@app.command()
def stats() -> None:
    """Quick counters from the database."""
    from psav.db.queries import stats_summary

    s = stats_summary()
    table = Table(title="PSAV Leads — stats")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for k, v in s.items():
        table.add_row(k, str(v))
    console.print(table)


# =========================================================================
# export-top
# =========================================================================
@app.command("export-top")
def export_top(
    n: int = typer.Argument(30, help="Number of leads to export"),
    out: Path = typer.Option(Path("top_leads.csv"), "--out", "-o"),
) -> None:
    """Export the top N leads (view v_priority_leads) to CSV."""
    from psav.db.queries import fetch_priority_leads

    rows = fetch_priority_leads(limit=n)
    if not rows:
        console.print(
            "[yellow]No leads in v_priority_leads. Run `psav ingest receita-dump` first.[/yellow]"
        )
        raise typer.Exit(code=1)

    fieldnames = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
    console.print(f"[green]Exported {len(rows)} leads to {out}[/green]")


# =========================================================================
# watchlist commands
# =========================================================================
@watchlist_app.command("list")
def watchlist_list() -> None:
    """List companies and people in the watchlists."""
    from psav.watchlist import load_companies, load_people

    companies = load_companies()
    people = load_people()

    t = Table(title=f"Watchlist — companies ({len(companies)})")
    t.add_column("CNPJ")
    t.add_column("Aliases")
    t.add_column("Category")
    t.add_column("Priority")
    for c in companies:
        t.add_row(
            c.cnpj or "—",
            ", ".join(c.razao_social_aliases),
            c.category,
            c.priority,
        )
    console.print(t)

    t2 = Table(title=f"Watchlist — people ({len(people)})")
    t2.add_column("Name")
    t2.add_column("Associations")
    t2.add_column("Category")
    for p in people:
        t2.add_row(p.nome, "; ".join(p.associations), p.category)
    console.print(t2)


@watchlist_app.command("add-company")
def watchlist_add_company(
    razao_alias: str = typer.Argument(..., help="Alias to match in the corporate name (e.g. 'MERCADO BITCOIN')"),
    cnpj: str | None = typer.Option(None, "--cnpj", help="CNPJ if known (14 digits)"),
    category: str = typer.Option("other", "--category", "-c"),
    priority: str = typer.Option("ativo", "--priority", "-p"),
    notes: str = typer.Option("", "--notes", "-n"),
) -> None:
    """Add a company to the watchlist (data/watchlist/companies.yaml)."""
    from psav.watchlist import add_company

    add_company(cnpj=cnpj, razao_alias=razao_alias, category=category, priority=priority, notes=notes)
    console.print(f"[green]Added: {razao_alias} (CNPJ: {cnpj or 'unknown'})[/green]")


@watchlist_app.command("add-person")
def watchlist_add_person(
    nome: str = typer.Argument(..., help="Full name in UPPERCASE without accents"),
    association: list[str] = typer.Option([], "--assoc", "-a", help="Associated company/role (repeatable)"),
    category: str = typer.Option("other", "--category", "-c"),
    priority: str = typer.Option("ativo", "--priority", "-p"),
) -> None:
    """Add a person to the watchlist (data/watchlist/people.yaml)."""
    from psav.watchlist import add_person

    add_person(
        nome=nome, associations=list(association), category=category, priority=priority
    )
    console.print(f"[green]Added: {nome}[/green]")


# =========================================================================
# enrich excel — Gemini-powered post-extraction enrichment of top_leads.xlsx
# =========================================================================
@enrich_app.command("excel")
def enrich_excel_cmd(
    in_path: Path = typer.Option(
        Path("top_leads.xlsx"),
        "--in",
        "-i",
        help="Input .xlsx (must contain a 'leads' sheet).",
    ),
    out_path: Path = typer.Option(
        Path("top_leads_enriched.xlsx"),
        "--out",
        "-o",
        help="Output .xlsx — original sheets are preserved, leads sheet gains new columns.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Bypass cache and re-query Gemini for every CNPJ."
    ),
    only_cnpj: list[str] = typer.Option(
        [],
        "--only",
        help="Restrict enrichment to these CNPJs (debug; can be repeated).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Run deterministic classifiers only (no Gemini calls); useful for wiring tests.",
    ),
) -> None:
    """Enrich an existing top_leads.xlsx with origin / size / tech_profile / website."""
    from psav.config import get_settings
    from psav.enrichment.enricher import enrich_companies
    from psav.enrichment.excel_io import read_leads_sheet, read_other_sheets
    from psav.exporters.excel import write_excel

    settings = get_settings()
    if not dry_run and not settings.gemini_api_key:
        console.print(
            "[red]GEMINI_API_KEY is missing in .env — set it or use --dry-run.[/red]"
        )
        raise typer.Exit(code=1)

    leads = read_leads_sheet(in_path)
    other_sheets = read_other_sheets(in_path)

    # --force ⇒ skip cache READS for every CNPJ (force re-query) but still
    # WRITE successful results so the next run is cheap.
    force_set: list[str] | None = None
    if force:
        force_set = [str(c.get("cnpj", "")) for c in leads if c.get("cnpj")]

    enrichments = asyncio.run(
        enrich_companies(
            leads,
            settings,
            use_cache=True,
            force_refresh=force_set,
            only_cnpj=list(only_cnpj) if only_cnpj else None,
            dry_run=dry_run,
        )
    )

    # Merge enrichment dicts into the leads dicts.
    enriched_leads: list[dict[str, Any]] = []
    for lead in leads:
        cnpj = lead.get("cnpj")
        if cnpj and cnpj in enrichments:
            merged = {**lead, **enrichments[cnpj]}
            enriched_leads.append(merged)
        else:
            enriched_leads.append(lead)

    # Pull partners + watchlist_only + partner_matches sheets from the source xlsx.
    def _to_records(name: str) -> list[dict[str, Any]]:
        df = other_sheets.get(name)
        return df.to_dicts() if df is not None else []

    write_excel(
        companies=enriched_leads,
        partners=_to_records("socios"),
        out_path=out_path,
        source_label="receita_dump+gemini_enrichment",
        watchlist_only=None,  # already in source xlsx; we re-emit via raw rows
        partner_matches=None,
    )
    console.print(f"[green]Enrichment complete → {out_path}[/green]")
    table = Table(title="Enrichment summary")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    table.add_row("input_leads", str(len(leads)))
    table.add_row("enriched", str(len(enrichments)))
    table.add_row("dry_run", str(dry_run))
    table.add_row("cache_path", str(settings.enrichment_cache_path))
    console.print(table)


# =========================================================================
# cleanup-raw — delete Receita zips/raw to free disk space
# =========================================================================
@app.command("cleanup-raw")
def cleanup_raw_cmd(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete data/receita/zips and data/receita/raw (frees ~30 GB).

    Run after the Excel has been generated — the raw data is no longer needed.
    """
    from psav.config import get_settings
    from psav.ingestion.receita_dump import cleanup_raw

    settings = get_settings()
    base = settings.receita_dump_dir.resolve()

    if not yes:
        zips_size = sum(
            f.stat().st_size for f in (base / "zips").rglob("*") if f.is_file()
        ) if (base / "zips").exists() else 0
        raw_size = sum(
            f.stat().st_size for f in (base / "raw").rglob("*") if f.is_file()
        ) if (base / "raw").exists() else 0
        total_gb = (zips_size + raw_size) / 1024**3
        console.print(f"[yellow]Will delete {total_gb:.2f} GB from {base}[/yellow]")
        if not typer.confirm("Confirm?"):
            console.print("Cancelled.")
            raise typer.Exit(code=0)

    freed = cleanup_raw(base)
    console.print(f"[green]Freed {freed / 1024**3:.2f} GB[/green]")


if __name__ == "__main__":
    app()
