import os
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import URL, Engine

from rapporteur_backend.app import create_app
from rapporteur_backend.db import make_engine
from rapporteur_backend.migrate import migrate


@pytest.fixture
def database() -> Iterator[Engine]:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("Нужен TEST_DATABASE_URL; make test запускает PostgreSQL")
    name = f"rapporteur_test_{uuid4().hex}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0").format(
                sql.Identifier(name)
            )
        )
    params = {key: str(value) for key, value in conninfo_to_dict(dsn).items() if value is not None}
    engine = make_engine(
        URL.create(
            "postgresql+psycopg",
            username=params.get("user"),
            password=params.get("password"),
            host=params.get("host"),
            port=int(params.get("port", "5432")),
            database=name,
        )
    )
    try:
        migrate(engine)
        yield engine
    finally:
        engine.dispose()
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def client(database: Engine) -> Iterator[TestClient]:
    with TestClient(create_app(database_url=database.url)) as client:
        yield client
