"""Ingestion of the Receita Federal CNPJ dump.

Flow:
  1. Resolve the latest month folder on arquivos.receitafederal.gov.br.
  2. Download Empresas[0-9].zip, Estabelecimentos[0-9].zip and Socios[0-9].zip in parallel.
  3. Unzip into data/receita/raw/.
  4. Extract SPSAVs with DuckDB (regex over corporate names + join with the headquarter establishment).
  5. Upsert companies + partners and create `razao_social_update` signals.

CLI: `uv run psav ingest receita-dump`.
"""

from __future__ import annotations

import asyncio
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from psav.config import get_settings
from psav.db.queries import insert_signals, upsert_companies, upsert_partners
from psav.exceptions import IngestionError, ReceitaLayoutError
from psav.ingestion.receita_layout import (
    EMPRESAS_COLS,
    EMPRESAS_GLOB,
    ESTABELE_COLS,
    ESTABELE_GLOB,
    MATRIZ,
    SITUACAO_ATIVA,
    SOCIO_PF,
    SOCIOS_COLS,
    SOCIOS_GLOB,
)
from psav.models.company import CompanyCreate, GroupType, PSAVModality
from psav.models.partner import PartnerCreate
from psav.models.signal import SignalCreate, SignalSource, SignalType
from psav.utils.logging import logger
from psav.utils.text import (
    SPSAV_PATTERN,
    is_spsav,
    mask_cpf,
    parse_br_date,
    parse_br_number,
    strip_accents,
)

## Receita Federal migrated its public open-data portal to a Nextcloud instance
## (SERPRO). Access is via WebDAV. The share token below is the public one
## advertised by the Receita; rotate it here if they ever change it.
RECEITA_SHARE_TOKEN = "gn672Ad4CF8N6TK"
WEBDAV_BASE = (
    f"https://arquivos.receitafederal.gov.br/public.php/dav/files/{RECEITA_SHARE_TOKEN}"
)
CNPJ_DIR_PATH = "/Dados/Cadastros/CNPJ"
MONTH_NAME_PATTERN = re.compile(r"^(\d{4}-\d{2})$")
TARGET_FILE_PATTERNS = (
    re.compile(r"^Empresas\d+\.zip$", re.IGNORECASE),
    re.compile(r"^Estabelecimentos\d+\.zip$", re.IGNORECASE),
    re.compile(r"^Socios\d+\.zip$", re.IGNORECASE),
)
## Receita's Nextcloud (SERPRO) is unstable and slow per connection. Strategy:
##   - one file at a time (parallelism between files = 1)
##   - each file is split into CHUNKS_PER_FILE Range requests fetched in parallel
##     → multiplies throughput without abusing the server (same total connections)
##   - short read timeout (60s), retry with long backoff (up to 120s)
##   - resumable via Range header
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)
MAX_PARALLEL_DOWNLOADS = 1
CHUNKS_PER_FILE = 4
MIN_FILE_FOR_CHUNKS = 50 * 1024 * 1024  # 50 MB — below this, splitting is not worth it
DOWNLOAD_RETRIES = 12
DOWNLOAD_BACKOFF_MAX = 120  # seconds
WEBDAV_NS = {"d": "DAV:"}

# Transient network errors that should trigger retry with backoff.
_RETRIABLE_HTTPX_EXC: tuple[type[Exception], ...] = (
    httpx.ReadError,
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteError,
    httpx.PoolTimeout,
)


# =========================================================================
# 1. Resolve the latest available month via WebDAV PROPFIND
# =========================================================================
async def _propfind(cli: httpx.AsyncClient, url: str) -> list[str]:
    """List immediate children (depth=1) of a WebDAV collection.

    Returns only the names (no path) — directories keep a trailing '/'.
    """
    import urllib.parse
    from xml.etree import ElementTree as ET

    resp = await cli.request("PROPFIND", url, headers={"Depth": "1"})
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    parsed_self = urllib.parse.urlparse(url).path
    self_path = urllib.parse.unquote(parsed_self).rstrip("/") + "/"

    names: list[str] = []
    for response in root.findall("d:response", WEBDAV_NS):
        href_el = response.find("d:href", WEBDAV_NS)
        if href_el is None or href_el.text is None:
            continue
        raw_href = urllib.parse.unquote(href_el.text)
        if raw_href.rstrip("/") + "/" == self_path:
            continue  # this is the directory itself
        rel = raw_href.removeprefix(self_path)
        # rel is something like "Empresas0.zip" or "2026-04/"
        names.append(rel)
    return names


async def resolve_latest_month_url() -> str:
    """Resolve the URL of the most recent YYYY-MM folder in /Dados/Cadastros/CNPJ via WebDAV."""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as cli:
        children = await _propfind(cli, f"{WEBDAV_BASE}{CNPJ_DIR_PATH}/")
    months = [c.rstrip("/") for c in children if MONTH_NAME_PATTERN.match(c.rstrip("/"))]
    if not months:
        raise IngestionError("No YYYY-MM folder found in the Receita Nextcloud.")
    latest = max(months)
    logger.info(f"Latest available month: {latest}")
    return f"{WEBDAV_BASE}{CNPJ_DIR_PATH}/{latest}/"


async def list_target_files(month_url: str) -> list[str]:
    """List the Empresas*/Estabelecimentos*/Socios*.zip names inside the monthly folder."""
    async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as cli:
        children = await _propfind(cli, month_url)
    files = [c for c in children if any(p.match(c) for p in TARGET_FILE_PATTERNS)]
    if not files:
        raise IngestionError(f"No Empresas*/Estabelecimentos*/Socios*.zip in {month_url}")
    return sorted(set(files))


# =========================================================================
# 2. Parallel download + unzip
# =========================================================================
async def _head_size(cli: httpx.AsyncClient, url: str) -> int | None:
    """Resolve the total file size via HEAD (falls back to a short Range GET)."""
    try:
        head = await cli.head(url)
        if head.status_code == 200 and head.headers.get("content-length"):
            return int(head.headers["content-length"])
    except _RETRIABLE_HTTPX_EXC:
        pass
    # Fallback: GET with a short Range to force a 206 + Content-Range.
    try:
        async with cli.stream("GET", url, headers={"Range": "bytes=0-0"}) as resp:
            cr = resp.headers.get("content-range", "")
            if "/" in cr:
                total = cr.rsplit("/", 1)[-1].strip()
                if total.isdigit():
                    return int(total)
            await resp.aread()
    except _RETRIABLE_HTTPX_EXC:
        pass
    return None


async def _download_range_chunk(
    cli: httpx.AsyncClient,
    url: str,
    chunk_path: Path,
    range_start: int,
    range_end: int,  # inclusive
    progress_task: TaskID,
    progress: Progress,
) -> None:
    """Download bytes [range_start, range_end] (inclusive) into `chunk_path`,
    resuming if the file already contains partial bytes. Internal retry loop."""
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        already = chunk_path.stat().st_size if chunk_path.exists() else 0
        chunk_total = range_end - range_start + 1
        if already >= chunk_total:
            return
        actual_start = range_start + already
        headers = {"Range": f"bytes={actual_start}-{range_end}"}
        try:
            async with cli.stream("GET", url, headers=headers) as resp:
                if resp.status_code == 416:
                    # server thinks we already have everything
                    return
                if resp.status_code not in (200, 206):
                    resp.raise_for_status()
                if already:
                    progress.update(progress_task, advance=already)
                with chunk_path.open("ab" if already else "wb") as fh:
                    async for piece in resp.aiter_bytes(chunk_size=1 << 16):
                        fh.write(piece)
                        progress.update(progress_task, advance=len(piece))
            return
        except _RETRIABLE_HTTPX_EXC as exc:
            last_exc = exc
            wait_s = min(2 ** (attempt - 1), DOWNLOAD_BACKOFF_MAX)
            logger.warning(
                f"{chunk_path.name}: chunk attempt {attempt}/{DOWNLOAD_RETRIES} "
                f"failed ({type(exc).__name__}). Retrying in {wait_s}s"
            )
            await asyncio.sleep(wait_s)
    raise IngestionError(f"Chunk failed after {DOWNLOAD_RETRIES} attempts: {chunk_path}") from last_exc


async def _download_one(
    cli: httpx.AsyncClient,
    url: str,
    dest: Path,
    progress: Progress,
    sem: asyncio.Semaphore,
) -> Path:
    """Download a single dump file.

    Strategy:
      - HEAD to resolve the size. If known AND large, split into N chunks
        and download them in parallel (multi-Range, à la aria2c).
      - Otherwise, fall back to a simple linear stream (with resume).
      - In both cases: retry with backoff on transient errors.
    """
    if dest.exists() and dest.stat().st_size > 0:
        logger.info(f"Already exists, skipping: {dest.name}")
        return dest

    async with sem:
        total = await _head_size(cli, url)
        if total and total >= MIN_FILE_FOR_CHUNKS:
            return await _download_chunked(cli, url, dest, total, progress)
        # Unknown size or small file → linear stream with resume.
        return await _download_streamed(cli, url, dest, progress)


async def _download_chunked(
    cli: httpx.AsyncClient,
    url: str,
    dest: Path,
    total_size: int,
    progress: Progress,
) -> Path:
    """Split into CHUNKS_PER_FILE pieces, download in parallel, then concatenate."""
    chunk_dir = dest.parent / f".{dest.name}.chunks"
    chunk_dir.mkdir(exist_ok=True)
    n = CHUNKS_PER_FILE
    chunk_size = total_size // n

    ranges: list[tuple[int, int, Path]] = []
    for i in range(n):
        start = i * chunk_size
        end = (start + chunk_size - 1) if i < n - 1 else (total_size - 1)
        ranges.append((start, end, chunk_dir / f"part{i:02d}"))

    task = progress.add_task(f"{dest.name} (x{n} chunks)", total=total_size)
    # chunks may already contain partial bytes from a previous run
    for _, _, p in ranges:
        if p.exists():
            progress.update(task, advance=p.stat().st_size)

    await asyncio.gather(
        *(
            _download_range_chunk(cli, url, p, s, e, task, progress)
            for s, e, p in ranges
        )
    )

    # Concatenate chunks into the final destination
    tmp_final = dest.with_suffix(dest.suffix + ".assembling")
    with tmp_final.open("wb") as out:
        for _, _, p in ranges:
            with p.open("rb") as src:
                while piece := src.read(1 << 20):
                    out.write(piece)
    tmp_final.rename(dest)

    # Clean up chunks
    for _, _, p in ranges:
        p.unlink(missing_ok=True)
    chunk_dir.rmdir()

    if dest.stat().st_size != total_size:
        raise IngestionError(
            f"{dest.name}: final size {dest.stat().st_size} != expected {total_size}"
        )
    return dest


async def _download_streamed(
    cli: httpx.AsyncClient,
    url: str,
    dest: Path,
    progress: Progress,
) -> Path:
    """Linear stream download with resume — used when the server hides the file size."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    last_exc: Exception | None = None
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            start_byte = tmp.stat().st_size if tmp.exists() else 0
            headers = {"Range": f"bytes={start_byte}-"} if start_byte else {}
            async with cli.stream("GET", url, headers=headers) as resp:
                if start_byte and resp.status_code == 200:
                    start_byte = 0
                    tmp.unlink(missing_ok=True)
                elif start_byte and resp.status_code == 416:
                    tmp.unlink(missing_ok=True)
                    continue
                elif resp.status_code not in (200, 206):
                    resp.raise_for_status()

                content_length = int(resp.headers.get("content-length") or 0)
                total = (start_byte + content_length) if content_length else None
                task_label = f"{dest.name}{' (retry ' + str(attempt) + ')' if attempt > 1 else ''}"
                task = progress.add_task(task_label, total=total)
                if start_byte:
                    progress.update(task, advance=start_byte)
                mode = "ab" if start_byte else "wb"
                with tmp.open(mode) as fh:
                    async for piece in resp.aiter_bytes(chunk_size=1 << 16):
                        fh.write(piece)
                        progress.update(task, advance=len(piece))
            tmp.rename(dest)
            return dest
        except _RETRIABLE_HTTPX_EXC as exc:
            last_exc = exc
            wait_s = min(2 ** (attempt - 1), DOWNLOAD_BACKOFF_MAX)
            logger.warning(
                f"{dest.name}: attempt {attempt}/{DOWNLOAD_RETRIES} "
                f"failed ({type(exc).__name__}: {exc}). Retrying in {wait_s}s"
            )
            await asyncio.sleep(wait_s)
    raise IngestionError(f"Failed after {DOWNLOAD_RETRIES} attempts: {dest.name}") from last_exc


async def download_dump(
    target_dir: Path | None = None, skip_partners: bool = False
) -> tuple[Path, list[Path]]:
    """Download Empresas/Estabelecimentos (and Socios, when skip_partners=False).

    Returns `(zip_dir, list_of_downloaded_zips)`.
    """
    settings = get_settings()
    base = (target_dir or settings.receita_dump_dir).resolve()
    zip_dir = base / "zips"
    zip_dir.mkdir(parents=True, exist_ok=True)

    month_url = await resolve_latest_month_url()
    files = await list_target_files(month_url)
    if skip_partners:
        files = [f for f in files if not f.lower().startswith("socios")]
        logger.info("--skip-partners: skipping download of Socios*.zip")
    logger.info(f"{len(files)} files to download from {month_url}")

    sem = asyncio.Semaphore(MAX_PARALLEL_DOWNLOADS)
    columns: tuple[Any, ...] = (
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
    )
    downloaded: list[Path] = []
    failures: list[tuple[str, BaseException]] = []
    # 1 file at a time x CHUNKS_PER_FILE parallel chunks = N simultaneous connections cap.
    limits = httpx.Limits(
        max_connections=CHUNKS_PER_FILE + 2,
        max_keepalive_connections=CHUNKS_PER_FILE,
    )
    async with httpx.AsyncClient(
        timeout=DOWNLOAD_TIMEOUT, follow_redirects=True, limits=limits
    ) as cli:
        with Progress(*columns) as progress:
            results = await asyncio.gather(
                *(
                    _download_one(cli, month_url + name, zip_dir / name, progress, sem)
                    for name in files
                ),
                return_exceptions=True,
            )
    for name, res in zip(files, results, strict=True):
        if isinstance(res, BaseException):
            failures.append((name, res))
            logger.error(f"❌ {name}: {type(res).__name__}: {res}")
        else:
            downloaded.append(res)
    if failures:
        raise IngestionError(
            f"{len(failures)}/{len(files)} downloads failed after retries: "
            + ", ".join(f for f, _ in failures)
        )
    return zip_dir, downloaded


def unzip_all(zip_dir: Path, raw_dir: Path) -> list[Path]:
    """Unzip every archive into raw_dir. Returns the list of extracted files."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    for zpath in sorted(zip_dir.glob("*.zip")):
        with zipfile.ZipFile(zpath) as zf:
            for member in zf.namelist():
                target = raw_dir / Path(member).name
                if target.exists() and target.stat().st_size > 0:
                    extracted.append(target)
                    continue
                with zf.open(member) as src, target.open("wb") as dst:
                    while chunk := src.read(1 << 20):
                        dst.write(chunk)
                extracted.append(target)
                logger.info(f"Extracted: {target.name}")
    return extracted


# =========================================================================
# 3. DuckDB extraction
# =========================================================================
def _column_list(cols: dict[str, int]) -> str:
    """Generate the SQL fragment that aliases the columns we care about.

    DuckDB names columns as `column0`, `column1`, ... when the CSV has fewer
    than 10 columns, and as `column00`, `column01`, ... when it has 10+
    (so lexicographic ordering matches numeric order). We adapt the padding
    to the column count.
    """
    width = 2 if len(cols) >= 10 else 1
    return ", ".join(f"column{idx:0{width}d} as {name}" for name, idx in cols.items())


def _check_column_count(con: duckdb.DuckDBPyConnection, glob: str, expected: int) -> None:
    """Fail early if the Receita Federal layout has changed."""
    try:
        row = con.execute(
            f"select * from read_csv_auto('{glob}', delim=';', header=false, "
            "encoding='utf-8', quote='\"', ignore_errors=true, sample_size=1) limit 1"
        ).fetchone()
    except duckdb.Error as e:
        raise ReceitaLayoutError(f"Failed to read {glob}: {e}") from e
    if row is None:
        raise ReceitaLayoutError(f"{glob} returned zero rows — corrupted dump?")
    if len(row) != expected:
        raise ReceitaLayoutError(
            f"{glob} has {len(row)} columns, expected {expected}. "
            "Receita layout has changed — update receita_layout.py."
        )


def extract_spsavs(
    raw_dir: Path, skip_partners: bool = False
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the dump CSVs, filter SPSAVs by corporate name, return (companies, partners).

    The SQL regex is a fast pass; a tighter Python regex (`is_spsav()`) runs
    afterwards to remove false positives (e.g. companies that contain 'SPSAV'
    as a generic acronym).

    If `skip_partners=True`, returns `(companies, [])` without reading
    `*.SOCIOCSV`.
    """
    raw_dir = raw_dir.resolve()
    if not any(raw_dir.glob(EMPRESAS_GLOB)):
        raise IngestionError(f"No {EMPRESAS_GLOB} found in {raw_dir}")
    if not any(raw_dir.glob(ESTABELE_GLOB)):
        raise IngestionError(f"No {ESTABELE_GLOB} found in {raw_dir}")

    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    con.execute("set threads=4")

    empresas_glob = str(raw_dir / EMPRESAS_GLOB)
    estabele_glob = str(raw_dir / ESTABELE_GLOB)
    socios_glob = str(raw_dir / SOCIOS_GLOB)

    _check_column_count(con, empresas_glob, len(EMPRESAS_COLS))
    _check_column_count(con, estabele_glob, len(ESTABELE_COLS))
    if any(raw_dir.glob(SOCIOS_GLOB)):
        _check_column_count(con, socios_glob, len(SOCIOS_COLS))

    # DuckDB has no native strip_accents. We accept both spellings (Ç/C,
    # SERVIÇO/SERVICO) directly in the regex and run a stricter Python
    # validation via is_spsav().
    sql_regex = (
        r"SOCIEDADE\s+PRESTADORA\s+DE\s+SERVI(C|Ç)OS?\s+DE\s+ATIVOS\s+VIRTUAIS"
        r"|\bSPSAV\b"
    )

    logger.info("Reading Empresas + filtering by SPSAV (DuckDB)...")
    empresas_rows = con.execute(f"""
        with empresas_raw as (
            select {_column_list(EMPRESAS_COLS)}
            from read_csv_auto(
                '{empresas_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        )
        select cnpj_basico, razao_social, capital_social
        from empresas_raw
        where regexp_matches(upper(razao_social), '{sql_regex}')
    """).fetchall()
    logger.info(f"  → {len(empresas_rows)} regex matches (pre-validation)")

    if not empresas_rows:
        return [], []

    # Tighter validation (the SPSAV acronym alone can produce false positives —
    # we run the Python-side regex which strips accents).
    empresas_validas = [
        {"cnpj_basico": r[0], "razao_social": r[1], "capital_social": r[2]}
        for r in empresas_rows
        if is_spsav(r[1])
    ]
    logger.info(f"  → {len(empresas_validas)} validated as SPSAV")

    if not empresas_validas:
        return [], []

    # Load the validated subset into a temp table for efficient joins.
    con.execute(
        "create or replace temp table spsav_basico as select * from (values "
        + ", ".join(f"('{e['cnpj_basico']}')" for e in empresas_validas)
        + ") t(cnpj_basico)"
    )

    logger.info("Reading Estabelecimentos (headquarters) for SPSAVs...")
    estab_cols_sql = _column_list(ESTABELE_COLS)
    estab_rows = con.execute(f"""
        with estab_raw as (
            select {estab_cols_sql}
            from read_csv_auto(
                '{estabele_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        )
        select
            cnpj_basico,
            cnpj_basico || cnpj_ordem || cnpj_dv as cnpj_full,
            nome_fantasia,
            situacao_cadastral,
            data_inicio_atividade,
            cnae_fiscal_principal,
            cnae_fiscal_secundaria,
            uf,
            municipio,
            ddd_1,
            telefone_1,
            correio_eletronico
        from estab_raw
        where identificador_matriz_filial = '{MATRIZ}'
          and cnpj_basico in (select cnpj_basico from spsav_basico)
    """).fetchall()
    logger.info(f"  → {len(estab_rows)} headquarter establishments")

    estab_by_basico: dict[str, dict[str, Any]] = {}
    for r in estab_rows:
        (
            cnpj_basico,
            cnpj_full,
            nome_fantasia,
            situacao,
            data_inicio,
            cnae_principal,
            cnae_secundaria,
            uf,
            municipio,
            ddd,
            telefone,
            email,
        ) = r
        estab_by_basico[cnpj_basico] = {
            "cnpj": cnpj_full,
            "nome_fantasia": nome_fantasia or None,
            "situacao_cadastral": situacao,
            "data_inicio_atividade": parse_br_date(data_inicio),
            "cnae_principal": cnae_principal or None,
            "cnae_secundarios": [
                c.strip() for c in (cnae_secundaria or "").split(",") if c.strip()
            ],
            "uf": uf or None,
            "municipio": municipio or None,
            "telefone": (f"({ddd}) {telefone}".strip() if ddd and telefone else (telefone or None)),
            "email": (email or None),
        }

    companies: list[dict[str, Any]] = []
    for emp in empresas_validas:
        estab = estab_by_basico.get(emp["cnpj_basico"])
        if not estab:
            logger.warning(
                f"SPSAV {emp['razao_social']} (basico {emp['cnpj_basico']}) "
                "has no headquarter establishment — skipping"
            )
            continue
        if estab["situacao_cadastral"] != SITUACAO_ATIVA:
            logger.info(
                f"SPSAV {emp['razao_social']} situation={estab['situacao_cadastral']} "
                "(not active) — including anyway, archived=false; review manually"
            )
        companies.append(
            {
                "cnpj": estab["cnpj"],
                "razao_social": emp["razao_social"],
                "nome_fantasia": estab["nome_fantasia"],
                "capital_social": parse_br_number(emp["capital_social"]),
                "data_constituicao": estab["data_inicio_atividade"],
                "data_ultima_alteracao": estab["data_inicio_atividade"],
                "cnae_principal": estab["cnae_principal"],
                "cnae_secundarios": estab["cnae_secundarios"],
                "endereco_uf": estab["uf"],
                "endereco_municipio": estab["municipio"],
                "telefone": estab["telefone"],
                "email_contato": estab["email"],
                "match_source": "spsav_regex",
                "match_details": "corporate name contains 'SPSAV' or the full legal phrase",
            }
        )

    partners: list[dict[str, Any]] = []
    if skip_partners:
        logger.info("--skip-partners: skipping Sócios read")
    elif any(raw_dir.glob(SOCIOS_GLOB)) and companies:
        logger.info("Reading Sócios for the SPSAVs...")
        socios_cols_sql = _column_list(SOCIOS_COLS)
        socios_rows = con.execute(f"""
            with socios_raw as (
                select {socios_cols_sql}
                from read_csv_auto(
                    '{socios_glob}',
                    delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                    all_varchar=true
                )
            )
            select
                cnpj_basico,
                identificador_socio,
                nome_socio_ou_razao_social,
                cnpj_cpf_socio,
                qualificacao_socio,
                data_entrada_sociedade
            from socios_raw
            where cnpj_basico in (select cnpj_basico from spsav_basico)
        """).fetchall()
        logger.info(f"  → {len(socios_rows)} partners")

        cnpj_by_basico = {
            emp["cnpj_basico"]: estab_by_basico[emp["cnpj_basico"]]["cnpj"]
            for emp in empresas_validas
            if emp["cnpj_basico"] in estab_by_basico
        }
        for r in socios_rows:
            cnpj_basico, ident_socio, nome, cpf_cnpj, qualif, data_entrada = r
            cnpj_full = cnpj_by_basico.get(cnpj_basico)
            if not cnpj_full or not nome:
                continue
            partners.append(
                {
                    "cnpj": cnpj_full,
                    "nome": nome,
                    "qualificacao": qualif or None,
                    "cpf_cnpj": (
                        mask_cpf(cpf_cnpj) if ident_socio == SOCIO_PF else (cpf_cnpj or None)
                    ),
                    "data_entrada": parse_br_date(data_entrada),
                }
            )

    return companies, partners


# =========================================================================
# 4. Persistence (companies + partners + signals)
# =========================================================================
def persist(companies: list[dict[str, Any]], partners: list[dict[str, Any]]) -> dict[str, int]:
    """Upsert into Supabase and create razao_social_update signals."""
    if not companies:
        logger.warning("Nothing to persist.")
        return {"companies": 0, "partners": 0, "signals": 0}

    company_payloads = [
        CompanyCreate(
            cnpj=c["cnpj"],
            razao_social=c["razao_social"],
            nome_fantasia=c["nome_fantasia"],
            capital_social=c["capital_social"],
            data_constituicao=c["data_constituicao"],
            data_ultima_alteracao=c["data_ultima_alteracao"],
            cnae_principal=c["cnae_principal"],
            cnae_secundarios=c["cnae_secundarios"],
            endereco_uf=c["endereco_uf"],
            endereco_municipio=c["endereco_municipio"],
            telefone=c["telefone"],
            email_contato=c["email_contato"],
            group_type=GroupType.B,
            psav_modality=PSAVModality.INDEFINIDA,
            psav_modality_source="razao_social",
            source_added="receita_dump",
        )
        for c in companies
    ]
    n_companies = upsert_companies(company_payloads)
    logger.info(f"Upsert companies: {n_companies}")

    partner_payloads = [
        PartnerCreate(
            cnpj=p["cnpj"],
            nome=p["nome"],
            qualificacao=p["qualificacao"],
            cpf_cnpj=p["cpf_cnpj"],
            data_entrada=p["data_entrada"],
        )
        for p in partners
    ]
    n_partners = upsert_partners(partner_payloads)
    logger.info(f"Upsert partners: {n_partners}")

    now = datetime.now(UTC)
    signal_payloads = [
        SignalCreate(
            cnpj=c["cnpj"],
            signal_type=SignalType.RAZAO_SOCIAL_UPDATE,
            signal_date=(
                datetime.fromisoformat(c["data_ultima_alteracao"]).replace(tzinfo=UTC)
                if c["data_ultima_alteracao"]
                else now
            ),
            signal_source=SignalSource.RECEITA,
            signal_payload={
                "razao_social": c["razao_social"],
                "uf": c["endereco_uf"],
                "capital_social": str(c["capital_social"]) if c["capital_social"] else None,
            },
            confidence=1.0,
        )
        for c in companies
    ]
    n_signals = insert_signals(signal_payloads)
    logger.info(f"Insert signals razao_social_update: {n_signals}")

    return {"companies": n_companies, "partners": n_partners, "signals": n_signals}


# =========================================================================
# 5. Orchestrated pipeline
# =========================================================================
def cleanup_raw(base_dir: Path) -> int:
    """Delete zips/ and raw/ after a successful extraction. Returns bytes freed."""
    import shutil

    freed = 0
    for sub in ("zips", "raw"):
        target = base_dir / sub
        if not target.exists():
            continue
        size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
        shutil.rmtree(target)
        freed += size
        logger.info(f"🗑️  Deleted {target} ({size / 1024**3:.2f} GB)")
    return freed


def _join_estabele_for_basicos(
    con: duckdb.DuckDBPyConnection,
    estabele_glob: str,
    basicos_seq: list[tuple[str, str, Any]],
    match_source: str,
    match_details: str,
) -> list[dict[str, Any]]:
    """JOIN against the headquarter Estabelecimentos and return dicts shaped like companies[].

    `basicos_seq`: list of (cnpj_basico, razao_social, raw_capital_social).
    """
    if not basicos_seq:
        return []
    estab_cols_sql = _column_list(ESTABELE_COLS)
    basicos_set = {b for b, _, _ in basicos_seq}
    basicos_values = ", ".join(f"('{b}')" for b in basicos_set)
    con.execute(
        "create or replace temp table _basicos_in as select * from "
        f"(values {basicos_values}) t(cnpj_basico)"
    )
    estab_rows = con.execute(f"""
        with estab_raw as (
            select {estab_cols_sql}
            from read_csv_auto(
                '{estabele_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        )
        select
            cnpj_basico,
            cnpj_basico || cnpj_ordem || cnpj_dv as cnpj_full,
            nome_fantasia, situacao_cadastral, data_inicio_atividade,
            cnae_fiscal_principal, cnae_fiscal_secundaria, uf, municipio,
            ddd_1, telefone_1, correio_eletronico
        from estab_raw
        where identificador_matriz_filial = '{MATRIZ}'
          and cnpj_basico in (select cnpj_basico from _basicos_in)
    """).fetchall()
    estab_by_basico: dict[str, dict[str, Any]] = {}
    for r in estab_rows:
        (basico, full, fant, sit, dt, cnae_p, cnae_s, uf, mun, ddd, tel, mail) = r
        estab_by_basico[basico] = {
            "cnpj": full,
            "nome_fantasia": fant or None,
            "situacao_cadastral": sit,
            "data_inicio_atividade": parse_br_date(dt),
            "cnae_principal": cnae_p or None,
            "cnae_secundarios": [c.strip() for c in (cnae_s or "").split(",") if c.strip()],
            "uf": uf or None,
            "municipio": mun or None,
            "telefone": (f"({ddd}) {tel}".strip() if ddd and tel else (tel or None)),
            "email": (mail or None),
        }

    out: list[dict[str, Any]] = []
    for basico, razao, cap in basicos_seq:
        estab = estab_by_basico.get(basico)
        if not estab:
            logger.warning(
                f"{match_source}: {razao!r} (basico {basico}) has no headquarter — skipping"
            )
            continue
        out.append({
            "cnpj": estab["cnpj"],
            "razao_social": razao,
            "nome_fantasia": estab["nome_fantasia"],
            "capital_social": parse_br_number(cap),
            "data_constituicao": estab["data_inicio_atividade"],
            "data_ultima_alteracao": estab["data_inicio_atividade"],
            "cnae_principal": estab["cnae_principal"],
            "cnae_secundarios": estab["cnae_secundarios"],
            "endereco_uf": estab["uf"],
            "endereco_municipio": estab["municipio"],
            "telefone": estab["telefone"],
            "email_contato": estab["email"],
            "match_source": match_source,
            "match_details": match_details,
        })
    return out


def extract_watchlist_candidates(
    raw_dir: Path,
    watchlist_companies: list[Any],
) -> list[dict[str, Any]]:
    """Find companies matching the watchlist (alias in corporate name OR exact CNPJ).

    Performs a JOIN with Estabelecimentos. Returns a list of companies with
    `match_source='watchlist_alias'` (or `'watchlist_cnpj'`).
    """
    if not watchlist_companies:
        return []

    raw_dir = raw_dir.resolve()
    empresas_glob = str(raw_dir / EMPRESAS_GLOB)
    estabele_glob = str(raw_dir / ESTABELE_GLOB)

    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    con.execute("set threads=4")

    # Build the WHERE clause
    where_parts: list[str] = []
    aliases_all: list[str] = []
    cnpjs_basicos: list[str] = []
    for w in watchlist_companies:
        for alias in getattr(w, "razao_social_aliases", []):
            aliases_all.append(alias.upper().replace("'", "''"))
        cnpj = getattr(w, "cnpj", None)
        if cnpj:
            cnpjs_basicos.append(cnpj[:8])
    if aliases_all:
        regex_alt = "|".join(aliases_all)
        where_parts.append(f"regexp_matches(upper(razao_social), '{regex_alt}')")
    if cnpjs_basicos:
        in_list = ", ".join(f"'{c}'" for c in set(cnpjs_basicos))
        where_parts.append(f"cnpj_basico in ({in_list})")
    if not where_parts:
        return []
    where_clause = " or ".join(where_parts)

    logger.info(f"Watchlist: searching {len(aliases_all)} aliases + {len(cnpjs_basicos)} CNPJs")
    rows = con.execute(f"""
        with empresas_raw as (
            select {_column_list(EMPRESAS_COLS)}
            from read_csv_auto(
                '{empresas_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        )
        select cnpj_basico, razao_social, capital_social
        from empresas_raw
        where {where_clause}
    """).fetchall()
    logger.info(f"  → {len(rows)} watchlist matches")

    return _join_estabele_for_basicos(
        con,
        estabele_glob,
        [(r[0], r[1], r[2]) for r in rows],
        match_source="watchlist_alias",
        match_details="corporate name or CNPJ present in the manual watchlist",
    )


def extract_cnae_expansive_candidates(raw_dir: Path) -> list[dict[str, Any]]:
    """Find companies under crypto-adjacent CNAEs whose corporate name has a crypto keyword.

    Aims to capture entities that operate virtual assets without the literal
    "SPSAV" — e.g. tokenisation issuers, exchanges with more commercial names.
    """
    from psav.ingestion.cnae_codes import CRYPTO_ADJACENT_CNAES, CRYPTO_KEYWORDS

    raw_dir = raw_dir.resolve()
    empresas_glob = str(raw_dir / EMPRESAS_GLOB)
    estabele_glob = str(raw_dir / ESTABELE_GLOB)

    con = duckdb.connect()
    con.execute("set memory_limit='4GB'")
    con.execute("set threads=4")

    cnae_list = ", ".join(f"'{c}'" for c in CRYPTO_ADJACENT_CNAES)
    keywords_regex = "|".join(re.escape(k) for k in CRYPTO_KEYWORDS)

    logger.info(
        f"CNAE expansive: searching companies with CNAE in {len(CRYPTO_ADJACENT_CNAES)} codes "
        f"+ {len(CRYPTO_KEYWORDS)} keywords"
    )
    estab_cols_sql = _column_list(ESTABELE_COLS)
    rows = con.execute(f"""
        with estab_raw as (
            select {estab_cols_sql}
            from read_csv_auto(
                '{estabele_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        ),
        empresas_raw as (
            select {_column_list(EMPRESAS_COLS)}
            from read_csv_auto(
                '{empresas_glob}',
                delim=';', header=false, encoding='utf-8', quote='"', ignore_errors=true,
                all_varchar=true
            )
        ),
        cnae_basicos as (
            select distinct cnpj_basico
            from estab_raw
            where identificador_matriz_filial = '{MATRIZ}'
              and (cnae_fiscal_principal in ({cnae_list})
                   or list_contains(string_split(cnae_fiscal_secundaria, ','),
                                    cnae_fiscal_principal))
        )
        select e.cnpj_basico, e.razao_social, e.capital_social
        from empresas_raw e
        join cnae_basicos c on c.cnpj_basico = e.cnpj_basico
        where regexp_matches(upper(e.razao_social), '{keywords_regex}')
           or regexp_matches(upper(coalesce(e.razao_social,'')), 'CRIPTO|CRYPTO|BITCOIN|BLOCKCHAIN|TOKEN|WEB3')
    """).fetchall()
    logger.info(f"  → {len(rows)} candidates via CNAE+keyword")

    return _join_estabele_for_basicos(
        con,
        estabele_glob,
        [(r[0], r[1], r[2]) for r in rows],
        match_source="cnae_expansive",
        match_details="crypto-adjacent CNAE + crypto keyword in corporate name",
    )


def cross_reference_partners(
    partners: list[dict[str, Any]],
    watchlist_people: list[Any],
) -> list[dict[str, Any]]:
    """Cross-reference partners (Receita Sócios) against the known-people list.

    Returns a list of matches: each item has cnpj, partner_name, matched_known,
    associations.
    """
    from psav.utils.text import normalize

    if not partners or not watchlist_people:
        return []
    people_norm = {normalize(p.nome): p for p in watchlist_people}
    matches: list[dict[str, Any]] = []
    for partner in partners:
        partner_nome_norm = normalize(partner.get("nome", ""))
        for known_name_norm, known in people_norm.items():
            if known_name_norm and known_name_norm in partner_nome_norm:
                matches.append({
                    "cnpj": partner["cnpj"],
                    "partner_name": partner["nome"],
                    "matched_known": known.nome,
                    "associations": known.associations,
                    "category": known.category,
                    "priority": known.priority,
                })
                break
    return matches


def find_watchlist_only(
    companies_found: list[dict[str, Any]],
    watchlist_companies: list[Any],
) -> list[Any]:
    """Identify watchlist companies that were NOT found in the extracted data.

    Returns a list of WatchedCompany (from the watchlist module) — used in the
    Excel "watchlist_only" sheet as an alert that the company has not yet
    created its SPSAV.
    """
    if not watchlist_companies:
        return []
    found_cnpjs = {c["cnpj"] for c in companies_found if c.get("cnpj")}
    found_basicos = {c["cnpj"][:8] for c in companies_found if c.get("cnpj")}
    found_razoes = [c.get("razao_social", "") for c in companies_found]
    out: list[Any] = []
    for w in watchlist_companies:
        if getattr(w, "cnpj", None) and (
            w.cnpj in found_cnpjs or w.cnpj[:8] in found_basicos
        ):
            continue
        if any(w.matches_razao(r) for r in found_razoes):
            continue
        out.append(w)
    return out


async def run_full_pipeline(
    skip_download: bool = False,
    persist_to_db: bool = True,
    excel_out: Path | None = None,
    skip_partners: bool = False,
    cleanup: bool = False,
    use_watchlist: bool = False,
    cnae_expansive: bool = False,
    check_known_partners: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    base = settings.receita_dump_dir.resolve()
    raw_dir = base / "raw"

    if skip_download:
        logger.info(f"--skip-download: using existing files in {raw_dir}")
        if not raw_dir.exists() or not any(raw_dir.iterdir()):
            raise IngestionError(f"{raw_dir} is empty. Run without --skip-download to fetch the dump.")
    else:
        zip_dir, _ = await download_dump(base, skip_partners=skip_partners)
        unzip_all(zip_dir, raw_dir)

    # Pass 1: SPSAV regex (always)
    companies, partners = extract_spsavs(raw_dir, skip_partners=skip_partners)
    logger.info(f"SPSAVs extracted (regex): {len(companies)}; partners: {len(partners)}")

    # Pass 2: known-companies watchlist (optional)
    watchlist_only_entries: list[Any] = []
    if use_watchlist:
        from psav.watchlist import load_companies as load_watchlist_companies

        watchlist = load_watchlist_companies()
        watch_companies = extract_watchlist_candidates(raw_dir, watchlist)
        # dedupe by CNPJ
        existing_cnpjs = {c["cnpj"] for c in companies}
        new_from_watch = [c for c in watch_companies if c["cnpj"] not in existing_cnpjs]
        companies.extend(new_from_watch)
        logger.info(f"Watchlist: +{len(new_from_watch)} new companies")
        watchlist_only_entries = find_watchlist_only(companies, watchlist)
        logger.info(f"Watchlist-only (in watchlist but NOT in dump): {len(watchlist_only_entries)}")

    # Pass 3: CNAE expansive (optional)
    if cnae_expansive:
        cnae_companies = extract_cnae_expansive_candidates(raw_dir)
        existing_cnpjs = {c["cnpj"] for c in companies}
        new_from_cnae = [c for c in cnae_companies if c["cnpj"] not in existing_cnpjs]
        companies.extend(new_from_cnae)
        logger.info(f"CNAE expansive: +{len(new_from_cnae)} new companies")

    # Cross-reference partners against the people watchlist (optional)
    partner_matches: list[dict[str, Any]] = []
    if check_known_partners and not skip_partners:
        from psav.watchlist import load_people as load_watchlist_people

        people = load_watchlist_people()
        partner_matches = cross_reference_partners(partners, people)
        logger.info(f"Partner matches (known executives): {len(partner_matches)}")

    summary: dict[str, Any] = {
        "companies_extracted": len(companies),
        "partners_extracted": len(partners),
        "watchlist_only": len(watchlist_only_entries),
        "partner_matches": len(partner_matches),
    }
    if excel_out is not None:
        from psav.exporters.excel import write_excel

        path = write_excel(
            companies,
            partners,
            excel_out,
            watchlist_only=watchlist_only_entries,
            partner_matches=partner_matches,
        )
        summary["excel_path"] = str(path)
    elif persist_to_db:
        summary.update(persist(companies, partners))
    else:
        logger.info("--no-persist: skipping Supabase upsert")

    if cleanup and companies:
        freed = cleanup_raw(base)
        summary["disk_freed_gb"] = round(freed / 1024**3, 2)
    elif cleanup:
        logger.warning("--cleanup skipped: zero SPSAVs extracted (raw/zips preserved)")

    return summary


# Re-exports used by tests / auxiliary CLIs
__all__ = [
    "SPSAV_PATTERN",
    "WEBDAV_BASE",
    "download_dump",
    "extract_spsavs",
    "persist",
    "resolve_latest_month_url",
    "run_full_pipeline",
    "strip_accents",
    "unzip_all",
]
