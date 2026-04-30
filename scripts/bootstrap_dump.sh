#!/usr/bin/env bash
# Wrapper script that downloads the Receita Federal CNPJ dump.
# All the actual logic (latest-month resolution, parallel multi-chunk download,
# unzip) lives in src/psav/ingestion/receita_dump.py.

set -euo pipefail
exec uv run psav ingest receita-dump "$@"
