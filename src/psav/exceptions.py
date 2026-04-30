"""Project-specific exceptions."""


class PSAVError(Exception):
    """Base class for all project exceptions."""


class IngestionError(PSAVError):
    """Failure in the ingestion layer (download, parsing, etc.)."""


class ReceitaLayoutError(IngestionError):
    """Receita Federal layout differs from the expected schema."""


class EnrichmentError(PSAVError):
    """Failure in the enrichment layer (external APIs, scraping)."""


class ScoringError(PSAVError):
    """Failure in score computation."""


class ConfigError(PSAVError):
    """Required environment variable is missing or invalid."""
