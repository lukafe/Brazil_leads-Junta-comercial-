"""Tests for the multi-source enrichment helpers (no network required)."""

from psav.enrichment.sources import (
    _format_cnpj_punct,
    _strip_legal_boilerplate,
    extract_email_domain,
    parse_cnpjbiz_data,
    research_link,
)


# =========================================================================
# Email domain extraction
# =========================================================================
class TestExtractEmailDomain:
    def test_corporate_email(self) -> None:
        assert extract_email_domain("contato@avenia.io") == "avenia.io"

    def test_corporate_com_br(self) -> None:
        assert extract_email_domain("comercial@empresa.com.br") == "empresa.com.br"

    def test_uppercase_normalised(self) -> None:
        assert extract_email_domain("CEO@FOO.IO") == "foo.io"

    def test_strip_whitespace(self) -> None:
        assert extract_email_domain("  ceo@foo.io  ") == "foo.io"

    def test_gmail_rejected(self) -> None:
        assert extract_email_domain("ceo@gmail.com") is None

    def test_hotmail_rejected(self) -> None:
        assert extract_email_domain("ceo@hotmail.com") is None

    def test_outlook_rejected(self) -> None:
        assert extract_email_domain("ceo@outlook.com") is None

    def test_yahoo_br_rejected(self) -> None:
        assert extract_email_domain("ceo@yahoo.com.br") is None

    def test_empty_or_none(self) -> None:
        assert extract_email_domain(None) is None
        assert extract_email_domain("") is None
        assert extract_email_domain("nope") is None  # no @
        assert extract_email_domain("@foo") is None  # no local part scenario

    def test_no_dot_in_domain(self) -> None:
        assert extract_email_domain("ceo@localhost") is None


# =========================================================================
# Boilerplate stripping (used by verify_website tokens)
# =========================================================================
class TestStripLegalBoilerplate:
    def test_strips_full_phrase(self) -> None:
        out = _strip_legal_boilerplate(
            "ACME SOCIEDADE PRESTADORA DE SERVIÇOS DE ATIVOS VIRTUAIS LTDA"
        )
        assert out == "ACME"

    def test_strips_acronym(self) -> None:
        assert _strip_legal_boilerplate("XYZ SPSAV LTDA") == "XYZ"

    def test_strips_sa(self) -> None:
        assert _strip_legal_boilerplate("FOOBAR S.A.") == "FOOBAR"

    def test_keeps_real_brand(self) -> None:
        assert (
            _strip_legal_boilerplate("MERCADO BITCOIN SPSAV LTDA")
            == "MERCADO BITCOIN"
        )

    def test_handles_accents(self) -> None:
        assert (
            _strip_legal_boilerplate("AÇUCAR SPSAV LTDA")
            == "ACUCAR"
        )

    def test_empty(self) -> None:
        assert _strip_legal_boilerplate("") == ""
        assert _strip_legal_boilerplate(None) == ""


# =========================================================================
# CNPJ punctuation
# =========================================================================
def test_format_cnpj_punct() -> None:
    assert _format_cnpj_punct("12345678000190") == "12.345.678/0001-90"


def test_format_cnpj_punct_invalid() -> None:
    assert _format_cnpj_punct("123") == "123"
    assert _format_cnpj_punct("") == ""


# =========================================================================
# cnpj.biz HTML parsing
# =========================================================================
class TestParseCnpjbizData:
    def test_extracts_known_fields(self) -> None:
        html = """<html><body>
        <dl>
          <dt>Nome Fantasia</dt><dd>ACME Cripto</dd>
          <dt>Site</dt><dd>https://acme.com.br</dd>
          <dt>Telefone</dt><dd>(11) 99999-9999</dd>
          <dt>E-mail</dt><dd>contato@acme.com.br</dd>
        </dl>
        </body></html>"""
        out = parse_cnpjbiz_data(html)
        assert out["nome_fantasia"] == "ACME Cripto"
        assert out["site"] == "https://acme.com.br"
        assert out["telefone"] == "(11) 99999-9999"
        assert out["email"] == "contato@acme.com.br"

    def test_rejects_placeholder_site(self) -> None:
        html = """<dl><dt>Site</dt><dd>n/a</dd></dl>"""
        assert parse_cnpjbiz_data(html)["site"] is None

    def test_empty_html(self) -> None:
        out = parse_cnpjbiz_data("")
        assert out == {
            "nome_fantasia": None,
            "site": None,
            "telefone": None,
            "email": None,
        }


# =========================================================================
# Research link
# =========================================================================
def test_research_link_valid_cnpj() -> None:
    assert research_link("12345678000190") == "https://cnpj.biz/12345678000190"


def test_research_link_invalid_cnpj() -> None:
    assert research_link("") == ""
    assert research_link("123") == ""
