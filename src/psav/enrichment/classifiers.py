"""Deterministic, rule-based classifiers for the enrichment pass.

These run BEFORE Gemini so we save quota. Pure functions, easy to test.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from psav.utils.text import normalize

# =========================================================================
# Constants — known players whose corporate name reveals their parent
# =========================================================================

# Foreign exchanges and crypto firms with BR subsidiaries.
# Match keys must already be normalised (uppercase, accent-stripped).
INTL_PARENTS: dict[str, str] = {
    "BITSO": "Bitso",
    "BYBIT": "Bybit",
    "PAYWARD": "Kraken (Payward)",
    "WEBULL": "Webull",
    "RIPIO": "Ripio",
    "BINANCE": "Binance",
    "COINBASE": "Coinbase",
    "OKX": "OKX",
    "OKCOIN": "OKX",
    "GEMINI": "Gemini Trust",
    "CRYPTO.COM": "Crypto.com",
    "KUCOIN": "KuCoin",
    "HUOBI": "Huobi",
    "BITGET": "Bitget",
    "FALCONX": "FalconX",
    "KEYROCK": "Keyrock",
    "FIREBLOCKS": "Fireblocks",
    "GALAXY DIGITAL": "Galaxy Digital",
    "GENESIS": "Genesis Global",
    "BITGO": "BitGo",
    "ANCHORAGE": "Anchorage Digital",
    "BTCBOX": "BTCBOX (JP)",
    "BITSTAMP": "Bitstamp",
    "EL DORADO": "El Dorado (LATAM)",
    "OSL": "OSL (HK)",
    # additional aliases
    "MAMORU": "Mamoru (JP)",
    "ZIMBA": "Zimba",
    "MASTERPAY": "Masterpay",
}

# Brazilian banks / brokers / fintechs that operate Web2 services and
# may create SPSAV subsidiaries → web2_with_web3.
BR_BANKS_BROKERS: set[str] = {
    "ITAU",
    "ITAU UNIBANCO",
    "SANTANDER",
    "BTG",
    "BTG PACTUAL",
    "BTGT",  # observed in the dataset (BTGT BRASIL)
    "XP",
    "INTER",
    "BANCO INTER",
    "BRADESCO",
    "NUBANK",
    "MERCADO LIVRE",
    "MERCADO PAGO",
    "C6",
    "C6 BANK",
    "PICPAY",
    "PAGSEGURO",
    "STONE",
    "SAFRA",
    "BANCO ORIGINAL",
    "MODAL",
    "GENIAL",
    "CAIXA",
    "BANCO DO BRASIL",
    "VOTORANTIM",
    "DAYCOVAL",
    "NEON",
    "WILL BANK",
}

# Crypto-explicit keywords on the corporate name → native_web3.
CRYPTO_NATIVE_KEYWORDS: tuple[str, ...] = (
    "BITCOIN",
    "BTC",
    "CRYPTO",
    "CRIPTO",
    "BLOCKCHAIN",
    "TOKEN",
    "WEB3",
    "WEB 3",
    "DEFI",
    "DIGITAL ASSETS",
    "DIGITAL ASSET",
    "VIRTUAL ASSETS",
    "VIRTUAL ASSET",
    "DEX",
    "EXCHANGE",
)

_CRYPTO_KEYWORD_REGEX = re.compile(
    r"\b(?:" + "|".join(re.escape(k) for k in CRYPTO_NATIVE_KEYWORDS) + r")\b",
    flags=re.IGNORECASE,
)

# "BRASIL" / "BRAZIL" / "(BR)" tokens in the corporate name. Used as a
# best-effort fallback when no foreign-parent token matched.
_BR_NAME_TOKEN = re.compile(r"\b(?:BRASIL|BRAZIL|\(BR\))\b", re.IGNORECASE)


# =========================================================================
# Origin classifier
# =========================================================================
def classify_origin_from_razao(
    razao_social: str,
) -> tuple[str | None, str | None, str | None]:
    """Try to classify origin (brasileira / internacional) purely from corporate name.

    Returns ``(origin, parent_company, evidence)`` or ``(None, None, None)`` when
    the rule-based classifier cannot decide.
    """
    if not razao_social:
        return None, None, None

    norm = normalize(razao_social)

    # 1) Foreign parent — strongest signal.
    for key, parent in INTL_PARENTS.items():
        if key in norm:
            return (
                "internacional",
                parent,
                f"Corporate name contains foreign-parent token '{key}' (mapped to {parent})",
            )

    # 2) Brazilian bank / broker — strong signal of brasileira-with-web2 origin.
    for key in BR_BANKS_BROKERS:
        if key in norm:
            return (
                "brasileira",
                None,
                f"Corporate name contains Brazilian institution token '{key}'",
            )

    # 3) "BRASIL" / "BRAZIL" / "(BR)" token — best-effort fallback. We only
    # reach this branch when no foreign-parent token matched (so any "BRASIL"
    # in the name is most likely the country, not a coincidental substring).
    if _BR_NAME_TOKEN.search(norm):
        return (
            "brasileira",
            None,
            "Corporate name contains 'BRASIL/BRAZIL/(BR)' token (no foreign-parent match)",
        )

    return None, None, None


# =========================================================================
# Tech-profile classifier
# =========================================================================
def classify_tech_profile_from_razao(
    razao_social: str,
    *,
    constituicao: date | None = None,
    parent_is_brazilian_bank: bool = False,
    is_international: bool = False,
) -> tuple[str | None, str | None]:
    """Try to classify tech profile from the corporate name + age + parent flag.

    Returns ``(tech_profile, evidence)`` or ``(None, None)`` when ambiguous.

    The "pre-2017 → Web2 incumbent" heuristic is INTENTIONALLY skipped for
    international companies: a Brazilian subsidiary of a foreign crypto-native
    parent (Ripio, OSL, …) may have incorporated locally before 2017 even
    though the parent is Web3-native. In those cases we leave the call to
    Gemini.
    """
    if parent_is_brazilian_bank:
        return (
            "web2_with_web3",
            "Subsidiary of a Brazilian bank/broker (traditional Web2 financial)",
        )

    if razao_social and _CRYPTO_KEYWORD_REGEX.search(normalize(razao_social)):
        return (
            "native_web3",
            "Corporate name contains a crypto-native keyword (BITCOIN/CRYPTO/BLOCKCHAIN/...)",
        )

    if is_international:
        # Foreign-parent SPSAVs need a Gemini look-up — the BR incorporation
        # date does not reflect the parent's true tech heritage.
        return None, None

    if constituicao and constituicao.year >= 2017:
        # Companies founded after the post-2017 crypto boom that don't match
        # bank/keyword rules — leave for Gemini.
        return None, None

    if constituicao and constituicao.year < 2017 and razao_social:
        # Old Brazilian company, no crypto keyword, no bank match → most
        # likely a Web2 incumbent.
        return (
            "web2_with_web3",
            f"Founded in {constituicao.year} (pre-crypto-boom), no native-Web3 naming",
        )

    return None, None


# =========================================================================
# Holistic size score
# =========================================================================
SIZE_WEIGHTS: dict[str, int] = {
    "capital_social": 15,
    "headcount": 30,
    "footprint": 20,
    "funding": 15,
    "age_activity": 20,
}
assert sum(SIZE_WEIGHTS.values()) == 100, "Size weights must sum to 100"


def _capital_points(capital: Decimal | float | int | None) -> int:
    """0..15 based on declared capital social (Brazilian companies notoriously
    declare token amounts, so we cap influence at 15%)."""
    if capital is None:
        return 0
    cap = float(capital)
    if cap >= 50_000_000:  # R$50M+
        return 15
    if cap >= 10_000_000:  # R$10M+
        return 10
    if cap >= 1_000_000:  # R$1M+
        return 5
    return 0


def _headcount_points(headcount: int | None) -> int:
    """0..30 — strongest single signal of size."""
    if headcount is None:
        return 0
    if headcount >= 200:
        return 30
    if headcount >= 50:
        return 20
    if headcount >= 10:
        return 10
    return 0


def _footprint_points(multinational: bool | None) -> int:
    """0..20 — operates in 2+ countries (or has multi-region BR offices)."""
    if multinational is True:
        return 20
    return 0


def _funding_points(has_significant_funding: bool | None) -> int:
    """0..15 — any public funding event > USD 1M (equity, debt, or token)."""
    if has_significant_funding is True:
        return 15
    return 0


def _age_activity_points(constituicao: date | None, today: date | None = None) -> int:
    """0..20 — reflects how long the company has been operating."""
    if constituicao is None:
        return 0
    today = today or datetime.now().date()
    days = (today - constituicao).days
    if days >= 365 * 3:  # 3+ years
        return 20
    if days >= 365:  # 1-3 years
        return 10
    if days >= 180:  # 6-12 months
        return 5
    return 0


def compute_size_score(
    *,
    capital_social: Decimal | float | int | None,
    headcount: int | None,
    multinational: bool | None,
    has_significant_funding: bool | None,
    constituicao: date | None,
    today: date | None = None,
) -> tuple[int, dict[str, int]]:
    """Compute a 0-100 holistic size score and return ``(total, breakdown)``."""
    breakdown = {
        "capital_social": _capital_points(capital_social),
        "headcount": _headcount_points(headcount),
        "footprint": _footprint_points(multinational),
        "funding": _funding_points(has_significant_funding),
        "age_activity": _age_activity_points(constituicao, today),
    }
    return sum(breakdown.values()), breakdown


def score_to_bucket(
    score: int,
    constituicao: date | None = None,
    today: date | None = None,
) -> str:
    """Map score → ``small`` / ``mid`` / ``big``.

    Guardrail: if the company is younger than 30 days, force ``small`` —
    a freshly-incorporated SPSAV should be treated as a small lead even
    when the parent is a major bank (the parent is the actual prospect).
    """
    today = today or datetime.now().date()
    if constituicao is not None:
        days_old = (today - constituicao).days
        if days_old < 30:
            return "small"
    if score >= 66:
        return "big"
    if score >= 30:
        return "mid"
    return "small"
