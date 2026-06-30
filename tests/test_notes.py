"""노트/지식 API 테스트 (실제 임베딩 + FakeLLM)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import fetch, fetchrow
from app.llm import FakeLLMClient
from tests._helpers import build_app


@pytest_asyncio.fixture
async def notes_client(clean_db):
    pool = clean_db
    llm = FakeLLMClient(complete_fn=lambda p, t: "태그가, 나, 다")
    app = build_app(pool, llm=llm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, pool


async def _ingest(client, title, body, tags=None, merge_into=None):
    return (
        await client.post(
            "/api/ingest",
            json={"title": title, "body": body, "tags": tags or [], "merge_into": merge_into},
        )
    ).json()


async def test_pages_and_tags(notes_client):
    client, _ = notes_client
    await _ingest(client, "노트A", "본문 A", ["공통", "에이"])
    await _ingest(client, "노트B", "본문 B", ["공통"])

    pages = (await client.get("/api/pages", params={"tag": "공통"})).json()["pages"]
    assert {p["title"] for p in pages} == {"노트A", "노트B"}

    tags = (await client.get("/api/tags")).json()["tags"]
    counts = {t["tag"]: t["count"] for t in tags}
    assert counts["공통"] == 2
    assert counts["에이"] == 1


async def test_search_snippet(notes_client):
    client, _ = notes_client
    await _ingest(client, "검색노트", "비건은 인공지능 연구자다.")
    results = (await client.get("/api/search", params={"q": "비건 인공지능"})).json()["results"]
    assert results
    top = results[0]
    assert {"note_id", "title", "snippet", "score"} <= set(top)
    assert top["title"] == "검색노트"


async def test_page_backlinks(notes_client):
    client, _ = notes_client
    await _ingest(client, "노트A", "여기서 [[노트B]]를 참고한다.")
    await _ingest(client, "노트B", "B의 본문.")
    page = (await client.get("/api/page/노트B")).json()
    assert "노트A" in page["backlinks"]
    assert "노트A" in page["titles"] and "노트B" in page["titles"]


async def test_ingest_sync_index(notes_client):
    client, _ = notes_client
    await _ingest(client, "즉시검색", "갓 저장된 노트는 바로 검색된다.")
    results = (await client.get("/api/search", params={"q": "갓 저장된 노트"})).json()["results"]
    assert any(r["title"] == "즉시검색" for r in results)


async def test_tags_replace_and_suggest(notes_client):
    client, _ = notes_client
    await _ingest(client, "태그노트", "본문.", ["old"])
    replaced = (await client.post("/api/page/태그노트/tags", json={"tags": ["new1", "new2"]})).json()
    assert replaced["ok"] and replaced["tags"] == ["new1", "new2"]

    suggested = (await client.post("/api/page/태그노트/suggest-tags")).json()
    assert suggested["tags"] == ["태그가", "나", "다"]


async def test_delete_unresolves(notes_client):
    client, pool = notes_client
    await _ingest(client, "노트A", "[[노트B]] 링크.")
    await _ingest(client, "노트B", "삭제 대상.")
    b = await fetchrow(pool, "SELECT id FROM notes WHERE title='노트B'", None)

    await client.delete("/api/page/노트B")

    # B 청크 CASCADE 삭제.
    chunks = await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (b["id"],))
    assert chunks == []
    # A→B edges의 dst_note는 NULL(행은 유지).
    edge = await fetchrow(
        pool, "SELECT dst_note FROM edges WHERE dst_title='노트B'", None
    )
    assert edge is not None and edge["dst_note"] is None


async def test_update_replaces_current_body(notes_client):
    client, _ = notes_client
    await _ingest(client, "수정노트", "이전 본문.")
    updated = await _ingest(client, "수정노트", "새 본문.")

    assert updated["action"] == "updated"
    page = (await client.get("/api/page/수정노트")).json()["page"]
    assert page["body"] == "새 본문."

    results = (await client.get("/api/search", params={"q": "새 본문"})).json()["results"]
    assert any(r["title"] == "수정노트" for r in results)


async def test_note_user_isolation(clean_db):
    """두 유저가 동명 노트를 각자 가지며 서로의 목록/조회에 안 섞인다."""
    pool = clean_db
    from app.db import execute

    await execute(pool, "INSERT INTO users (email, name) VALUES ('u2@x.com', 'U2')")
    u2 = (await fetchrow(pool, "SELECT id FROM users WHERE email='u2@x.com'"))["id"]

    llm = FakeLLMClient(complete_fn=lambda p, t: "t")
    app1 = build_app(pool, llm=llm)  # FAKE_USER id=1
    app2 = build_app(pool, llm=llm, user={"id": u2, "email": "u2@x.com", "name": "U2"})
    async with AsyncClient(transport=ASGITransport(app=app1), base_url="http://test") as a1, \
               AsyncClient(transport=ASGITransport(app=app2), base_url="http://test") as a2:
        # 같은 제목 "메모"를 각자 다른 본문으로 저장 → (user_id, title) 유니크로 공존.
        await _ingest(a1, "메모", "유저1 본문", ["공통"])
        await _ingest(a2, "메모", "유저2 본문", ["공통"])

        # 목록은 각자 1건씩, 본문도 각자 것.
        p1 = (await a1.get("/api/pages")).json()["pages"]
        p2 = (await a2.get("/api/pages")).json()["pages"]
        assert [p["title"] for p in p1] == ["메모"]
        assert [p["title"] for p in p2] == ["메모"]
        assert (await a1.get("/api/page/메모")).json()["page"]["body"] == "유저1 본문"
        assert (await a2.get("/api/page/메모")).json()["page"]["body"] == "유저2 본문"

        # 검색도 격리: 유저2 본문 키워드로 유저1이 검색해도 안 나온다.
        hits = (await a1.get("/api/search", params={"q": "유저2 본문"})).json()["results"]
        assert all(h["title"] == "메모" for h in hits)  # 떠도 유저1의 '메모'만(타인 노트 없음)
        titles_bodies = [(h["title"], h["snippet"]) for h in hits]
        assert all("유저2 본문" not in snip for _, snip in titles_bodies)
