"""``services.platform`` — the shared FastAPI/Celery service template (M15).

Public surface used by every module::

    from services.platform import create_app, get_settings, get_logger

    app = create_app("event-pipeline")
"""
from __future__ import annotations

from .app import create_app
from .config import Settings, get_settings
from .logging import configure_logging, get_correlation_id, get_logger, set_correlation_id

__all__ = [
    "create_app",
    "Settings",
    "get_settings",
    "get_logger",
    "configure_logging",
    "get_correlation_id",
    "set_correlation_id",
]
