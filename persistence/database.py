"""Database engine and session factory.

Transaction ownership: callers (services) open the transaction with ``session.begin()``.
Repositories only add, flush and query; they never commit or roll back.
"""

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def create_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    # pool_pre_ping drops connections the server closed while idle instead of failing a request.
    return create_async_engine(url, echo=echo, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after commit, so responses can be built from them
    # without a second round trip.
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
