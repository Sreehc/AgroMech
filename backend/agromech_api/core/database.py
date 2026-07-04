from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from agromech_api.core.config import Settings, get_settings


@lru_cache
def get_engine() -> Engine:
    return create_database_engine(get_settings())


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)
