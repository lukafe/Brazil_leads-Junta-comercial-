"""Multi-source signal collection for trade name + website enrichment.

Each function is a pure-ish lookup against a single source. The orchestrator
in :mod:`psav.enrichment.enricher` calls them in order, then cross-validates
the candidates by fetching the URL and checking content.

Sources, in confidence order:
  1. ``extract_email_domain`` — Receita Federal email domain (deterministic).
  2. ``fetch_cnpjbiz_html`` — public scrape of cnpj.biz/<digits> for nome_fantasia + site.
  3. (LLM) Gemini grounded search — handled separately in the orchestrator.
  4. ``verify_website`` — fetch the candidate URL, check it mentions the CNPJ
     or razão social tokens (so we don't ship a wrong site to a BD).
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from psav.utils.logging import logger
from psav.utils.text import normalize, strip_accents

# Email providers we should NOT treat as a corporate domain.
GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "yahoo.com",
        "yahoo.com.br",
        "ymail.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "uol.com.br",
        "bol.com.br",
        "ig.com.br",
        "terra.com.br",
        "globo.com",
        "globomail.com",
        "r7.com",
        "protonmail.com",
        "proton.me",
        "tutanota.com",
        "zoho.com",
        "aol.com",
    }
)

# Wider HTTP timeout: cnpj.biz and small SPSAV websites can be slow.
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


# =========================================================================
# Layer 1 — Email domain (Receita)
# =========================================================================
def extract_email_domain(email: str | None) -> str | None:
    """Return the corporate domain of an email, or None if generic / missing.

    Returns the bare domain (no scheme). The caller decides whether to prepend
    ``https://`` and verify.
    """
    if not email or "@" not in email:
        return None
    _local, _, domain = email.strip().lower().rpartition("@")
    if not domain or "." not in domain:
        return None
    if domain in GENERIC_EMAIL_DOMAINS:
        return None
    # Some Receita emails carry trailing whitespace or weird chars.
    domain = re.sub(r"[^a-z0-9.\-]", "", domain).strip(".")
    if not domain or domain.count(".") < 1:
        return None
    return domain


# =========================================================================
# Layer 3 — cnpj.biz public page scrape
# =========================================================================
_CNPJBIZ_FIELD_RE = re.compile(
    r"<dt[^>]*>\s*(?P<key>[^<]+?)\s*</dt>\s*<dd[^>]*>\s*(?P<val>[^<]*?)\s*</dd>",
    re.IGNORECASE | re.DOTALL,
)


def fetch_cnpjbiz_html(cnpj_digits: str, *, timeout: httpx.Timeout = HTTP_TIMEOUT) -> str | None:
    """Fetch the cnpj.biz HTML page for a CNPJ. Returns the body or None on failure.

    The page is public and has been stable for years. We mirror a regular
    browser User-Agent to avoid being blocked.
    """
    if not cnpj_digits or len(cnpj_digits) != 14:
        return None
    url = f"https://cnpj.biz/{cnpj_digits}"
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
        )
    except (httpx.HTTPError, OSError) as e:
        logger.debug(f"cnpj.biz fetch failed for {cnpj_digits}: {e}")
        return None
    if resp.status_code != 200 or not resp.text:
        return None
    return resp.text


def parse_cnpjbiz_data(html: str) -> dict[str, str | None]:
    """Extract structured fields from a cnpj.biz HTML page.

    Returns dict with keys: ``nome_fantasia``, ``site``, ``telefone``, ``email``.
    All values may be None.
    """
    out: dict[str, str | None] = {
        "nome_fantasia": None,
        "site": None,
        "telefone": None,
        "email": None,
    }
    if not html:
        return out
    pairs = {
        normalize(m.group("key")): m.group("val").strip()
        for m in _CNPJBIZ_FIELD_RE.finditer(html)
    }
    out["nome_fantasia"] = (
        pairs.get("NOME FANTASIA")
        or pairs.get("FANTASIA")
        or None
    )
    site = pairs.get("SITE") or pairs.get("WEBSITE") or pairs.get("URL")
    if site and site.lower() not in {"-", "n/a", "n/d", "nao informado", "não informado", ""}:
        out["site"] = site
    out["telefone"] = pairs.get("TELEFONE") or None
    out["email"] = pairs.get("E-MAIL") or pairs.get("EMAIL") or None
    return out


def fetch_cnpjbiz_data(cnpj_digits: str) -> dict[str, str | None]:
    """Convenience: fetch + parse cnpj.biz in one call."""
    html = fetch_cnpjbiz_html(cnpj_digits)
    if html is None:
        return {"nome_fantasia": None, "site": None, "telefone": None, "email": None}
    return parse_cnpjbiz_data(html)


# =========================================================================
# Layer 4 — Cross-validation by fetching the candidate website
# =========================================================================
def verify_website(
    url: str,
    *,
    cnpj_digits: str,
    razao_social: str,
    nome_fantasia: str | None = None,
    timeout: httpx.Timeout = HTTP_TIMEOUT,
) -> tuple[bool, str]:
    """Fetch the candidate URL and check its content matches the company.

    A page is considered a match when ANY of these conditions is true:
      * The page HTML contains the CNPJ digits (with or without punctuation).
      * The page HTML contains a normalised substring of the razão social
        (after stripping the legal-form suffix and SPSAV boilerplate).
      * The page HTML contains the nome_fantasia (when provided).

    Returns ``(matched, reason)`` so the caller can record evidence.
    """
    if not url:
        return False, "empty url"
    try:
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url.lstrip("/")
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "pt-BR,pt;q=0.9"},
        )
    except (httpx.HTTPError, OSError) as e:
        return False, f"fetch_failed: {type(e).__name__}"

    # 401/403 → the site exists and is protected by anti-bot. Real spam/dead
    # domains usually 404 or timeout. We treat protected sites as a soft pass
    # so legitimate-but-anti-bot company pages aren't dropped.
    if resp.status_code in (401, 403):
        return True, f"http_{resp.status_code}_protected"
    if resp.status_code >= 400:
        return False, f"http_{resp.status_code}"
    body = resp.text or ""
    if not body:
        return False, "empty_body"
    body_norm = normalize(body)

    # 1) CNPJ match (digits with or without dots/dashes/slash).
    if cnpj_digits and (
        cnpj_digits in body
        or _format_cnpj_punct(cnpj_digits) in body
    ):
        return True, "cnpj_match"

    # 2) Razão social token match — strip legal boilerplate and SPSAV.
    rz_norm = _strip_legal_boilerplate(razao_social)
    rz_tokens = [t for t in rz_norm.split() if len(t) >= 4]
    if rz_tokens:
        rz_substr = " ".join(rz_tokens[: min(3, len(rz_tokens))])
        if rz_substr and rz_substr in body_norm:
            return True, f"razao_token_match:{rz_substr}"

    # 3) Nome fantasia match — fully normalised.
    if nome_fantasia:
        nf_norm = normalize(nome_fantasia)
        if len(nf_norm) >= 4 and nf_norm in body_norm:
            return True, f"nome_fantasia_match:{nf_norm}"

    return False, "no_signal_in_homepage"


def _format_cnpj_punct(digits: str) -> str:
    if not digits or len(digits) != 14:
        return digits or ""
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


_LEGAL_BOILERPLATE_RE = re.compile(
    r"\b(?:"
    r"SOCIEDADE\s+PRESTADORA\s+DE\s+SERVI(?:C|Ç)OS?\s+DE\s+ATIVOS\s+VIRTUAIS"
    r"|S(?:\.|\s)?P(?:\.|\s)?S(?:\.|\s)?A(?:\.|\s)?V"
    r"|SPSAV"
    r"|LTDA\.?"
    r"|LIMITADA"
    r"|S\.?A\.?|S/A"
    r"|EIRELI"
    r"|EPP"
    r"|ME"
    r")\b",
    re.IGNORECASE,
)


def _strip_legal_boilerplate(razao_social: str | None) -> str:
    """Strip 'SOCIEDADE PRESTADORA DE SERVIÇOS DE ATIVOS VIRTUAIS', 'SPSAV',
    'LTDA', 'S/A', etc. Used only for token-matching during verification —
    NOT for filling nome_fantasia (which we want from a high-confidence source).
    """
    if not razao_social:
        return ""
    cleaned = _LEGAL_BOILERPLATE_RE.sub(" ", strip_accents(razao_social))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -,.")
    return cleaned.upper()


# =========================================================================
# Public helper — assemble candidates per CNPJ
# =========================================================================
def collect_candidates(
    *,
    cnpj_digits: str,
    razao_social: str,
    receita_email: str | None,
    receita_nome_fantasia: str | None,
    gemini_website: str | None,
    gemini_nome_fantasia: str | None,
) -> dict[str, dict[str, Any]]:
    """Aggregate candidate (website, nome_fantasia) pairs from each source.

    Returns ``{source_name: {"website": ..., "nome_fantasia": ...}}``. Sources
    with nothing useful are omitted.
    """
    out: dict[str, dict[str, Any]] = {}

    # Layer 1 — Receita email domain (deterministic, very high confidence).
    domain = extract_email_domain(receita_email)
    if domain:
        out["email_domain"] = {"website": f"https://{domain}", "nome_fantasia": None}

    # Layer 2 — Gemini search (already performed by caller).
    if gemini_website:
        out["gemini"] = {
            "website": gemini_website,
            "nome_fantasia": gemini_nome_fantasia,
        }

    # Note: cnpj.biz is blocked by Cloudflare to programmatic access (403),
    # but works fine in a browser — so we expose it via ``research_link()``
    # for BDs to click, instead of scraping it server-side.

    # Layer 0 — Receita's own nome_fantasia (already collected, just re-emit).
    if receita_nome_fantasia:
        out["receita"] = {"website": None, "nome_fantasia": receita_nome_fantasia}

    return out


def research_link(cnpj_digits: str) -> str:
    """Public URL where a BD can do their own quick research on a CNPJ."""
    if not cnpj_digits or len(cnpj_digits) != 14:
        return ""
    return f"https://cnpj.biz/{cnpj_digits}"
