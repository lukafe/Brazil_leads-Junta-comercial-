"""Prompt templates and JSON schemas for the four Gemini enrichment calls.

Each function returns ``(prompt_text, response_schema)``. The orchestrator
hands them to ``GeminiClient.ask_json`` which transparently runs the two-call
grounding+schema pattern.
"""

from __future__ import annotations

from typing import Any


def prompt_website(
    *,
    razao_social: str,
    cnpj_formatted: str,
    nome_fantasia: str | None,
) -> tuple[str, dict[str, Any]]:
    text = f"""You are researching a Brazilian company that registered as a virtual-asset service provider (SPSAV).

Company details:
- Corporate name (razão social): {razao_social}
- CNPJ: {cnpj_formatted}
- Existing trade name hint: {nome_fantasia or "unknown"}

Use Google Search aggressively across MULTIPLE source types:
1. Official commercial site (preferred): .com.br, .com, .io, .xyz domains.
2. LinkedIn company page (extract a website link from there).
3. Instagram bio of a company handle (extract any link).
4. Twitter/X bio for the same.
5. CNPJ.biz / casadosdados.com.br listing for that exact CNPJ.
6. Press releases / Bloomberg / Crunchbase profile (use to find their canonical URL).
7. Brazilian regulatory filings (BCB / CVM) — they sometimes link a website.

Rules for the website field:
- Return the canonical commercial URL with `https://` prefix.
- DO NOT return a LinkedIn URL, a Crunchbase URL, a directory URL, or a press article — return the destination they LINK to.
- If after thorough search the company truly has zero public web presence, return "NONE".

Rules for the nome_fantasia field:
- If LinkedIn / Instagram / website displays a commercial brand name distinct from the legal name, return that.
- If the legal corporate name strips down to a clean brand (e.g. "MERCADO BITCOIN SPSAV LTDA" → "Mercado Bitcoin"), return that clean form.
- DO NOT return "unknown", "Unknown", "NONE", "Not available", or "n/d" — if you cannot find or infer one, return an empty string "".

Cite the page URL that justified your answer.
"""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "website": {"type": "string", "description": "Canonical https URL or 'NONE'"},
            "nome_fantasia": {
                "type": "string",
                "description": "Commercial brand name (or empty string if unknown)",
            },
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "source_url": {"type": "string"},
        },
        "required": ["website", "confidence"],
    }
    return text, schema


def prompt_origin_parent(
    *,
    razao_social: str,
    cnpj_formatted: str,
    website: str | None,
) -> tuple[str, dict[str, Any]]:
    text = f"""Determine whether the Brazilian company below is BRAZILIAN-NATIVE or a SUBSIDIARY of a foreign company.

Company:
- Corporate name: {razao_social}
- CNPJ: {cnpj_formatted}
- Website: {website or "unknown"}

Definitions:
- "brasileira" = founded in Brazil with Brazilian capital and operations as its primary base. Examples: Mercado Bitcoin, Foxbit, NovaDAX.
- "internacional" = controlled by a foreign parent or operating as the Brazilian subsidiary of a foreign group. Examples: Bitso (Mexico), Bybit (Dubai), Kraken/Payward (US), Webull (US), Ripio (Argentina), Binance, Coinbase, OKX.

Use Google Search to verify ownership and answer:
1. origin: brasileira | internacional | unknown
2. parent_company: name of the foreign parent (only when internacional)
3. evidence: one sentence with the source justifying the call
4. source_url: the URL of the page that supported the answer
"""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "origin": {"type": "string", "enum": ["brasileira", "internacional", "unknown"]},
            "parent_company": {"type": "string"},
            "evidence": {"type": "string"},
            "source_url": {"type": "string"},
        },
        "required": ["origin", "evidence"],
    }
    return text, schema


def prompt_size_signals(
    *,
    razao_social: str,
    website: str | None,
    cnpj_formatted: str,
) -> tuple[str, dict[str, Any]]:
    text = f"""I need three numeric/boolean signals about this Brazilian company:

- Corporate name: {razao_social}
- CNPJ: {cnpj_formatted}
- Website: {website or "unknown"}

Use Google Search to research:
1. Estimated employee headcount. Prefer LinkedIn's 'employees' count, otherwise Crunchbase or the company's About page. Round to the nearest power-of-2 bucket if exact unknown (10, 25, 50, 100, 200, 500, 1000, ...).
2. Does the company have operations / offices in 2+ countries? (boolean)
3. Has it raised any public funding round (equity, debt, or token sale) over USD 1M? (boolean)

Cite the source URLs for each answer.
"""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "estimated_headcount": {"type": "integer"},
            "headcount_source": {"type": "string"},
            "multinational": {"type": "boolean"},
            "multinational_evidence": {"type": "string"},
            "has_significant_funding": {"type": "boolean"},
            "funding_evidence": {"type": "string"},
            "source_urls": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["multinational", "has_significant_funding"],
    }
    return text, schema


def prompt_tech_profile(
    *,
    razao_social: str,
    website: str | None,
    nome_fantasia: str | None,
) -> tuple[str, dict[str, Any]]:
    text = f"""Classify the company below as either Web2-with-Web3 or natively Web3.

Company:
- Corporate name: {razao_social}
- Trade name: {nome_fantasia or "unknown"}
- Website: {website or "unknown"}

Definitions:
- "web2_with_web3" = a traditional Web2 financial institution (bank, broker, fintech, insurer, payments processor) that created a crypto / SPSAV subsidiary as a new product line. Examples: Itaú, Santander, BTG/Mynt, XP, Inter creating SPSAV arms.
- "native_web3" = a crypto-first / Web3-native company since inception, whose core product is virtual assets. Examples: Mercado Bitcoin, Foxbit, Hashdex, Ripio, Bitso.

Use Google Search to verify the company's founding date, leadership background, and product mix on the website.

Answer with:
1. tech_profile: web2_with_web3 | native_web3 | unknown
2. evidence: one sentence with the source justification
3. source_urls: the URLs that supported the answer
"""
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "tech_profile": {
                "type": "string",
                "enum": ["web2_with_web3", "native_web3", "unknown"],
            },
            "evidence": {"type": "string"},
            "source_urls": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tech_profile", "evidence"],
    }
    return text, schema
