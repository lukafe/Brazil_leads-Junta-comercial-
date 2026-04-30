"""Tests for the watchlist module."""

from pathlib import Path

import yaml

from psav.watchlist import (
    WatchedCompany,
    WatchedPerson,
    add_company,
    add_person,
    load_companies,
    load_people,
)


def test_watched_company_alias_match() -> None:
    w = WatchedCompany(razao_social_aliases=["MERCADO BITCOIN", "MB SERVICOS"])
    assert w.matches_razao("MERCADO BITCOIN HOLDING LTDA") == "MERCADO BITCOIN"
    assert w.matches_razao("Mercado Bitcoin Investimentos S.A.") == "MERCADO BITCOIN"
    assert w.matches_razao("MB SERVIÇOS DIGITAIS LTDA") == "MB SERVICOS"  # accent stripped
    assert w.matches_razao("FOXBIT LTDA") is None


def test_watched_company_empty_alias() -> None:
    w = WatchedCompany(razao_social_aliases=[])
    assert w.matches_razao("anything") is None


def test_watched_person_normalized() -> None:
    p = WatchedPerson(nome="João Cançado")
    assert p.nome_normalized == "JOAO CANCADO"


def test_load_companies_empty(tmp_path: Path) -> None:
    p = tmp_path / "empty.yaml"
    p.write_text("[]", encoding="utf-8")
    assert load_companies(p) == []


def test_load_companies_full(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        yaml.safe_dump(
            [
                {
                    "cnpj": "12.345.678/0001-90",
                    "razao_social_aliases": ["ACME"],
                    "category": "exchange",
                    "priority": "estrategico",
                }
            ]
        ),
        encoding="utf-8",
    )
    rows = load_companies(p)
    assert len(rows) == 1
    assert rows[0].cnpj == "12345678000190"
    assert rows[0].category == "exchange"
    assert rows[0].matches_razao("ACME LTDA") == "ACME"


def test_load_people_full(tmp_path: Path) -> None:
    p = tmp_path / "people.yaml"
    p.write_text(
        yaml.safe_dump(
            [
                {
                    "nome": "REINALDO RABELO",
                    "associations": ["Mercado Bitcoin (CEO)"],
                    "category": "exchange_executive",
                }
            ]
        ),
        encoding="utf-8",
    )
    rows = load_people(p)
    assert len(rows) == 1
    assert rows[0].nome == "REINALDO RABELO"
    assert rows[0].nome_normalized == "REINALDO RABELO"


def test_add_company_appends(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("[]", encoding="utf-8")
    add_company(cnpj="12345678000190", razao_alias="ACME", category="exchange", path=p)
    add_company(cnpj=None, razao_alias="BETA", path=p)
    rows = load_companies(p)
    assert len(rows) == 2
    assert rows[0].cnpj == "12345678000190"
    assert rows[1].cnpj is None


def test_add_person_appends(tmp_path: Path) -> None:
    p = tmp_path / "p.yaml"
    p.write_text("[]", encoding="utf-8")
    add_person(nome="JOAO SILVA", associations=["Acme (CTO)"], path=p)
    rows = load_people(p)
    assert len(rows) == 1
    assert rows[0].nome == "JOAO SILVA"
    assert rows[0].associations == ["Acme (CTO)"]


def test_load_companies_real_file_validates() -> None:
    """Read the real project YAML to confirm it is well-formed."""
    rows = load_companies()
    assert len(rows) >= 5  # at least 5 known companies
    # Mercado Bitcoin should be present as an alias
    assert any("MERCADO BITCOIN" in alias for r in rows for alias in r.razao_social_aliases)


def test_load_people_real_file_validates() -> None:
    rows = load_people()
    assert len(rows) >= 3
