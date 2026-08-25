"""
Pytest fixtures for integration tests (Postgres via testcontainers).
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from testcontainers.postgres import PostgresContainer

from app.main import app
from app.db.database import get_db
from app.models.base import Base


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Start a throwaway Postgres container for the test session."""
    with PostgresContainer("postgres:16") as postgres:
        yield postgres.get_connection_url().replace(
            "postgresql+psycopg2", "postgresql+asyncpg"
        )


@pytest.fixture(scope="session")
def engine(postgres_url: str):
    """Async engine bound to the test container."""
    return create_async_engine(postgres_url)


@pytest.fixture(scope="session")
def session_factory(engine):
    """Session factory for the test database."""
    return async_sessionmaker(
        expire_on_commit=False, class_=AsyncSession, bind=engine
    )


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_database(engine, session_factory):
    """Create tables before each test and drop them after."""

    async def override_get_db() -> AsyncSession:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app.dependency_overrides[get_db] = override_get_db

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_client():
    """Provides an AsyncClient for testing FastAPI routes."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
