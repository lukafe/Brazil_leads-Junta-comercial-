"""Text helpers: normalisation, masking, Brazilian-format parsing."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation


def strip_accents(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize(s: str) -> str:
    """Uppercase + accent-stripped + collapsed whitespace."""
    return re.sub(r"\s+", " ", strip_accents(s).upper().strip())


CNPJ_DIGITS = re.compile(r"\D+")


def clean_cnpj(s: str) -> str:
    """Return only the 14 digits of a CNPJ (strips dots, slashes, dashes, etc.)."""
    return CNPJ_DIGITS.sub("", s)


def is_valid_cnpj_format(s: str) -> bool:
    """Check that the string contains exactly 14 numeric digits."""
    digits = clean_cnpj(s)
    return len(digits) == 14 and digits.isdigit()


def parse_br_number(s: str | None) -> Decimal | None:
    """Convert a Brazilian-format number ('1.234.567,89') to Decimal.
    Also accepts plain numeric strings."""
    if s is None or not str(s).strip():
        return None
    raw = str(s).strip()
    # Already in US format (e.g. when DuckDB has normalised it)
    if "," not in raw and raw.replace(".", "").replace("-", "").isdigit() and raw.count(".") <= 1:
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None
    # Brazilian format: dot as thousands separator, comma as decimal
    cleaned = raw.replace(".", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_br_date(s: str | None) -> str | None:
    """Receive YYYYMMDD (Receita format) and return ISO YYYY-MM-DD; '0' or empty → None."""
    if s is None:
        return None
    raw = str(s).strip()
    if not raw or raw == "0" or raw == "00000000":
        return None
    if len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def mask_cpf(cpf_or_cnpj: str | None) -> str | None:
    """Mask CPF/CNPJ: keep only the first 3 and last 2 digits.

    Receita already partially masks CPFs (***123456**) but we enforce
    consistency here.
    """
    if not cpf_or_cnpj:
        return None
    digits = re.sub(r"\D", "", cpf_or_cnpj)
    if not digits:
        return cpf_or_cnpj
    if len(digits) <= 5:
        return cpf_or_cnpj
    return f"{digits[:3]}***{digits[-2:]}"


SPSAV_PATTERN = re.compile(
    r"(SOCIEDADE\s+PRESTADORA\s+DE\s+SERVI(?:C|Ç)OS?\s+DE\s+ATIVOS\s+VIRTUAIS"
    r"|\bSPSAV\b"
    r"|\bS\.?\s*P\.?\s*S\.?\s*A\.?\s*V\.?\b)",
    re.IGNORECASE,
)


def is_spsav(razao_social: str) -> bool:
    """Detect corporate names that identify as SPSAV (BCB Resolution 520)."""
    return bool(SPSAV_PATTERN.search(strip_accents(razao_social)))
