# PSAV Leads — Brazilian Virtual-Asset Service Provider Lead Database

**A self-hosted lead intelligence pipeline for IN BCB 701 audit opportunities in Brazil.**

This repository builds a continuously-refreshed database of every Brazilian
company that has registered as a *Sociedade Prestadora de Serviços de Ativos
Virtuais* (SPSAV — Virtual-Asset Service Provider) under the new Brazilian
Central Bank crypto-asset regulation, ranks them by relevance, and exports a
ready-to-use spreadsheet for business-development outreach.

It is designed for the BD team of a security-audit firm (CertiK Brazil) that
needs to identify and prioritise the ~150-300 companies which, by **October 30,
2026**, must obtain BCB authorisation supported by an independent technical
certification — exactly the kind of audit a firm like CertiK provides.

---

## 1. Regulatory Background

In February 2026, four new instruments came into force that reshape the
Brazilian crypto market:

| Instrument | Subject |
|---|---|
| BCB Resolution 519 | Authorisation procedure for VASPs |
| BCB Resolution 520 | Constitution and operation of VASPs (defines the legal term **SPSAV** that companies must add to their corporate name) |
| BCB Resolution 521 | Foreign-exchange operations involving virtual assets |
| **IN BCB 701** | **Technical-certification requirement issued by an independent qualified firm** |
| IN BCB 704 | Authorisation procedural details |

Every existing or new VASP has until **2026-10-30** to file for authorisation,
and that filing **requires** a technical certification under IN 701. The market
estimate is ~150 SPSAVs already in flight plus an unknown number of incumbents
(banks, brokers) that will migrate. Mapping, qualifying and prioritising those
companies is the bottleneck this project removes.

---

## 2. What This Repository Does

```
┌───────────────────────────────────────────┐
│  Receita Federal (Brazilian IRS) public   │
│  CNPJ dump — 60 M companies, ~5 GB/month  │
└─────────────────────────┬─────────────────┘
                          │ download (WebDAV PROPFIND, multi-chunk)
                          ▼
┌───────────────────────────────────────────┐
│  DuckDB extraction:                       │
│   1. SPSAV regex pass                     │
│   2. Manual watchlist (known players)     │
│   3. CNAE-adjacent + crypto keywords      │
│   4. Cross-reference partners ↔ known     │
│      crypto executives                    │
└─────────────────────────┬─────────────────┘
                          │
                          ▼
┌───────────────────────────────────────────┐
│  top_leads.xlsx (5 sheets):               │
│   • leads             — ranked candidates │
│   • socios            — partners/officers │
│   • watchlist_only    — known players     │
│                         not yet in dump   │
│   • partner_matches   — known executives  │
│                         in new entities   │
│   • metadata          — generation stats  │
└───────────────────────────────────────────┘
```

The pipeline can also persist into a Supabase Postgres database (reserved for
phases 3-4 of the roadmap: scoring, daily delta ingestion via Casa dos Dados,
Slack digest).

---

## 3. Why a Multi-Pass Approach

A single regex over the public dump catches only companies that have *already*
inserted "SPSAV" or the full legal phrase into their corporate name. That misses
three important populations:

1. **Established incumbents** that are still operating under their original
   name and will create a subsidiary closer to the deadline (Mercado Bitcoin,
   Foxbit, NovaDAX, Bitybank, …).
2. **Adjacent businesses** with a different legal term but operating crypto
   services (tokenisation issuers, asset managers, FX brokers entering the
   space).
3. **Stealth subsidiaries** — a known crypto executive registers a fresh CNPJ
   under a generic name to receive the SPSAV licence later.

The pipeline addresses all three:

- **Pass 1 — SPSAV regex.** The literal compliance term (`SOCIEDADE PRESTADORA
  DE SERVIÇOS DE ATIVOS VIRTUAIS` or the acronym `SPSAV`) is searched across
  ~60 M corporate names, two-stage filter (DuckDB SQL regex → Python
  validation) to remove false positives.
- **Pass 2 — manual watchlist.** [`data/watchlist/companies.yaml`](data/watchlist/companies.yaml)
  lists ~12 known players. Each has aliases that the pipeline searches for in
  every corporate name. Players not found are flagged in the
  `watchlist_only` sheet so the BD team can still reach out before the company
  files paperwork.
- **Pass 3 — CNAE expansive filter.** Companies registered under
  crypto-adjacent CNAE codes (Brazilian industry codes — see
  [`src/psav/ingestion/cnae_codes.py`](src/psav/ingestion/cnae_codes.py)) whose
  corporate name contains crypto keywords (`cripto`, `blockchain`, `bitcoin`,
  `web3`, `digital assets`, …).
- **Pass 4 — partner cross-reference.** [`data/watchlist/people.yaml`](data/watchlist/people.yaml)
  lists known C-level executives of established crypto firms. The pipeline
  scans the `partners` (sócios/administradores) table and reports every CNPJ
  in which a known executive appears — a strong signal of a stealth subsidiary.

Each lead row carries a `match_source` column so the BD team can see *why* it
ended up in the spreadsheet.

---

## 4. Tech Stack

| Layer | Choice | Reason |
|---|---|---|
| Runtime | Python 3.12 | required by uv-managed venv |
| Dependencies | [uv](https://github.com/astral-sh/uv) | 10× faster than poetry/pip |
| Tabular | Polars + DuckDB | streaming-scan over 60 M rows on a laptop |
| HTTP | httpx (async) | parallel multi-chunk Range downloads |
| CLI | typer + rich | colourful output, sane defaults |
| Validation | pydantic v2 | enum-driven schemas, CNPJ normalisation |
| Persistence (optional) | Supabase Postgres | RLS, pg_cron, single source of truth |
| Output | xlsxwriter | five-sheet Excel ready for outreach |

---

## 5. Quick Start

### 5.1. Prerequisites (one-off)

```bash
# Python 3.12 (the system Python on macOS is usually too old)
brew install python@3.12     # or use uv to install it

# uv — Python project & dependency manager
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc

# Optional — only needed for the Supabase persistence path
brew install supabase/tap/supabase
```

### 5.2. Project setup

```bash
git clone https://github.com/lukafe/Brazil_leads-Junta-comercial-.git psav-leads
cd psav-leads
uv sync
uv run psav hello       # → "PSAV Leads CLI ready"
uv run pytest           # → all tests pass (~44)
```

### 5.3. Run the pipeline (~1-3 hours, ~5 GB download)

The simplest possible run, generating an Excel without touching a database:

```bash
uv run psav ingest receita-dump \
  --to-excel ./top_leads.xlsx \
  --skip-partners \
  --cleanup
```

To get the full power of the multi-pass extraction (recommended once you have
edited the watchlists):

```bash
uv run psav ingest receita-dump \
  --to-excel ./top_leads.xlsx \
  --use-watchlist \
  --cnae-expansive \
  --check-known-partners \
  --cleanup
```

After completion, open `top_leads.xlsx`. The disk is automatically cleaned of
the ~26 GB of raw CSVs (`--cleanup`).

---

## 6. CLI Reference

```bash
# Health check
uv run psav hello

# Ingestion (Receita Federal CNPJ dump → Excel/Supabase)
uv run psav ingest receita-dump --to-excel out.xlsx [--use-watchlist] \
                                [--cnae-expansive] [--check-known-partners] \
                                [--skip-download] [--skip-partners] [--cleanup]

# Watchlist management
uv run psav watchlist list
uv run psav watchlist add-company "MERCADO BITCOIN" --cnpj 18.213.434/0001-11 \
                                  --category exchange --priority estrategico
uv run psav watchlist add-person "REINALDO RABELO" --assoc "Mercado Bitcoin (CEO)"

# Database utilities (require Supabase configured in .env)
uv run psav stats
uv run psav export-top 30 --out leads.csv

# Free disk space
uv run psav cleanup-raw [--yes]
```

Run `--help` after any command for full option documentation.

---

## 7. Data Sources

| Source | Format | Refresh | Notes |
|---|---|---|---|
| **Receita Federal** | Public WebDAV/Nextcloud, ZIP'd CSVs (latin-1) | Monthly, ~5 GB compressed | Authoritative. Mirrored at `https://arquivos.receitafederal.gov.br/` |
| Casa dos Dados (Phase 4, optional) | REST API | Daily delta | Paid (~R$200/month) — replaces the monthly dump for hot leads |
| Anthropic API (Phase 2) | LLM | On-demand | Classifies SPSAV modality (intermediary / custodian / broker) |
| Apify (Phase 2) | LinkedIn scraper | On-demand | Enriches with C-level decision-makers |
| SerpAPI / Tavily (Phase 2) | Web search | Daily | News, partnership, fundraising signals |

For Phase 1 (this repo), only the Receita Federal dump is used — fully free.

---

## 8. Repository Layout

```
.
├── data/
│   └── watchlist/
│       ├── companies.yaml      # known players to track manually
│       └── people.yaml         # known crypto executives
├── src/psav/
│   ├── cli.py                  # typer entrypoint (psav ...)
│   ├── config.py               # pydantic-settings, reads .env
│   ├── exceptions.py           # PSAVError + sub-exceptions
│   ├── watchlist.py            # YAML load/save/match helpers
│   ├── db/                     # Supabase client + queries
│   ├── exporters/excel.py      # five-sheet xlsx writer
│   ├── ingestion/
│   │   ├── receita_dump.py     # download + DuckDB extraction (~900 LOC)
│   │   ├── receita_layout.py   # column-index map of the public dump
│   │   └── cnae_codes.py       # crypto-adjacent CNAE constants
│   ├── models/                 # pydantic models for all DB tables
│   └── utils/                  # logging, retry, text helpers
├── supabase/migrations/        # Postgres schema + view
├── tests/                      # pytest suite (~44 tests)
├── pyproject.toml              # uv-managed deps
├── .env.example                # template — copy to .env and fill in
└── README.md                   # this file
```

---

## 9. Roadmap

| Phase | Description | Status |
|---|---|---|
| **0 — Setup** | Project skeleton, configs, Postgres migrations | ✅ |
| **1 — Acquisition** | Receita dump → SPSAV detection → Excel | ✅ |
| **1.5 — Coverage** | Watchlist + CNAE expansive + partner cross-ref | ✅ |
| 2 — Enrichment | Claude-classified modality, LinkedIn decision-makers, news/jobs signals | ⏳ |
| 3 — Scoring | 0-100 score per CNPJ, tier (strategic / active / long-tail) | ⏳ |
| 4 — Live system | Casa dos Dados delta, daily Slack digest, GitHub Actions cron | ⏳ |
| 5 — Group A + dashboard | BCB authorised institutions scraper, Streamlit/Next dashboard | ⏳ |

---

## 10. Notable Engineering Decisions

- **DuckDB over pandas.** The CNPJ dump has ~60 M companies and ~70 M
  establishments. Pandas would OOM on a laptop; DuckDB streams from CSV with
  predicate push-down.
- **Multi-chunk HTTP downloads.** The Receita Federal Nextcloud frequently
  drops connections mid-stream. Each file is split into 4 parallel Range
  requests with per-chunk retry + resumable `.part` storage. ~3-4× speed-up vs
  sequential.
- **UTF-8 + `ignore_errors`.** Some Receita CSVs contain Windows-1252 stray
  bytes that break strict latin-1 validation. The reader switches to UTF-8 with
  permissive error handling — losing fewer than 0.01% of rows in practice.
- **Two-stage SPSAV regex.** The SQL regex is permissive (fast); a Python
  pass strips accents and validates each match against a tighter pattern,
  removing false positives from generic acronyms.
- **CPF masking on partners.** Receita already partial-masks PF CPFs
  (`***12345678**`); the pipeline reduces them further to `123***78` before
  any logging or storage, satisfying LGPD on PII.
- **`--cleanup` flag.** Deletes the ~26 GB of raw data after a successful
  Excel write, keeping only the ~15 KB output.

---

## 11. Configuration

Copy `.env.example` to `.env` and fill in only what you need:

```bash
# Required for the Supabase persistence path (Phase 4+) — leave empty for Excel-only
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_DB_URL=

# Phase 2 enrichment (not required for Phase 1)
ANTHROPIC_API_KEY=
APIFY_TOKEN=
SERPAPI_KEY=

# Phase 4 live system
CASA_DOS_DADOS_TOKEN=
SLACK_WEBHOOK_URL=

# Always
ENVIRONMENT=development
LOG_LEVEL=INFO
RECEITA_DUMP_DIR=./data/receita
```

For Phase 1 only the `RECEITA_DUMP_DIR` line is necessary.

---

## 12. Development

```bash
# Lint + format
uv run ruff check src tests
uv run ruff format src tests

# Type check (strict)
uv run mypy src

# Tests
uv run pytest -v

# Pre-commit hooks (one-off install)
uv run pre-commit install
```

---

## 13. License

Internal CertiK Brazil tool. All rights reserved. Not for redistribution
without explicit written permission.

---

## 14. Author

Lucas Ceccon — Business Development, CertiK Brazil.
