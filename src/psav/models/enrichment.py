"""Enrichment model (one row per CNPJ, upserted)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from psav.utils.text import clean_cnpj, is_valid_cnpj_format


class Enrichment(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cnpj: str

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

    auditor_atual: str | None = None
    has_crypto_product_live: bool = False
    crypto_product_url: str | None = None
    site_mentions_spsav: bool = False

    last_enriched_at: datetime | None = None

    @field_validator("cnpj")
    @classmethod
    def _normalize_cnpj(cls, v: str) -> str:
        cleaned = clean_cnpj(v)
        if not is_valid_cnpj_format(cleaned):
            raise ValueError(f"Invalid CNPJ: {v!r}")
        return cleaned
