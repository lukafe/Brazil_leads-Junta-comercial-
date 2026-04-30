"""Pydantic-model validation — focused on CNPJ normalisation and enums."""

import pytest
from pydantic import ValidationError

from psav.models.company import CompanyCreate, GroupType, PSAVModality


class TestCompanyCreate:
    def test_normalizes_punctuated_cnpj(self) -> None:
        c = CompanyCreate(
            cnpj="12.345.678/0001-90",
            razao_social="X SPSAV LTDA",
            group_type=GroupType.B,
            source_added="receita_dump",
        )
        assert c.cnpj == "12345678000190"

    def test_rejects_invalid_cnpj(self) -> None:
        with pytest.raises(ValidationError):
            CompanyCreate(
                cnpj="123",
                razao_social="X",
                group_type=GroupType.B,
                source_added="receita_dump",
            )

    def test_modality_indefinida(self) -> None:
        c = CompanyCreate(
            cnpj="12345678000190",
            razao_social="X",
            group_type=GroupType.B,
            psav_modality=PSAVModality.INDEFINIDA,
            source_added="receita_dump",
        )
        # use_enum_values=True → compare against the string value
        assert c.psav_modality == "indefinida"
