"""Enrichment model (one row per CNPJ, upserted)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from psav.utils.text import clean_cnpj, is_valid_cnpj_format


class Enrichment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cnpj: str

    # --- Phase 2.A — BD-facing classification (Gemini-powered) ---
    website: str | None = None
    website_source: str | None = None  # "razao_social_match" | "gemini_search" | "manual"

    nome_fantasia_enriched: str | None = None  # populated only when Receita NULL
    nome_fantasia_source: str | None = None  # "receita" | "gemini_search" | "website"

    origin: Literal["brasileira", "internacional"] | None = None
    origin_parent_company: str | None = None  # e.g. "Bitso", "Bybit", "Itaú Unibanco"
    origin_evidence: str | None = None  # one-sentence justification
    origin_source_url: str | None = None

    size: Literal["small", "mid", "big"] | None = None
    size_score: int | None = None  # 0-100
    size_breakdown: dict[str, int] | None = None  # per-dimension contribution

    estimated_headcount: int | None = None
    estimated_headcount_source: str | None = None
    multinational: bool | None = None
    has_significant_funding: bool | None = None

    tech_profile: Literal["web2_with_web3", "native_web3"] | None = None
    tech_profile_evidence: str | None = None
    tech_profile_source_url: str | None = None

    # --- Phase 2.B — LinkedIn / decision-makers (reserved, not implemented yet) ---
    linkedin_company_url: str | None = None
    linkedin_employee_count: int | None = None
    linkedin_followers: int | None = None
    linkedin_industry: str | None = None

    ceo_name: str | None = None
    ceo_linkedin: str | None = None
    head_compliance_name: str | None = None
    head_compliance_linkedin: str | None = None
    head_legal_name: str | None = None
    head_legal_linkedin: str | None = None
    cto_name: str | None = None
    cto_linkedin: str | None = None

    # --- Phase 2.C — auditor / product signals ---
    auditor_atual: str | None = None
    has_crypto_product_live: bool = False
    crypto_product_url: str | None = None
    site_mentions_spsav: bool = False

    last_enriched_at: datetime | None = None
    enrichment_evidence: str | None = None  # human-readable cell for the Excel "leads" sheet

    @field_validator("cnpj")
    @classmethod
    def _normalize_cnpj(cls, v: str) -> str:
        cleaned = clean_cnpj(v)
        if not is_valid_cnpj_format(cleaned):
            raise ValueError(f"Invalid CNPJ: {v!r}")
        return cleaned

    def to_company_dict(self) -> dict[str, Any]:
        """Flatten into the column shape consumed by `excel._enrich_company_row`."""
        return self.model_dump(exclude_none=False, mode="json")
