"""Tests for cross_reference_partners()."""

from psav.ingestion.receita_dump import cross_reference_partners
from psav.watchlist import WatchedPerson


def test_cross_reference_finds_known() -> None:
    partners = [
        {"cnpj": "12345678000190", "nome": "JOAO REINALDO RABELO SILVA"},
        {"cnpj": "98765432000110", "nome": "MARIA OLIVEIRA"},
    ]
    people = [
        WatchedPerson(nome="REINALDO RABELO", associations=["MB"], category="x"),
    ]
    matches = cross_reference_partners(partners, people)
    assert len(matches) == 1
    assert matches[0]["cnpj"] == "12345678000190"
    assert matches[0]["matched_known"] == "REINALDO RABELO"


def test_cross_reference_empty() -> None:
    assert cross_reference_partners([], []) == []
    assert cross_reference_partners([{"cnpj": "1", "nome": "X"}], []) == []
    assert cross_reference_partners(
        [], [WatchedPerson(nome="REINALDO RABELO")]
    ) == []


def test_cross_reference_accent_insensitive() -> None:
    partners = [{"cnpj": "12345678000190", "nome": "JOÃO REINÁLDO RÁBELO"}]
    people = [WatchedPerson(nome="REINALDO RABELO")]
    matches = cross_reference_partners(partners, people)
    assert len(matches) == 1


def test_cross_reference_no_match() -> None:
    partners = [{"cnpj": "12345678000190", "nome": "FULANO CICLANO"}]
    people = [WatchedPerson(nome="REINALDO RABELO")]
    assert cross_reference_partners(partners, people) == []
