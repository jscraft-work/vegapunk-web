"""정적 서빙 테스트."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.session import COOKIE_NAME, MemoryStore, create_session


@pytest_asyncio.fixture
async def static_client(migrated_pool):
    app = create_app()
    app.state.pool = migrated_pool
    app.state.session_store = MemoryStore()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, app


async def test_login_page(static_client):
    client, _ = static_client
    r = await client.get("/login")
    assert r.status_code == 200
    assert "vegapunk" in r.text


async def test_static_assets(static_client):
    client, _ = static_client
    r = await client.get("/static/app.js")
    assert r.status_code == 200


async def test_index_requires_auth(static_client):
    client, app = static_client
    # 미인증 → /login 리다이렉트.
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"

    # 인증 세션 있으면 index.html.
    token = await create_session(app.state.session_store, 1, 3600)
    client.cookies.set(COOKIE_NAME, token)
    r2 = await client.get("/", follow_redirects=False)
    assert r2.status_code == 200
    assert "vegapunk" in r2.text
