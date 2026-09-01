"""Schema + stream bootstrap.

``create_all`` mirrors the SQLAlchemy metadata for local/dev and the SQLite
tests; production migrations are owned by Alembic (POA/03 §4). ``ensure_stream_group``
creates the Redis stream + consumer group before the relay/consumers start.
"""
from __future__ import annotations

from sqlalchemy import Engine

from .publisher import CONSUMER_GROUP, RedisStreamPublisher
from .tables import metadata


def create_all(engine: Engine) -> None:
    metadata.create_all(engine)


def ensure_stream_group(publisher: RedisStreamPublisher, group: str = CONSUMER_GROUP) -> None:
    publisher.ensure_group(group)
