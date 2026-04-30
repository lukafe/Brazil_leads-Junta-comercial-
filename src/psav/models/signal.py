"""Signal model (timeline events per company)."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from psav.utils.text import clean_cnpj, is_valid_cnpj_format


class SignalType(StrEnum):
    RAZAO_SOCIAL_UPDATE = "razao_social_update"
    FUNDRAISING = "fundraising"
    SECURITY_INCIDENT = "security_incident"
    KEY_HIRE = "key_hire"
    JOB_OPENING = "job_opening"
    PUBLIC_INTENT = "public_intent"
    PARTNERSHIP = "partnership"
    REGULATORY_FILING = "regulatory_filing"
    PRODUCT_LAUNCH = "product_launch"
    MEDIA_MENTION = "media_mention"


class SignalSource(StrEnum):
    RECEITA = "receita"
    CASA_DOS_DADOS = "casa_dos_dados"
    NEWS = "news"
    LINKEDIN = "linkedin"
    WEBSITE = "website"
    MANUAL = "manual"
    BCB = "bcb"


class SignalCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    cnpj: str
    signal_type: SignalType
    signal_date: datetime
    signal_source: SignalSource
    signal_payload: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 1.0

    @field_validator("cnpj")
    @classmethod
    def _normalize_cnpj(cls, v: str) -> str:
        cleaned = clean_cnpj(v)
        if not is_valid_cnpj_format(cleaned):
            raise ValueError(f"Invalid CNPJ: {v!r}")
        return cleaned

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {v}")
        return v


class Signal(SignalCreate):
    id: UUID
    created_at: datetime
