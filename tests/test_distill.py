"""distill 라우트 테스트 (FakeLLM)."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.db import fetch, fetchrow
from app.ingest import ingest_note
from app.llm import FakeLLMClient
from app.routes.distill import extract_json_array
from tests._helpers import build_app


def test_lenient_json():
    # 앞뒤 잡소리 + body에 실제 줄바꿈.
    noisy = (
        '네 결과입니다:\n[\n  {"title":"A","body":"첫 줄\n둘째 줄","tags":["t1"]}\n]\n이상입니다.'
    )
    arr = extract_json_array(noisy)
    assert len(arr) == 1
    assert arr[0]["title"] == "A"
    assert "\n" in arr[0]["body"]  # 줄바꿈 보존
    # 잡담만 → 빈 배열.
    assert extract_json_array("그냥 인사네요. 저장할 거 없어요.") == []
    assert extract_json_array("빈 배열입니다 []") == []
    # 회귀: 서두에 [[위키링크]]/[라벨]이 있어도 진짜 배열을 찾아야 한다(첫 '[' 오인 금지).
    pre = '다음은 [[오이재배]] 관련 [기존 노트] 기반 노트입니다:\n[{"title":"오이","body":"한 포기에 [[지주]] 하나.","tags":["농사"]}]'
    arr2 = extract_json_array(pre)
    assert len(arr2) == 1 and arr2[0]["title"] == "오이"
    assert "[[지주]]" in arr2[0]["body"]  # 본문 속 위키링크는 보존


@pytest_asyncio.fixture
async def distill_client(clean_db):
    pool = clean_db

    def complete_fn(prompt, tier):
        # merge-preview/distill 모두 이 함수를 탄다. 프롬프트로 분기.
        if "통합한 본문" in prompt:
            return "통합된 본문입니다."
        return "[]"

    llm = FakeLLMClient(complete_fn=complete_fn)
    app = build_app(pool, llm=llm)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, pool, llm


async def test_merge_preview(distill_client):
    client, pool, llm = distill_client
    note = await fetchrow(
        pool,
        "INSERT INTO notes (user_id, title, body) VALUES (1, %s,%s) RETURNING id",
        ("연봉협상", "기존 본문: 연봉 5천."),
    )
    r = await client.post(
        "/api/notes/merge-preview",
        json={"target_note_id": note["id"], "candidate_body": "연봉 6천으로 변경."},
    )
    data = r.json()
    assert data["merged_body"] == "통합된 본문입니다."
    assert "diff" in data
    # 보존규칙 프롬프트가 LLM에 전달됐는지.
    assert "보존 규칙" in llm.last_prompt
    assert "삭제하지" in llm.last_prompt


async def test_ingest_merge_replaces_body(clean_db):
    pool = clean_db
    # 기존 노트.
    note = await fetchrow(
        pool,
        "INSERT INTO notes (user_id, title, body) VALUES (1, %s,%s) RETURNING id",
        ("연봉협상", "이전 본문."),
    )
    # 병합 저장.
    result = await ingest_note(
        pool,
        user_id=1,
        title="연봉협상",
        body="통합된 새 본문.",
        tags=["커리어"],
        merge_into=note["id"],
    )
    assert result["action"] == "merged"
    updated = await fetchrow(pool, "SELECT body FROM notes WHERE id=%s", (note["id"],))
    assert updated["body"] == "통합된 새 본문."
    tags = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = %s",
        (note["id"],),
    )
    assert [t["name"] for t in tags] == ["커리어"]


async def test_ingest_reindex(clean_db):
    pool = clean_db
    # 저장(동기 인덱싱) 후 검색에 즉시 반영.
    await ingest_note(
        pool, user_id=1, title="비건연구", body="비건은 인공지능 연구를 한다.", tags=[]
    )
    from app import search

    async with pool.connection() as conn:
        hits = await search.search(conn, "비건 인공지능 연구", 1)
    assert any(h.note_title == "비건연구" for h in hits)
