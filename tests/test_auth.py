"""인증 테스트 (OAuth 프로바이더 모킹)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.routes import auth
from app.session import MemoryStore


@pytest_asyncio.fixture
async def auth_client(migrated_pool, monkeypatch):
    """인증 우회를 적용하지 않은 실제 보호 앱(401/콜백 흐름 검증용)."""
    # 사용자 테이블 정리.
    async with migrated_pool.connection() as conn:
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")
        await conn.commit()

    app = create_app()
    app.state.pool = migrated_pool
    app.state.session_store = MemoryStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, monkeypatch


async def test_callback_creates_user_and_session(auth_client):
    client, monkeypatch = auth_client

    async def fake_oauth(request, provider):
        return {"provider": "github", "sub": "42", "email": "u@gh.com", "name": "깃헙유저"}

    monkeypatch.setattr(auth, "_complete_oauth", fake_oauth)

    r = await client.get("/auth/callback/github")
    assert r.status_code == 302
    assert "session=" in r.headers.get("set-cookie", "")

    # 세션 쿠키로 /auth/me → user 반환.
    me = (await client.get("/auth/me")).json()
    assert me["user"]["email"] == "u@gh.com"


async def test_me(auth_client):
    client, _ = auth_client
    # 쿠키 없음 → null.
    me = (await client.get("/auth/me")).json()
    assert me["user"] is None


async def test_logout(auth_client):
    client, monkeypatch = auth_client

    async def fake_oauth(request, provider):
        return {"provider": "github", "sub": "1", "email": "a@b.com", "name": "A"}

    monkeypatch.setattr(auth, "_complete_oauth", fake_oauth)
    await client.get("/auth/callback/github")
    assert (await client.get("/auth/me")).json()["user"] is not None

    await client.get("/auth/logout")
    assert (await client.get("/auth/me")).json()["user"] is None


async def test_protected_routes(auth_client):
    client, monkeypatch = auth_client
    # 미인증 → 401.
    assert (await client.get("/api/conversations")).status_code == 401

    async def fake_oauth(request, provider):
        return {"provider": "github", "sub": "7", "email": "p@q.com", "name": "P"}

    monkeypatch.setattr(auth, "_complete_oauth", fake_oauth)
    await client.get("/auth/callback/github")

    # 인증 후 통과.
    assert (await client.get("/api/conversations")).status_code == 200


async def test_kakao_no_email_fallback(auth_client):
    client, monkeypatch = auth_client

    async def fake_oauth(request, provider):
        # 카카오 이메일 미동의.
        return {"provider": "kakao", "sub": "999", "email": None, "name": "닉네임"}

    monkeypatch.setattr(auth, "_complete_oauth", fake_oauth)
    r = await client.get("/auth/callback/kakao")
    assert r.status_code == 302

    me = (await client.get("/auth/me")).json()
    assert me["user"]["email"] == "kakao:999"  # placeholder 폴백
    assert me["user"]["name"] == "닉네임"
