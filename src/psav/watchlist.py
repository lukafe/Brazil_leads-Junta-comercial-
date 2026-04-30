"""Watchlists of known Brazilian crypto-market companies and people.

Loads `data/watchlist/companies.yaml` and `data/watchlist/people.yaml`,
exposes matching utilities to enrich the Receita Federal extraction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from psav.utils.logging import logger
from psav.utils.text import clean_cnpj, normalize

WATCHLIST_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "watchlist"
COMPANIES_PATH = WATCHLIST_DIR / "companies.yaml"
PEOPLE_PATH = WATCHLIST_DIR / "people.yaml"


@dataclass
class WatchedCompany:
    cnpj: str | None = None
    razao_social_aliases: list[str] = field(default_factory=list)
    category: str = "other"
    priority: str = "ativo"
    notes: str = ""

    @property
    def aliases_normalized(self) -> list[str]:
        return [normalize(a) for a in self.razao_social_aliases]

    def matches_razao(self, razao_social: str) -> str | None:
        """Return the matched alias (normalized form) or None."""
        norm = normalize(razao_social)
        for alias in self.aliases_normalized:
            if alias and alias in norm:
                return alias
        return None


@dataclass
class WatchedPerson:
    nome: str
    associations: list[str] = field(default_factory=list)
    category: str = "other"
    priority: str = "ativo"

    @property
    def nome_normalized(self) -> str:
        return normalize(self.nome)


def _ensure_dir() -> None:
    WATCHLIST_DIR.mkdir(parents=True, exist_ok=True)


def load_companies(path: Path | None = None) -> list[WatchedCompany]:
    p = path or COMPANIES_PATH
    if not p.exists():
        logger.warning(f"Company watchlist not found at {p}")
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    out: list[WatchedCompany] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        out.append(
            WatchedCompany(
                cnpj=clean_cnpj(entry["cnpj"]) if entry.get("cnpj") else None,
                razao_social_aliases=list(entry.get("razao_social_aliases", [])),
                category=entry.get("category", "other"),
                priority=entry.get("priority", "ativo"),
                notes=entry.get("notes", ""),
            )
        )
    return out


def load_people(path: Path | None = None) -> list[WatchedPerson]:
    p = path or PEOPLE_PATH
    if not p.exists():
        logger.warning(f"People watchlist not found at {p}")
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    out: list[WatchedPerson] = []
    for entry in raw:
        if not isinstance(entry, dict) or "nome" not in entry:
            continue
        out.append(
            WatchedPerson(
                nome=entry["nome"],
                associations=list(entry.get("associations", [])),
                category=entry.get("category", "other"),
                priority=entry.get("priority", "ativo"),
            )
        )
    return out


def add_company(
    cnpj: str | None,
    razao_alias: str,
    category: str = "other",
    priority: str = "ativo",
    notes: str = "",
    path: Path | None = None,
) -> None:
    """Append a company to the YAML, preserving existing entries."""
    p = path or COMPANIES_PATH
    _ensure_dir()
    existing: list[Any] = (
        yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else []
    ) or []
    new_entry: dict[str, Any] = {
        "razao_social_aliases": [razao_alias.upper()],
        "category": category,
        "priority": priority,
    }
    if cnpj:
        new_entry["cnpj"] = clean_cnpj(cnpj)
    if notes:
        new_entry["notes"] = notes
    existing.append(new_entry)
    p.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")


def add_person(
    nome: str,
    associations: list[str] | None = None,
    category: str = "other",
    priority: str = "ativo",
    path: Path | None = None,
) -> None:
    """Append a person to the YAML, preserving existing entries."""
    p = path or PEOPLE_PATH
    _ensure_dir()
    existing: list[Any] = (
        yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else []
    ) or []
    existing.append(
        {
            "nome": nome.upper(),
            "associations": associations or [],
            "category": category,
            "priority": priority,
        }
    )
    p.write_text(yaml.safe_dump(existing, sort_keys=False, allow_unicode=True), encoding="utf-8")
