"""OAuth 2.1 인가서버(AS) 테스트 (Task v2-02).

상류 provider(github)는 _fetch_profile 모킹으로 대체한다. 저장소는 MemoryStore.
전 과정(디스커버리·DCR·authorize→상류→code→token·PKCE·refresh 회전)을 검증한다.
"""

from urllib.parse import parse_qs, urlparse

import pytest_asyncio
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app
from app.routes import auth
from app.session import MemoryStore

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PROFILE = {"provider": "github", "sub": "12345", "email": "mcp@gh.com", "name": "MCP유저"}


@pytest_asyncio.fixture
async def oauth_client(migrated_pool, monkeypatch):
    # users 초기화(resolve_user가 id=1 신규 생성하도록).
    async with migrated_pool.connection() as conn:
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")
        await conn.commit()

    # 상류 login 리다이렉트가 동작하려면 client_id가 비어있지 않아야 한다.
    monkeypatch.setenv("GH_CLIENT_ID", "test-gh-id")
    monkeypatch.setenv("GH_CLIENT_SECRET", "test-gh-secret")
    get_settings.cache_clear()

    app = create_app()
    app.state.pool = migrated_pool
    app.state.session_store = MemoryStore()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, app, monkeypatch
    get_settings.cache_clear()


async def _register(ac, redirect_uri=REDIRECT):
    r = await ac.post(
        "/oauth/register",
        json={"redirect_uris": [redirect_uri], "client_name": "claude-test"},
    )
    return r


async def _run_authcode(ac, app, monkeypatch, profile=PROFILE):
    """DCR→authorize→상류 로그인(모킹)→콜백 재개→code 발급. 반환: (code, verifier, client_id)."""
    client_id = (await _register(ac)).json()["client_id"]
    verifier = "verifier-" + "a" * 60
    challenge = create_s256_code_challenge(verifier)

    # 1) authorize → 상류 로그인으로 302(/auth/login/github?authreq=...)
    authz = await ac.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "client-state-xyz",
            "scope": "mcp",
        },
        follow_redirects=False,
    )
    assert authz.status_code in (302, 307), authz.text
    login_path = authz.headers["location"]
    assert login_path.startswith("/auth/login/github?authreq=")

    # 2) 상류 login → github authorize로 302(state 포함)
    login = await ac.get(login_path, follow_redirects=False)
    assert login.status_code in (302, 307)
    gh_qs = parse_qs(urlparse(login.headers["location"]).query)
    gh_state = gh_qs["state"][0]

    # 3) 상류 콜백(프로필 모킹) → authorize 재개 → claude redirect_uri로 302(code 포함)
    async def fake_fetch(settings, prov, code):
        return profile

    monkeypatch.setattr(auth, "_fetch_profile", fake_fetch)
    cb = await ac.get(
        f"/auth/callback/github?code=upstreamcode&state={gh_state}",
        follow_redirects=False,
    )
    assert cb.status_code in (302, 307), cb.text
    cb_url = urlparse(cb.headers["location"])
    assert f"{cb_url.scheme}://{cb_url.netloc}{cb_url.path}" == REDIRECT
    cb_qs = parse_qs(cb_url.query)
    assert cb_qs["state"][0] == "client-state-xyz"
    return cb_qs["code"][0], verifier, client_id


async def _exchange(ac, code, verifier, client_id):
    return await ac.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": REDIRECT,
        },
    )


# ── 테스트 ────────────────────────────────────────────────────


async def test_discovery_documents(oauth_client):
    ac, _, _ = oauth_client
    prm = (await ac.get("/.well-known/oauth-protected-resource")).json()
    assert prm["resource"].endswith("/mcp")
    assert isinstance(prm["authorization_servers"], list) and prm["authorization_servers"]

    asm = (await ac.get("/.well-known/oauth-authorization-server")).json()
    assert asm["issuer"]
    assert asm["authorization_endpoint"].endswith("/oauth/authorize")
    assert asm["token_endpoint"].endswith("/oauth/token")
    assert asm["code_challenge_methods_supported"] == ["S256"]


async def test_dcr_register(oauth_client):
    ac, _, _ = oauth_client
    r = await _register(ac)
    assert r.status_code == 201
    assert r.json()["client_id"]
    # loopback(Claude Code)도 허용.
    lb = await _register(ac, "http://127.0.0.1:53535/callback")
    assert lb.status_code == 201
    # 허용되지 않은 redirect_uri는 거부.
    bad = await _register(ac, "https://evil.example.com/cb")
    assert bad.status_code == 400
    # ChatGPT 커넥터: chatgpt.com/chat.openai.com https 콜백(경로 무관) 허용.
    cg = await _register(ac, "https://chatgpt.com/connector_platform_oauth_redirect")
    assert cg.status_code == 201
    cg2 = await _register(ac, "https://chatgpt.com/any/other/path")
    assert cg2.status_code == 201
    oa = await _register(ac, "https://chat.openai.com/oauth/callback")
    assert oa.status_code == 201
    # 신뢰 호스트라도 http(비-loopback)면 거부(https 필수).
    insecure = await _register(ac, "http://chatgpt.com/connector_platform_oauth_redirect")
    assert insecure.status_code == 400


async def test_authcode_pkce_flow(oauth_client):
    ac, app, monkeypatch = oauth_client
    code, verifier, client_id = await _run_authcode(ac, app, monkeypatch)
    tok = await _exchange(ac, code, verifier, client_id)
    assert tok.status_code == 200, tok.text
    body = tok.json()
    assert body["access_token"] and body["token_type"] == "Bearer"
    assert body["refresh_token"]


async def test_pkce_mismatch_rejected(oauth_client):
    ac, app, monkeypatch = oauth_client
    code, _verifier, client_id = await _run_authcode(ac, app, monkeypatch)
    tok = await _exchange(ac, code, "wrong-verifier-" + "b" * 50, client_id)
    assert tok.status_code == 400
    assert tok.json()["error"] == "invalid_grant"


async def test_token_resolves_user(oauth_client):
    ac, app, monkeypatch = oauth_client
    code, verifier, client_id = await _run_authcode(ac, app, monkeypatch)
    access = (await _exchange(ac, code, verifier, client_id)).json()["access_token"]
    # 발급 토큰 → user_id 오프라인 매핑(보호 리소스가 이걸로 스코프한다).
    # (실제 /mcp는 Task 03의 MCP 서버가 마운트 — 여기선 AS의 매핑 계약만 검증.)
    from app import oauth_store

    principal = await oauth_store.resolve_access(app.state.session_store, access)
    assert principal is not None
    assert principal["user_id"] == 1  # 초기화 직후 첫 유저 = 1


async def test_unauth_returns_www_authenticate(oauth_client):
    ac, _, _ = oauth_client
    r = await ac.get("/mcp")
    assert r.status_code == 401
    www = r.headers.get("www-authenticate", "")
    assert www.startswith("Bearer")
    assert "resource_metadata" in www


async def test_refresh_rotation(oauth_client):
    ac, app, monkeypatch = oauth_client
    code, verifier, client_id = await _run_authcode(ac, app, monkeypatch)
    first = (await _exchange(ac, code, verifier, client_id)).json()
    old_refresh = first["refresh_token"]

    rr = await ac.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": old_refresh,
            "client_id": client_id,
        },
    )
    assert rr.status_code == 200, rr.text
    body = rr.json()
    assert body["access_token"] != first["access_token"]
    assert body["refresh_token"] and body["refresh_token"] != old_refresh

    # 회전: 옛 refresh 재사용 거부.
    reuse = await ac.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": old_refresh},
    )
    assert reuse.status_code == 400
