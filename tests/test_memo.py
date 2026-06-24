"""메모 API 테스트 — 글로벌/대화별 저장 + 사용자 격리."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import execute, fetchrow
from tests._helpers import build_app


@pytest_asyncio.fixture
async def memo_client(clean_db):
    """FAKE_USER(id=1)로 인증된 메모 클라이언트."""
    pool = clean_db
    app = build_app(pool)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, pool


async def _seed_user(pool, email: str) -> int:
    await execute(pool, "INSERT INTO users (email, name) VALUES (%s, %s)", (email, email))
    row = await fetchrow(pool, "SELECT id FROM users WHERE email = %s", (email,))
    return row["id"]


async def test_global_memo_roundtrip(memo_client):
    ac, _ = memo_client
    # 초기엔 빈 문자열(행 없음).
    assert (await ac.get("/api/memo")).json()["body"] == ""
    # 저장 → 조회.
    await ac.put("/api/memo", json={"body": "# 내 메모\n할 일"})
    assert (await ac.get("/api/memo")).json()["body"] == "# 내 메모\n할 일"
    # 덮어쓰기.
    await ac.put("/api/memo", json={"body": "수정됨"})
    assert (await ac.get("/api/memo")).json()["body"] == "수정됨"


async def test_conv_memo_roundtrip(memo_client):
    ac, pool = memo_client
    conv = await fetchrow(
        pool, "INSERT INTO conversations (user_id) VALUES (1) RETURNING id", None
    )
    cid = conv["id"]
    assert (await ac.get(f"/api/conversations/{cid}/memo")).json()["body"] == ""
    await ac.put(f"/api/conversations/{cid}/memo", json={"body": "대화 메모"})
    assert (await ac.get(f"/api/conversations/{cid}/memo")).json()["body"] == "대화 메모"


async def test_conv_memo_unowned_404(memo_client):
    ac, pool = memo_client
    # 다른 유저(2)의 대화 → 1번 유저는 접근 불가(not found).
    u2 = await _seed_user(pool, "u2@x.com")
    conv = await fetchrow(
        pool, "INSERT INTO conversations (user_id) VALUES (%s) RETURNING id", (u2,)
    )
    cid = conv["id"]
    assert (await ac.get(f"/api/conversations/{cid}/memo")).json() == {"error": "not found"}
    assert (await ac.put(
        f"/api/conversations/{cid}/memo", json={"body": "침범"}
    )).json() == {"error": "not found"}


async def test_global_memo_user_isolation(clean_db):
    pool = clean_db
    u2 = await _seed_user(pool, "u2@x.com")

    app1 = build_app(pool)  # FAKE_USER id=1
    app2 = build_app(pool, user={"id": u2, "email": "u2@x.com", "name": "U2"})
    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as a1, \
               AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as a2:
        await a1.put("/api/memo", json={"body": "유저1 메모"})
        await a2.put("/api/memo", json={"body": "유저2 메모"})
        # 서로의 글로벌 메모가 섞이지 않는다.
        assert (await a1.get("/api/memo")).json()["body"] == "유저1 메모"
        assert (await a2.get("/api/memo")).json()["body"] == "유저2 메모"
