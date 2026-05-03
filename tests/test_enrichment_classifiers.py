"""Tests for the deterministic enrichment classifiers (no Gemini calls)."""

from datetime import date
from decimal import Decimal

import pytest

from psav.enrichment.classifiers import (
    SIZE_WEIGHTS,
    classify_origin_from_razao,
    classify_tech_profile_from_razao,
    compute_size_score,
    score_to_bucket,
)


# =========================================================================
# Origin classifier
# =========================================================================
class TestClassifyOrigin:
    def test_bitso_subsidiary(self) -> None:
        origin, parent, ev = classify_origin_from_razao(
            "BITSO SOCIEDADE PRESTADORA DE SERVICOS DE ATIVOS VIRTUAIS LTDA"
        )
        assert origin == "internacional"
        assert parent == "Bitso"
        assert ev and "BITSO" in ev

    def test_bybit_subsidiary(self) -> None:
        origin, parent, _ = classify_origin_from_razao("BYBIT BRASIL SPSAV LTDA")
        assert origin == "internacional"
        assert parent == "Bybit"

    def test_payward_kraken(self) -> None:
        origin, parent, _ = classify_origin_from_razao(
            "PAYWARD BRASIL SPSAV LTDA"
        )
        assert origin == "internacional"
        assert parent == "Kraken (Payward)"

    def test_brazilian_bank(self) -> None:
        origin, parent, _ = classify_origin_from_razao("ITAU SPSAV LTDA")
        assert origin == "brasileira"
        assert parent is None

    def test_santander(self) -> None:
        origin, _, _ = classify_origin_from_razao(
            "SANTANDER INVESTIMENTOS SOCIEDADE PRESTADORA DE SERVICOS DE ATIVOS VIRTUAIS SA"
        )
        assert origin == "brasileira"

    def test_btgt(self) -> None:
        origin, _, _ = classify_origin_from_razao(
            "BTGT BRASIL SOCIEDADE PRESTADORA DE SERVICOS DE ATIVOS VIRTUAIS LTDA"
        )
        assert origin == "brasileira"

    def test_unknown(self) -> None:
        origin, parent, ev = classify_origin_from_razao(
            "XYZ ALPHA SOCIEDADE PRESTADORA DE SERVICOS DE ATIVOS VIRTUAIS LTDA"
        )
        assert (origin, parent, ev) == (None, None, None)

    def test_empty(self) -> None:
        assert classify_origin_from_razao("") == (None, None, None)


# =========================================================================
# Tech-profile classifier
# =========================================================================
class TestClassifyTechProfile:
    def test_brazilian_bank_subsidiary(self) -> None:
        tech, ev = classify_tech_profile_from_razao(
            "ITAU SPSAV LTDA",
            constituicao=date(2024, 6, 1),
            parent_is_brazilian_bank=True,
        )
        assert tech == "web2_with_web3"
        assert ev is not None

    def test_crypto_keyword(self) -> None:
        tech, _ = classify_tech_profile_from_razao(
            "MERCADO BITCOIN SPSAV", constituicao=date(2018, 1, 1)
        )
        assert tech == "native_web3"

    def test_blockchain_keyword(self) -> None:
        tech, _ = classify_tech_profile_from_razao(
            "BLOCKCHAIN SOLUTIONS SPSAV LTDA", constituicao=date(2022, 1, 1)
        )
        assert tech == "native_web3"

    def test_old_company_no_keyword(self) -> None:
        tech, ev = classify_tech_profile_from_razao(
            "ALPHA INVESTIMENTOS SPSAV LTDA", constituicao=date(2010, 1, 1)
        )
        assert tech == "web2_with_web3"
        assert ev and "pre-crypto-boom" in ev

    def test_post2017_no_keyword_no_bank(self) -> None:
        tech, ev = classify_tech_profile_from_razao(
            "GENERIC HOLDINGS SPSAV LTDA", constituicao=date(2024, 6, 1)
        )
        assert (tech, ev) == (None, None)

    def test_no_constituicao(self) -> None:
        tech, ev = classify_tech_profile_from_razao(
            "GENERIC HOLDINGS SPSAV LTDA", constituicao=None
        )
        assert (tech, ev) == (None, None)


# =========================================================================
# Size score
# =========================================================================
class TestSizeScore:
    def test_weights_sum_to_100(self) -> None:
        assert sum(SIZE_WEIGHTS.values()) == 100

    def test_zero_signals(self) -> None:
        score, breakdown = compute_size_score(
            capital_social=None,
            headcount=None,
            multinational=None,
            has_significant_funding=None,
            constituicao=None,
        )
        assert score == 0
        assert all(v == 0 for v in breakdown.values())

    def test_max_signals(self) -> None:
        score, breakdown = compute_size_score(
            capital_social=Decimal("100000000"),
            headcount=500,
            multinational=True,
            has_significant_funding=True,
            constituicao=date(2010, 1, 1),
            today=date(2026, 5, 1),
        )
        assert score == 100
        assert breakdown == SIZE_WEIGHTS

    def test_capital_only_caps_at_15(self) -> None:
        score, _ = compute_size_score(
            capital_social=Decimal("283000000"),
            headcount=None,
            multinational=None,
            has_significant_funding=None,
            constituicao=None,
        )
        assert score == 15

    def test_headcount_dominates(self) -> None:
        score, _ = compute_size_score(
            capital_social=None,
            headcount=300,
            multinational=None,
            has_significant_funding=None,
            constituicao=None,
        )
        assert score == 30

    @pytest.mark.parametrize(
        ("score", "expected"),
        [(0, "small"), (29, "small"), (30, "mid"), (65, "mid"), (66, "big"), (100, "big")],
    )
    def test_buckets(self, score: int, expected: str) -> None:
        assert score_to_bucket(score) == expected

    def test_new_company_forced_small(self) -> None:
        # 30-day guardrail: even a 100-score company gets small if < 30 days old.
        result = score_to_bucket(
            100,
            constituicao=date(2026, 5, 1),
            today=date(2026, 5, 10),
        )
        assert result == "small"

    def test_old_company_score_respected(self) -> None:
        result = score_to_bucket(
            70,
            constituicao=date(2010, 1, 1),
            today=date(2026, 5, 10),
        )
        assert result == "big"
