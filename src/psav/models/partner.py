"""Partner / officer model (partners table)."""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from psav.utils.text import clean_cnpj, is_valid_cnpj_format


class PartnerCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    cnpj: str
    nome: str
    qualificacao: str | None = None
    cpf_cnpj: str | None = None
    percent_share: Decimal | None = None
    data_entrada: date | None = None

    @field_validator("cnpj")
    @classmethod
    def _normalize_cnpj(cls, v: str) -> str:
        cleaned = clean_cnpj(v)
        if not is_valid_cnpj_format(cleaned):
            raise ValueError(f"Invalid CNPJ: {v!r}")
        return cleaned


class Partner(PartnerCreate):
    id: UUID
    created_at: datetime
