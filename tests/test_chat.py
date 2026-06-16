"""채팅 파이프라인 & CRUD 테스트 (FakeLLM + search 모킹)."""

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app import search
from app.db import fetch, fetchrow
from app.llm import FakeLLMClient, LLMError, get_llm
from app.main import create_app


def _hit(note_id, title="노트", text="본문", score=0.5):
    return search.SearchHit(
        chunk_id=note_id * 10,
        note_id=note_id,
        note_title=title,
        text=text,
        score=score,
    )


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """SSE 본문을 (event, data) 리스트로 파싱."""
    events = []
    cur_event = None
    for line in text.splitlines():
        if line.startswith("event:"):
            cur_event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            payload = line[len("data:") :].strip()
            events.append((cur_event, json.loads(payload) if payload else None))
            cur_event = None
    return events


@pytest_asyncio.fixture
async def chat_ctx(clean_db, monkeypatch):
    """앱 + FakeLLM 주입 + search 모킹. (client, pool, llm, search_calls) 반환."""
    pool = clean_db

    # 시드 노트 1개(citations FK 충족용).
    note = await fetchrow(
        pool,
        "INSERT INTO notes (title, body) VALUES (%s, %s) RETURNING id",
        ("연봉협상", "이직 시 연봉 협상 팁."),
    )
    note_id = note["id"]

    # search 모킹: 호출된 쿼리를 기록하고 고정 hit 반환.
    search_calls = []

    async def fake_search(conn, query):
        search_calls.append(query)
        return [_hit(note_id, title="연봉협상")]

    monkeypatch.setattr(search, "search", fake_search)

    # FakeLLM: complete(다시쓰기/제목)와 stream(답변) 모두.
    state = {"complete_calls": 0}

    def complete_fn(prompt, tier):
        state["complete_calls"] += 1
        return "재작성된검색어"

    llm = FakeLLMClient(complete_fn=complete_fn, stream_chunks=["안", "녕"])

    from tests._helpers import build_app

    app = build_app(pool, llm=llm)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, pool, llm, state, search_calls, note_id


async def test_sse_event_order(chat_ctx):
    client, *_ = chat_ctx
    resp = await client.get("/api/chat", params={"q": "연봉 협상 팁", "conv": 0})
    events = [e for e, _ in _parse_sse(resp.text)]
    assert events[0] == "conversation"
    assert "sources" in events
    assert events.index("sources") < events.index("answer")
    assert events.count("answer") >= 1
    assert events[-1] == "done"


async def test_first_question_no_rewrite(chat_ctx):
    client, pool, llm, state, search_calls, _ = chat_ctx
    await client.get("/api/chat", params={"q": "원문질문", "conv": 0})
    # 첫 질문 → 다시쓰기 생략의 결정적 증거: 검색이 원문 그대로 수행됨.
    assert search_calls == ["원문질문"]
    # 다시쓰기 complete는 0회. 단, 첫 턴이면 제목 자동생성 complete가 1회(low) 발생.
    assert state["complete_calls"] == 1
    assert llm.last_tier == "low"


async def test_followup_rewrite(chat_ctx):
    client, pool, llm, state, search_calls, _ = chat_ctx
    # 1턴 만들기.
    r1 = await client.get("/api/chat", params={"q": "첫 질문", "conv": 0})
    conv_id = _parse_sse(r1.text)[0][1]["id"]
    state["complete_calls"] = 0
    search_calls.clear()

    await client.get("/api/chat", params={"q": "후속 질문", "conv": conv_id})
    assert state["complete_calls"] == 1  # 다시쓰기 1회
    assert search_calls == ["재작성된검색어"]  # 원문이 아니라 재작성된 쿼리로 검색


async def test_prompt_assembly(chat_ctx):
    client, pool, *_ = chat_ctx
    r = await client.get("/api/chat", params={"q": "원문질문이다", "conv": 0})
    conv_id = _parse_sse(r.text)[0][1]["id"]
    # 저장된 sent_prompt 확인.
    row = await fetchrow(
        pool,
        "SELECT sent_prompt FROM messages WHERE conv_id=%s AND role='assistant'",
        (conv_id,),
    )
    p = row["sent_prompt"]
    assert "[지시]" in p
    assert "[참고자료]" in p
    assert "[질문]\n원문질문이다" in p  # 질문은 원문
    assert "재작성된검색어" not in p  # 다시쓴 쿼리는 프롬프트에 안 들어감


async def test_citations_note_id(chat_ctx):
    client, pool, llm, state, search_calls, note_id = chat_ctx
    r = await client.get("/api/chat", params={"q": "질문", "conv": 0})
    conv_id = _parse_sse(r.text)[0][1]["id"]
    rows = await fetch(
        pool,
        "SELECT mc.note_id FROM message_citations mc "
        "JOIN messages m ON m.id=mc.message_id WHERE m.conv_id=%s",
        (conv_id,),
    )
    assert {row["note_id"] for row in rows} == {note_id}


async def test_crud(chat_ctx):
    client, pool, *_ = chat_ctx
    # 대화 생성.
    r = await client.get("/api/chat", params={"q": "안녕", "conv": 0})
    conv_id = _parse_sse(r.text)[0][1]["id"]

    # 목록.
    lst = (await client.get("/api/conversations")).json()
    assert any(c["id"] == conv_id for c in lst["conversations"])

    # 상세(메시지 + sources).
    detail = (await client.get(f"/api/conversations/{conv_id}")).json()
    assert detail["id"] == conv_id
    assert len(detail["messages"]) == 2  # user + assistant
    assistant = next(m for m in detail["messages"] if m["role"] == "assistant")
    assert len(assistant["sources"]) >= 1

    # 이름변경.
    patched = (
        await client.patch(f"/api/conversations/{conv_id}", json={"title": "새제목"})
    ).json()
    assert patched == {"ok": True, "title": "새제목"}

    # 삭제.
    deleted = (await client.delete(f"/api/conversations/{conv_id}")).json()
    assert deleted == {"deleted": True, "id": conv_id}
    after = (await client.get("/api/conversations")).json()
    assert all(c["id"] != conv_id for c in after["conversations"])


async def test_error_event(chat_ctx):
    client, pool, llm, *_ = chat_ctx

    # stream이 LLMError를 던지도록 교체.
    async def boom(prompt, *, tier="default"):
        raise LLMError("openclaw down")
        yield  # pragma: no cover

    llm.stream = boom

    r = await client.get("/api/chat", params={"q": "질문", "conv": 0})
    events = _parse_sse(r.text)
    assert any(e == "error" for e, _ in events)
    err = next(d for e, d in events if e == "error")
    assert "openclaw down" in err["message"]
