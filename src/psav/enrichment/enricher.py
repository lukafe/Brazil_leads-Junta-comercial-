"""Enrichment orchestrator — combines deterministic + Gemini passes per CNPJ."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from psav.config import Settings
from psav.enrichment import cache as cache_mod
from psav.enrichment import classifiers
from psav.enrichment import prompts as prompt_mod
from psav.enrichment.gemini_client import GeminiClient
from psav.exceptions import GeminiQuotaExceeded
from psav.utils.logging import logger
from psav.utils.text import clean_cnpj


class _GeminiClientLike(Protocol):
    """Subset of :class:`GeminiClient` used by the orchestrator (for testability)."""

    @property
    def model(self) -> str: ...

    async def ask_json(
        self,
        prompt: str,
        schema: dict[str, Any],
        *,
        use_search: bool = True,
    ) -> tuple[dict[str, Any], list[str]]: ...


# =========================================================================
# Helpers
# =========================================================================
def _format_cnpj(cnpj_digits: str) -> str:
    if not cnpj_digits or len(cnpj_digits) != 14:
        return cnpj_digits or ""
    c = cnpj_digits
    return f"{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}"


def _parse_iso_date(val: Any) -> Any:
    """Best-effort parse of an ISO date string into ``datetime.date``."""
    if not val:
        return None
    if hasattr(val, "year"):  # already a date or datetime
        return val.date() if hasattr(val, "date") else val
    try:
        return datetime.fromisoformat(str(val)).date()
    except (ValueError, TypeError):
        return None


_VERTEX_REDIRECT_HOSTS = (
    "vertexaisearch.cloud.google.com",
    "vertexaisearch.cloud.google.com/grounding-api-redirect",
)


def _clean_website(raw: str | None) -> str | None:
    """Normalise + reject Gemini grounding-redirect URLs.

    Gemini sometimes emits a vertex AI grounding-redirect URL instead of the
    underlying destination — those URLs are time-limited and useless to a BD.
    """
    if not raw:
        return None
    url = raw.strip()
    if not url or url.upper() == "NONE":
        return None
    lowered = url.lower()
    if any(host in lowered for host in _VERTEX_REDIRECT_HOSTS):
        return None
    # Reject obviously wrong schemes (mailto:, tel:, etc.).
    if "://" in url and not lowered.startswith(("http://", "https://")):
        return None
    # Add https:// when only a bare domain was returned.
    if not lowered.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    return url


def _build_evidence_cell(enrichment: dict[str, Any]) -> str:
    """Concatenate the per-field evidence strings into one Excel cell."""
    parts: list[str] = []
    if enrichment.get("origin"):
        parts.append(
            f"origin={enrichment['origin']}"
            + (f" ({enrichment['origin_parent_company']})"
               if enrichment.get("origin_parent_company") else "")
            + (f": {enrichment['origin_evidence']}"
               if enrichment.get("origin_evidence") else "")
        )
    if enrichment.get("tech_profile"):
        parts.append(
            f"tech={enrichment['tech_profile']}"
            + (f": {enrichment['tech_profile_evidence']}"
               if enrichment.get("tech_profile_evidence") else "")
        )
    if enrichment.get("size_breakdown"):
        breakdown = enrichment["size_breakdown"]
        breakdown_str = ", ".join(f"{k}={v}" for k, v in breakdown.items())
        parts.append(
            f"size={enrichment.get('size')}"
            f" (score={enrichment.get('size_score')}, {breakdown_str})"
        )
    if enrichment.get("estimated_headcount"):
        parts.append(
            f"headcount~{enrichment['estimated_headcount']}"
            + (f" via {enrichment['estimated_headcount_source']}"
               if enrichment.get("estimated_headcount_source") else "")
        )
    return " | ".join(parts)


# =========================================================================
# Per-CNPJ pipeline
# =========================================================================
async def _enrich_one(
    company: dict[str, Any],
    *,
    client: _GeminiClientLike | None,
    dry_run: bool,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Enrich a single company. Returns the enrichment dict (always — fields may be None on failure)."""
    cnpj = clean_cnpj(str(company.get("cnpj", "")))
    razao = company.get("razao_social") or ""
    nome_fant_existing = company.get("nome_fantasia") or None
    capital = company.get("capital_social")
    if capital is not None and not isinstance(capital, (Decimal, float, int)):
        try:
            capital = Decimal(str(capital))
        except Exception:
            capital = None
    constituicao = _parse_iso_date(company.get("data_constituicao"))

    enrichment: dict[str, Any] = {
        "cnpj": cnpj,
        "website": None,
        "website_source": None,
        "nome_fantasia_enriched": None,
        "nome_fantasia_source": "receita" if nome_fant_existing else None,
        "origin": None,
        "origin_parent_company": None,
        "origin_evidence": None,
        "origin_source_url": None,
        "size": None,
        "size_score": None,
        "size_breakdown": None,
        "estimated_headcount": None,
        "estimated_headcount_source": None,
        "multinational": None,
        "has_significant_funding": None,
        "tech_profile": None,
        "tech_profile_evidence": None,
        "tech_profile_source_url": None,
        "enrichment_evidence": None,
        "last_enriched_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    raw: dict[str, Any] = {}
    failures: list[str] = []

    # ---- Deterministic pass ---------------------------------------------
    origin, parent, origin_ev = classifiers.classify_origin_from_razao(razao)
    if origin:
        enrichment["origin"] = origin
        enrichment["origin_parent_company"] = parent
        enrichment["origin_evidence"] = origin_ev

    tech, tech_ev = classifiers.classify_tech_profile_from_razao(
        razao,
        constituicao=constituicao,
        parent_is_brazilian_bank=(origin == "brasileira" and parent is None),
        is_international=(origin == "internacional"),
    )
    if tech:
        enrichment["tech_profile"] = tech
        enrichment["tech_profile_evidence"] = tech_ev

    # ---- Gemini pass -----------------------------------------------------
    if not dry_run and client is not None:
        async with semaphore:
            cnpj_fmt = _format_cnpj(cnpj)

            # 1) Website + nome_fantasia (always).
            try:
                p_text, p_schema = prompt_mod.prompt_website(
                    razao_social=razao,
                    cnpj_formatted=cnpj_fmt,
                    nome_fantasia=nome_fant_existing,
                )
                ans, urls = await client.ask_json(p_text, p_schema)
                website = _clean_website(ans.get("website"))
                if website:
                    enrichment["website"] = website
                    enrichment["website_source"] = "gemini_search"
                inferred_nf = (ans.get("nome_fantasia") or "").strip()
                if inferred_nf and not nome_fant_existing:
                    enrichment["nome_fantasia_enriched"] = inferred_nf
                    enrichment["nome_fantasia_source"] = "gemini_search"
                raw["website"] = {"answer": ans, "urls": urls}
            except GeminiQuotaExceeded:
                raise
            except Exception as e:
                failures.append(f"website:{type(e).__name__}")
                logger.warning(f"{cnpj} website prompt failed: {e}")

            # 2) Origin + parent (only if deterministic returned None).
            if not enrichment["origin"]:
                try:
                    p_text, p_schema = prompt_mod.prompt_origin_parent(
                        razao_social=razao,
                        cnpj_formatted=cnpj_fmt,
                        website=enrichment["website"],
                    )
                    ans, urls = await client.ask_json(p_text, p_schema)
                    o = (ans.get("origin") or "").strip()
                    if o in ("brasileira", "internacional"):
                        enrichment["origin"] = o
                        enrichment["origin_parent_company"] = (
                            ans.get("parent_company") or None
                        )
                        enrichment["origin_evidence"] = ans.get("evidence") or None
                        enrichment["origin_source_url"] = ans.get("source_url") or (
                            urls[0] if urls else None
                        )
                    raw["origin"] = {"answer": ans, "urls": urls}
                except GeminiQuotaExceeded:
                    raise
                except Exception as e:
                    failures.append(f"origin:{type(e).__name__}")
                    logger.warning(f"{cnpj} origin prompt failed: {e}")

            # 3) Size signals (always).
            try:
                p_text, p_schema = prompt_mod.prompt_size_signals(
                    razao_social=razao,
                    website=enrichment["website"],
                    cnpj_formatted=cnpj_fmt,
                )
                ans, urls = await client.ask_json(p_text, p_schema)
                if ans.get("estimated_headcount") is not None:
                    enrichment["estimated_headcount"] = int(ans["estimated_headcount"])
                    enrichment["estimated_headcount_source"] = (
                        ans.get("headcount_source") or "gemini_search"
                    )
                if "multinational" in ans:
                    enrichment["multinational"] = bool(ans["multinational"])
                if "has_significant_funding" in ans:
                    enrichment["has_significant_funding"] = bool(
                        ans["has_significant_funding"]
                    )
                raw["size_signals"] = {"answer": ans, "urls": urls}
            except GeminiQuotaExceeded:
                raise
            except Exception as e:
                failures.append(f"size_signals:{type(e).__name__}")
                logger.warning(f"{cnpj} size_signals prompt failed: {e}")

            # 4) Tech profile (only if deterministic returned None).
            if not enrichment["tech_profile"]:
                try:
                    p_text, p_schema = prompt_mod.prompt_tech_profile(
                        razao_social=razao,
                        website=enrichment["website"],
                        nome_fantasia=enrichment["nome_fantasia_enriched"]
                        or nome_fant_existing,
                    )
                    ans, urls = await client.ask_json(p_text, p_schema)
                    t = (ans.get("tech_profile") or "").strip()
                    if t in ("web2_with_web3", "native_web3"):
                        enrichment["tech_profile"] = t
                        enrichment["tech_profile_evidence"] = ans.get("evidence") or None
                        srcs = ans.get("source_urls") or []
                        enrichment["tech_profile_source_url"] = (
                            srcs[0] if srcs else (urls[0] if urls else None)
                        )
                    raw["tech_profile"] = {"answer": ans, "urls": urls}
                except GeminiQuotaExceeded:
                    raise
                except Exception as e:
                    failures.append(f"tech_profile:{type(e).__name__}")
                    logger.warning(f"{cnpj} tech_profile prompt failed: {e}")

    # ---- Compute size score ---------------------------------------------
    score, breakdown = classifiers.compute_size_score(
        capital_social=capital,
        headcount=enrichment["estimated_headcount"],
        multinational=enrichment["multinational"],
        has_significant_funding=enrichment["has_significant_funding"],
        constituicao=constituicao,
    )
    bucket = classifiers.score_to_bucket(score, constituicao=constituicao)
    enrichment["size_score"] = score
    enrichment["size_breakdown"] = breakdown
    enrichment["size"] = bucket

    # ---- Evidence cell ---------------------------------------------------
    enrichment["enrichment_evidence"] = _build_evidence_cell(enrichment)
    if failures:
        suffix = " | gemini_failed: " + ", ".join(failures)
        enrichment["enrichment_evidence"] = (
            (enrichment["enrichment_evidence"] or "") + suffix
        )

    enrichment["_raw_responses"] = raw
    return enrichment


# =========================================================================
# Public API
# =========================================================================
async def enrich_companies(
    companies: list[dict[str, Any]],
    settings: Settings,
    *,
    use_cache: bool = True,
    force_refresh: list[str] | None = None,
    only_cnpj: list[str] | None = None,
    dry_run: bool = False,
    progress_cb: Callable[[int, int, str], Awaitable[None]] | None = None,
    client: _GeminiClientLike | None = None,
) -> dict[str, dict[str, Any]]:
    """Run enrichment for the given companies.

    Returns ``{cnpj_digits: enrichment_dict}``. Cache is updated incrementally
    (after each company finishes), so a crash mid-batch preserves progress.
    """
    cache_path = Path(settings.enrichment_cache_path)
    cache_data = cache_mod.load(cache_path) if use_cache else cache_mod._empty()
    force_set = {clean_cnpj(c) for c in (force_refresh or [])}
    only_set = {clean_cnpj(c) for c in (only_cnpj or [])} if only_cnpj else None

    # Lazy-instantiate the Gemini client only when we'll actually use it.
    if not dry_run and client is None:
        if not settings.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Either fill .env or run with --dry-run."
            )
        client = GeminiClient(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            enable_search=settings.gemini_search_grounding,
        )

    sem = asyncio.Semaphore(max(1, settings.enrichment_max_concurrency))
    results: dict[str, dict[str, Any]] = {}
    total = len(companies)

    for idx, company in enumerate(companies, start=1):
        cnpj = clean_cnpj(str(company.get("cnpj", "")))
        if not cnpj:
            continue
        if only_set is not None and cnpj not in only_set:
            continue

        # Cache lookup first.
        if use_cache and cnpj not in force_set:
            cached = cache_mod.get(cache_data, cnpj)
            if cached and not cache_mod.is_stale(
                cached, model=settings.gemini_model
            ):
                logger.info(f"[{idx}/{total}] {cnpj} cache hit")
                results[cnpj] = cached["data"]
                if progress_cb is not None:
                    await progress_cb(idx, total, "cache_hit")
                continue

        logger.info(
            f"[{idx}/{total}] {cnpj} {company.get('razao_social', '')[:60]}"
        )
        try:
            enriched = await _enrich_one(
                company, client=client, dry_run=dry_run, semaphore=sem
            )
        except GeminiQuotaExceeded as e:
            logger.error(f"Gemini quota exceeded after retries; flushing cache. {e}")
            if use_cache:
                cache_mod.save(cache_path, cache_data)
            raise
        except Exception as e:
            logger.error(f"{cnpj} unrecoverable failure: {e}")
            if progress_cb is not None:
                await progress_cb(idx, total, "error")
            continue

        raw = enriched.pop("_raw_responses", {})
        results[cnpj] = enriched
        if use_cache and not dry_run:
            cache_mod.set_entry(
                cache_data,
                cnpj=cnpj,
                enrichment_data=enriched,
                raw_responses=raw,
                model=settings.gemini_model,
            )
            cache_mod.save(cache_path, cache_data)
        if progress_cb is not None:
            await progress_cb(idx, total, "done")

    return results
