"""Соединение с PostgreSQL и неизменяемый идентификатор пользователя MVP."""

import os
from uuid import UUID

from sqlalchemy import URL, Engine, create_engine

LOCAL_USER_ID = UUID("00000000-0000-4000-8000-000000000001")


def configured_url() -> str | URL | None:
    if value := os.environ.get("DATABASE_URL"):
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value
    if not os.environ.get("PGHOST"):
        return None
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("PGUSER", "rapporteur"),
        password=os.environ.get("PGPASSWORD"),
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT", "5432")),
        database=os.environ.get("PGDATABASE", "rapporteur"),
    )


def make_engine(url: str | URL) -> Engine:
    return create_engine(
        url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": 5,
            "options": "-c timezone=UTC",
        },
    )
