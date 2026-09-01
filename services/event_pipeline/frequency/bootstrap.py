"""Schema bootstrap for the engagement ledger (local/dev + tests; prod = Alembic)."""
from __future__ import annotations

from sqlalchemy import Engine

from .tables import metadata


def create_all(engine: Engine) -> None:
    metadata.create_all(engine)
