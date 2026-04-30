"""loguru configuration."""

import sys

from loguru import logger as _logger

from psav.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )


configure_logging()
logger = _logger
