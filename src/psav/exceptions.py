"""Project-specific exceptions."""


class PSAVError(Exception):
    """Base class for all project exceptions."""


class IngestionError(PSAVError):
    """Failure in the ingestion layer (download, parsing, etc.)."""


class ReceitaLayoutError(IngestionError):
    """Receita Federal layout differs from the expected schema."""


class EnrichmentError(PSAVError):
    """Failure in the enrichment layer (external APIs, scraping)."""


class GeminiQuotaExceeded(EnrichmentError):
    """Gemini API rejected the request with RESOURCE_EXHAUSTED / 429."""


class GeminiTransientError(EnrichmentError):
    """Transient failure (503, 502, server disconnect, idle TCP reset). Retriable."""


class GeminiSchemaError(EnrichmentError):
    """Gemini returned invalid JSON / failed schema validation."""


class ScoringError(PSAVError):
    """Failure in score computation."""


class ConfigError(PSAVError):
    """Required environment variable is missing or invalid."""
