"""인증 테스트 (OAuth 외부호출 모킹, state는 Redis/MemoryStore)."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.routes import auth
from app.session import MemoryStore, stash_oauth_state


@pytest_asyncio.fixture
async def auth_client(migrated_pool, monkeypatch):
    async with migrated_pool.connection() as conn:
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")
        await conn.commit()

    app = create_app()
    app.state.pool = migrated_pool
    app.state.session_store = MemoryStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, monkeypatch, app


async def _login(ac, app, monkeypatch, profile):
    """state 발급 + _fetch_profile 모킹 후 콜백 호출(로그인 성공 흐름)."""
    provider = profile["provider"]
    state = "teststate-" + profile["sub"]
    await stash_oauth_state(app.state.session_store, state, provider)

    async def fake_fetch(settings, prov, code):
        return profile

    monkeypatch.setattr(auth, "_fetch_profile", fake_fetch)
    return await ac.get(f"/auth/callback/{provider}?code=x&state={state}")


async def test_callback_creates_user_and_session(auth_client):
    ac, monkeypatch, app = auth_client
    r = await _login(
        ac, app, monkeypatch,
        {"provider": "github", "sub": "42", "email": "u@gh.com", "name": "깃헙유저"},
    )
    assert r.status_code == 302
    assert "session=" in r.headers.get("set-cookie", "")
    me = (await ac.get("/auth/me")).json()
    assert me["user"]["email"] == "u@gh.com"


async def test_me(auth_client):
    ac, _, _ = auth_client
    me = (await ac.get("/auth/me")).json()
    assert me["user"] is None


async def test_logout(auth_client):
    ac, monkeypatch, app = auth_client
    await _login(
        ac, app, monkeypatch,
        {"provider": "github", "sub": "1", "email": "a@b.com", "name": "A"},
    )
    assert (await ac.get("/auth/me")).json()["user"] is not None
    await ac.get("/auth/logout")
    assert (await ac.get("/auth/me")).json()["user"] is None


async def test_protected_routes(auth_client):
    ac, monkeypatch, app = auth_client
    assert (await ac.get("/api/conversations")).status_code == 401
    await _login(
        ac, app, monkeypatch,
        {"provider": "github", "sub": "7", "email": "p@q.com", "name": "P"},
    )
    assert (await ac.get("/api/conversations")).status_code == 200


async def test_invalid_state_rejected(auth_client):
    ac, monkeypatch, app = auth_client

    async def fake_fetch(settings, prov, code):
        return {"provider": "github", "sub": "5", "email": "x@y.com", "name": "X"}

    monkeypatch.setattr(auth, "_fetch_profile", fake_fetch)
    # state 미발급 → 거부(위조 방지).
    r = await ac.get("/auth/callback/github?code=x&state=bogus")
    assert r.status_code == 400


async def test_kakao_no_email_fallback(auth_client):
    ac, monkeypatch, app = auth_client
    r = await _login(
        ac, app, monkeypatch,
        {"provider": "kakao", "sub": "999", "email": None, "name": "닉네임"},
    )
    assert r.status_code == 302
    me = (await ac.get("/auth/me")).json()
    assert me["user"]["email"] == "kakao:999"  # placeholder 폴백
    assert me["user"]["name"] == "닉네임"
