"""CNAE (Brazilian industry codes) candidates for crypto / VASP operations.

Companies that provide virtual-asset services often register under these CNAE
codes instead of (or in addition to) inserting "SPSAV" into their corporate
name. The list is curated against the Brazilian crypto market as of 2026.

Source: CNAE 2.3 table (IBGE/CONCLA), validated against known players.
"""

from typing import Final

# Financial / "auxiliary financial services" CNAEs — most Brazilian crypto
# exchanges and brokers fall into one of these.
CRYPTO_ADJACENT_CNAES: Final[set[str]] = {
    "6619399",  # Other auxiliary activities to financial services, n.e.c.
    "6619302",  # Correspondents of financial institutions
    "6619301",  # Collection and credit-info services
    "6499999",  # Other financial services activities, n.e.c.
    "6422100",  # Multi-purpose banks with commercial portfolio
    "6420400",  # Holding companies of financial institutions
    "6810201",  # Real-estate management for third parties
    "6612603",  # Foreign-exchange brokers
    "6612604",  # Commodity-contract brokers
    "6612605",  # Investment agents
    "6203100",  # Development and licensing of customisable software
    "6202300",  # Custom software development
    "6311900",  # Data processing, hosting and related services
    "6204000",  # IT consulting
    "6312100",  # Web portals and content providers
}

# Keywords that flag crypto activity in the corporate name or trade name,
# even when "SPSAV" is missing. Combined with an adjacent CNAE, this is a
# strong signal. Stored uppercase to match the Receita data directly.
CRYPTO_KEYWORDS: Final[set[str]] = {
    "CRIPTO",
    "CRIPTOATIVO",
    "CRIPTOMOEDA",
    "CRYPTO",
    "BLOCKCHAIN",
    "BITCOIN",
    "WEB3",
    "WEB 3",
    "DIGITAL ASSETS",
    "DIGITAL ASSET",
    "ATIVOS VIRTUAIS",
    "ATIVO VIRTUAL",
    "TOKENIZACAO",
    "TOKENIZAÇÃO",
    "EXCHANGE",
}
