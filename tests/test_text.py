"""Tests for the text helpers."""

from decimal import Decimal

import pytest

from psav.utils.text import (
    clean_cnpj,
    is_spsav,
    is_valid_cnpj_format,
    mask_cpf,
    parse_br_date,
    parse_br_number,
    strip_accents,
)


class TestCleanCnpj:
    def test_strips_non_digits(self) -> None:
        assert clean_cnpj("12.345.678/0001-90") == "12345678000190"

    def test_already_clean(self) -> None:
        assert clean_cnpj("12345678000190") == "12345678000190"


class TestValidCnpjFormat:
    def test_valid(self) -> None:
        assert is_valid_cnpj_format("12345678000190") is True

    def test_too_short(self) -> None:
        assert is_valid_cnpj_format("123") is False

    def test_with_letters(self) -> None:
        # contains letters — after stripping non-digits, the string is too short
        assert is_valid_cnpj_format("12345678ABCD90") is False


class TestStripAccents:
    def test_basic(self) -> None:
        assert strip_accents("Serviços") == "Servicos"

    def test_capital_c_cedilla(self) -> None:
        assert strip_accents("AÇÚCAR") == "ACUCAR"


class TestParseBrNumber:
    def test_typical(self) -> None:
        assert parse_br_number("1.234.567,89") == Decimal("1234567.89")

    def test_simple_integer(self) -> None:
        assert parse_br_number("1000") == Decimal("1000")

    def test_simple_decimal(self) -> None:
        assert parse_br_number("100.50") == Decimal("100.50")

    def test_empty(self) -> None:
        assert parse_br_number("") is None
        assert parse_br_number(None) is None

    def test_garbage(self) -> None:
        assert parse_br_number("abc") is None


class TestParseBrDate:
    def test_typical(self) -> None:
        assert parse_br_date("20260315") == "2026-03-15"

    def test_zero(self) -> None:
        assert parse_br_date("0") is None
        assert parse_br_date("00000000") is None

    def test_empty(self) -> None:
        assert parse_br_date("") is None
        assert parse_br_date(None) is None

    def test_invalid_length(self) -> None:
        assert parse_br_date("2026") is None


class TestMaskCpf:
    def test_pf_format(self) -> None:
        # Receita already partially masks CPFs; we enforce consistency
        assert mask_cpf("***12345678**") == "123***78"

    def test_full_cpf(self) -> None:
        masked = mask_cpf("12345678901")
        assert masked is not None
        assert masked.startswith("123")
        assert masked.endswith("01")
        assert "***" in masked

    def test_none(self) -> None:
        assert mask_cpf(None) is None

    def test_short(self) -> None:
        # fewer than 5 digits — return as-is
        assert mask_cpf("12") == "12"


class TestIsSpsav:
    @pytest.mark.parametrize(
        "razao",
        [
            "ACME SOCIEDADE PRESTADORA DE SERVIÇOS DE ATIVOS VIRTUAIS LTDA",
            "ACME SOCIEDADE PRESTADORA DE SERVICOS DE ATIVOS VIRTUAIS LTDA",
            "Acme Sociedade Prestadora de Serviço de Ativos Virtuais SPSAV",
            "XYZ SPSAV LTDA",
        ],
    )
    def test_positive(self, razao: str) -> None:
        assert is_spsav(razao) is True

    @pytest.mark.parametrize(
        "razao",
        [
            "ACME LTDA",
            "BANCO X S.A.",
            "ACME PRESTADORA DE SERVIÇOS DIVERSOS LTDA",  # no "ATIVOS VIRTUAIS"
        ],
    )
    def test_negative(self, razao: str) -> None:
        assert is_spsav(razao) is False
