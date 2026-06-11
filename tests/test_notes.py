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


async def test_versions_and_restore(notes_client):
    client, pool = notes_client
    await _ingest(client, "버전노트", "버전1 본문.")
    await _ingest(client, "버전노트", "버전2 본문.")  # 수정 → 버전 적재

    versions = (await client.get("/api/page/버전노트/versions")).json()["versions"]
    assert len(versions) >= 1
    v_id = versions[-1]["id"]  # 가장 오래된(버전1 백업)
    body = (await client.get(f"/api/page/버전노트/versions/{v_id}")).json()["body"]
    assert body == "버전1 본문."

    restored = (
        await client.post("/api/page/버전노트/restore", json={"version_id": v_id})
    ).json()
    assert restored["action"] == "restored"
    page = (await client.get("/api/page/버전노트")).json()["page"]
    assert page["body"] == "버전1 본문."  # 본문 교체됨
