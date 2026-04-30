"""Company model (companies table)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from psav.utils.text import clean_cnpj, is_valid_cnpj_format


class GroupType(StrEnum):
    A = "A"  # BCB-authorised institutions migrating to virtual assets
    B = "B"  # newly-created SPSAVs


class PSAVModality(StrEnum):
    INTERMEDIARIA = "intermediaria"
    CUSTODIANTE = "custodiante"
    CORRETORA = "corretora"
    INDEFINIDA = "indefinida"


class CompanyBase(BaseModel):
    model_config = ConfigDict(use_enum_values=True, str_strip_whitespace=True)

    cnpj: str
    razao_social: str
    nome_fantasia: str | None = None
    capital_social: Decimal | None = None
    data_constituicao: date | None = None
    data_ultima_alteracao: date | None = None
    cnae_principal: str | None = None
    cnae_secundarios: list[str] = Field(default_factory=list)
    endereco_uf: str | None = None
    endereco_municipio: str | None = None
    site: str | None = None
    email_contato: str | None = None
    telefone: str | None = None

    group_type: GroupType
    psav_modality: PSAVModality | None = None
    psav_modality_source: str | None = None

    bcb_authorized: bool = False
    bcb_authorization_type: str | None = None

    source_added: str

    @field_validator("cnpj")
    @classmethod
    def _normalize_cnpj(cls, v: str) -> str:
        cleaned = clean_cnpj(v)
        if not is_valid_cnpj_format(cleaned):
            raise ValueError(f"Invalid CNPJ: {v!r}")
        return cleaned


class CompanyCreate(CompanyBase):
    """Payload used for create/upsert."""


class Company(CompanyBase):
    """Company as read from the database."""

    first_seen_at: datetime
    last_seen_at: datetime
    archived: bool = False
