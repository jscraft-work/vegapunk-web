"""계정 모델(멀티 신원) 테스트.

- resolve_user: 신원 기반 find-or-create + 레거시 브리지.
- link_account: 로그인 상태에서 새 신원 연동(콜백 분기) + 충돌 거부.
- merge_users: 데이터 이전 + 툼스톤.

DB는 conftest의 migrated_pool 사용. 각 테스트는 users CASCADE로 초기화한다.
"""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import execute, fetch, fetchrow
from app.main import create_app
from app.routes import account, auth
from app.routes.auth import merge_users, resolve_user
from app.session import MemoryStore, stash_oauth_state


@pytest_asyncio.fixture
async def pool(migrated_pool):
    async with migrated_pool.connection() as conn:
        await conn.execute("TRUNCATE users RESTART IDENTITY CASCADE")
        await conn.commit()
    yield migrated_pool


async def _identities(pool):
    return await fetch(pool, "SELECT * FROM identities ORDER BY id")


async def _users(pool):
    return await fetch(pool, "SELECT * FROM users ORDER BY id")


# ── resolve_user ──────────────────────────────────────────────


async def test_new_identity_creates_user(pool):
    uid = await resolve_user(
        pool, {"provider": "github", "sub": "100", "email": "a@gh.com", "name": "A"}
    )
    users = await _users(pool)
    idents = await _identities(pool)
    assert len(users) == 1
    assert users[0]["id"] == uid
    assert len(idents) == 1
    assert idents[0]["provider"] == "github" and idents[0]["sub"] == "100"
    assert idents[0]["user_id"] == uid


async def test_known_identity_returns_same_user(pool):
    p = {"provider": "kakao", "sub": "55", "email": "k@k.com", "name": "K"}
    uid1 = await resolve_user(pool, p)
    uid2 = await resolve_user(pool, p)
    assert uid1 == uid2
    assert len(await _users(pool)) == 1
    assert len(await _identities(pool)) == 1


async def test_legacy_bridge(pool):
    # identity 0개 레거시 user(기존 단일 유저).
    legacy = await fetchrow(
        pool,
        "INSERT INTO users (email, name) VALUES ('legacy@x.com','레거시') RETURNING id",
    )
    uid = await resolve_user(
        pool,
        {"provider": "github", "sub": "777", "email": "legacy@x.com", "name": "G"},
    )
    assert uid == legacy["id"]  # 새 user 안 만들고 레거시에 흡수
    assert len(await _users(pool)) == 1
    idents = await _identities(pool)
    assert len(idents) == 1 and idents[0]["user_id"] == legacy["id"]


async def test_no_email_autolink(pool):
    # 이미 identity가 있는 user.
    uid_a = await resolve_user(
        pool, {"provider": "github", "sub": "1", "email": "same@x.com", "name": "A"}
    )
    # 같은 email이지만 다른 신원 → 자동 합치지 않고 새 user.
    uid_b = await resolve_user(
        pool, {"provider": "kakao", "sub": "2", "email": "same@x.com", "name": "B"}
    )
    assert uid_a != uid_b
    assert len(await _users(pool)) == 2
    assert len(await _identities(pool)) == 2


# ── link_account (HTTP 콜백) ──────────────────────────────────


@pytest_asyncio.fixture
async def link_client(pool, monkeypatch):
    """require_user를 user_id=1로 고정한 인앱 클라이언트 + 공유 세션 저장소."""
    from app.deps import require_user

    # 현재 로그인 user(id=1) 생성.
    async with pool.connection() as conn:
        await conn.execute(
            "INSERT INTO users (email, name) VALUES ('me@x.com','나')"
        )
        await conn.commit()

    app = create_app()
    app.state.pool = pool
    app.state.session_store = MemoryStore()
    app.dependency_overrides[require_user] = lambda: {
        "id": 1, "email": "me@x.com", "name": "나",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, app, monkeypatch


async def _link_callback(ac, app, monkeypatch, profile, token):
    provider = profile["provider"]
    state = "linkstate-" + profile["sub"]
    await stash_oauth_state(app.state.session_store, state, provider)

    async def fake_fetch(settings, prov, code):
        return profile

    monkeypatch.setattr(auth, "_fetch_profile", fake_fetch)
    return await ac.get(
        f"/auth/callback/{provider}?code=x&state={state}&link={token}"
    )


async def test_link_account_attaches_identity(link_client):
    ac, app, monkeypatch = link_client
    start = await ac.post("/api/account/link/start", json={"provider": "github"})
    assert start.status_code == 200
    url = start.json()["url"]
    assert url.startswith("/auth/login/github?link=")
    token = url.split("link=")[1]

    r = await _link_callback(
        ac, app, monkeypatch,
        {"provider": "github", "sub": "900", "email": "g@x.com", "name": "G"},
        token,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "linked"
    idents = await fetch(
        app.state.pool,
        "SELECT user_id FROM identities WHERE provider='github' AND sub='900'",
    )
    assert len(idents) == 1 and idents[0]["user_id"] == 1


async def test_link_conflict(link_client):
    ac, app, monkeypatch = link_client
    pool = app.state.pool
    # 다른 user(id=2)에 이미 묶인 신원.
    await execute(
        pool, "INSERT INTO users (email, name) VALUES ('other@x.com','남')"
    )
    await execute(
        pool,
        "INSERT INTO identities (user_id, provider, sub, email) "
        "VALUES (2,'github','200','o@x.com')",
    )

    start = await ac.post("/api/account/link/start", json={"provider": "github"})
    token = start.json()["url"].split("link=")[1]
    r = await _link_callback(
        ac, app, monkeypatch,
        {"provider": "github", "sub": "200", "email": "o@x.com", "name": "O"},
        token,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "conflict"
    assert body["other_user"] == 2
    # 중복 INSERT 안 됨: (github,200)은 여전히 user 2에만.
    idents = await fetch(
        pool, "SELECT user_id FROM identities WHERE provider='github' AND sub='200'"
    )
    assert len(idents) == 1 and idents[0]["user_id"] == 2


# ── merge_users ───────────────────────────────────────────────


async def test_merge_moves_data_and_tombstones(pool):
    # src(id=1), dst(id=2) 생성 + 각자 신원/노트.
    src = await resolve_user(
        pool, {"provider": "kakao", "sub": "s1", "email": "src@x.com", "name": "S"}
    )
    dst = await resolve_user(
        pool, {"provider": "github", "sub": "d1", "email": "dst@x.com", "name": "D"}
    )
    await execute(
        pool,
        "INSERT INTO notes (user_id, title, body) VALUES (%s,'노트A','본문')",
        (src,),
    )

    await merge_users(pool, src, dst)

    # 노트/신원이 dst로 이전.
    notes = await fetch(pool, "SELECT user_id FROM notes")
    assert all(n["user_id"] == dst for n in notes)
    idents = await fetch(pool, "SELECT user_id FROM identities ORDER BY id")
    assert all(i["user_id"] == dst for i in idents)
    assert len(idents) == 2
    # src는 툼스톤.
    srow = await fetchrow(pool, "SELECT status, merged_into FROM users WHERE id=%s", (src,))
    assert srow["status"] == "merged"
    assert srow["merged_into"] == dst
