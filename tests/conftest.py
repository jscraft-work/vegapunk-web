"""테스트 하네스.

대상 DB는 DATABASE_URL 환경변수로 받는다(테스트 전용 DB 권장).
세션 시작 시 마이그레이션을 적용하고, 각 테스트 후 데이터를 TRUNCATE 한다.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.db import make_pool, run_migrations
from app.main import create_app

# 테스트가 잡아야 할 테이블(스키마 테이블 제외)
_DATA_TABLES = [
    "message_citations",
    "messages",
    "conversations",
    "note_tags",
    "tags",
    "edges",
    "chunks",
    "note_versions",
    "notes",
    "users",
]


def _test_dsn() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres@localhost:55432/vegapunk_test",
    )


@pytest_asyncio.fixture(scope="session")
async def migrated_pool():
    # get_settings는 lru_cache이므로 환경변수가 반영되도록 초기화
    os.environ.setdefault("DATABASE_URL", _test_dsn())
    get_settings.cache_clear()
    pool = make_pool(_test_dsn())
    await pool.open()
    await pool.wait()
    await run_migrations(pool)
    yield pool
    await pool.close()


@pytest_asyncio.fixture
async def clean_db(migrated_pool):
    yield migrated_pool
    async with migrated_pool.connection() as conn:
        await conn.execute(
            "TRUNCATE %s RESTART IDENTITY CASCADE"
            % ", ".join(_DATA_TABLES)
        )
        await conn.commit()


@pytest_asyncio.fixture
async def client(migrated_pool):
    """ASGITransport 기반 인앱 클라이언트. 이미 마이그레이션된 풀을 주입.

    인증은 require_user override로 우회(테스트용 세션 주입 대체).
    """
    from app.deps import require_user
    from app.session import MemoryStore

    app = create_app()
    app.state.pool = migrated_pool
    app.state.session_store = MemoryStore()
    app.dependency_overrides[require_user] = lambda: {
        "id": 1, "email": "tester@example.com", "name": "테스터",
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
