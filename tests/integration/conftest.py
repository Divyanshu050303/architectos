"""Integration fixtures: a real Postgres database, migrated with Alembic (never create_all()).

Each test session creates its own scratch database on TEST_DATABASE_URL's server and drops it
afterwards. Each test runs inside a transaction that is rolled back, so tests never see each
other's rows and the append-only audit table needs no cleanup.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from persistence.database import create_engine

DEFAULT_ADMIN_URL = "postgresql+asyncpg://architectos:architectos@127.0.0.1:5434/postgres"


def admin_url() -> str:
    return os.environ.get("TEST_DATABASE_URL", DEFAULT_ADMIN_URL)


def _asyncpg_dsn(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _admin_execute(sql: str) -> None:
    connection = await asyncpg.connect(_asyncpg_dsn(admin_url()))
    try:
        await connection.execute(sql)
    finally:
        await connection.close()


def create_scratch_database() -> str:
    """Create an empty database and return its SQLAlchemy URL."""
    name = f"architectos_test_{uuid.uuid4().hex[:12]}"
    asyncio.run(_admin_execute(f'CREATE DATABASE "{name}"'))
    return make_url(admin_url()).set(database=name).render_as_string(hide_password=False)


def drop_scratch_database(url: str) -> None:
    name = make_url(url).database
    asyncio.run(_admin_execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    url = create_scratch_database()
    try:
        command.upgrade(alembic_config(url), "head")
        yield url
    finally:
        drop_scratch_database(url)


@pytest.fixture(scope="session")
async def engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(migrated_database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine: AsyncEngine) -> AsyncIterator[AsyncConnection]:
    """One connection per test, inside an outer transaction that is always rolled back."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            yield connection
        finally:
            await transaction.rollback()


def joined_session(connection: AsyncConnection) -> AsyncSession:
    """A session on the test's connection. Code under test may begin and commit transactions:
    with ``create_savepoint`` those only open and release savepoints, and the outer rollback
    still discards everything."""
    return AsyncSession(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")


@pytest.fixture
async def db(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    session = joined_session(connection)
    try:
        yield session
    finally:
        await session.close()
