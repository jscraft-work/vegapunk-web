"""MCP 도구 테스트 (Task v2-03).

도구 구현(app/mcp_tools.py)은 (pool, user_id, ...) 시그니처라 user_id를 직접 주입해
스코프·동작을 검증한다. 인증 게이트(토큰→user_id)는 app/mcp_server.py 래퍼가 담당하며
test_tool_requires_auth로 별도 검증한다.
"""

import pytest_asyncio

from app import indexing, mcp_tools, mcp_server, search
from app.db import fetch, fetchrow


@pytest_asyncio.fixture
async def two_user_kb(clean_db):
    """user 1(clean_db가 생성) + user 2. 각자 노트 인덱싱."""
    pool = clean_db
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO users (email, name) VALUES ('u2@x.com','유저2')")
        await conn.commit()

    async def seed(user_id, title, body):
        row = await fetchrow(
            pool,
            "INSERT INTO notes (user_id, title, body) VALUES (%s,%s,%s) RETURNING id",
            (user_id, title, body),
        )
        await indexing.index_after_save(pool, row["id"], is_new=True)
        return row["id"]

    # user1: 반려동물/연봉협상. user2: 반려동물(다른 내용).
    await seed(1, "반려동물", "고양이와 개는 사랑받는 반려동물이다. 함께 산책하며 교감한다.")
    await seed(1, "연봉협상", "이직할 때 연봉 협상이 중요하다. 시장 가치를 근거로 제시한다.")
    await seed(2, "타인노트", "이것은 다른 유저의 비밀 노트다. 절대 섞이면 안 된다.")
    return pool


async def test_search_notes_scoped(two_user_kb):
    pool = two_user_kb
    # user1 검색에 user2 노트가 섞이지 않아야 한다.
    hits = await mcp_tools.search_notes(pool, 1, "반려동물 고양이")
    titles = {h["title"] for h in hits}
    assert "반려동물" in titles
    assert "타인노트" not in titles
    # user2는 자기 노트만.
    hits2 = await mcp_tools.search_notes(pool, 2, "비밀 노트")
    assert all(h["title"] == "타인노트" for h in hits2)


async def test_search_no_gate_returns_more(two_user_kb):
    pool = two_user_kb
    # 무관에 가까운 질의에서 게이트 없는(MCP) 검색이 게이트 적용(웹)보다 후보를 더/같게 반환.
    q = "블랙홀 우주 물리"
    async with pool.connection() as conn:
        gated = await search.search(conn, q, 1, apply_gate=True, top_k=30)
        ungated = await search.search(conn, q, 1, apply_gate=False, top_k=30)
    assert len(ungated) >= len(gated)
    assert len(ungated) <= search.MCP_TOP_K_LIMIT


async def test_search_empty_returns_empty(two_user_kb):
    pool = two_user_kb
    # 인덱싱된 근거가 전혀 없는 유저 → []. (환각 유도 텍스트 없이 빈 리스트)
    empty = await mcp_tools.search_notes(pool, 999, "아무 질의나")
    assert empty == []


async def test_ingest_then_search(two_user_kb):
    pool = two_user_kb
    res = await mcp_tools.ingest_note(
        pool, 1, "새노트제목", "파이썬 비동기 프로그래밍과 asyncio 이벤트 루프 정리."
    )
    assert res["action"] in ("created", "updated")
    # 동기 인덱싱 → 즉시 검색됨.
    hits = await mcp_tools.search_notes(pool, 1, "파이썬 asyncio 이벤트 루프")
    assert any(h["title"] == "새노트제목" for h in hits)


async def test_find_merge_target(two_user_kb):
    pool = two_user_kb
    # 제목 정규화 일치 → 강한 대상.
    tgt = await mcp_tools.find_merge_target(pool, 1, "연봉 협상", "연봉 협상 관련 메모")
    assert tgt is not None
    assert tgt["title"] == "연봉협상"
    # 전혀 무관한 새 주제 → None.
    none = await mcp_tools.find_merge_target(
        pool, 1, "완전히새로운주제XYZ", "심해 열수분출공 생태계에 관한 독립적인 글."
    )
    assert none is None


async def test_get_list_update_delete(two_user_kb):
    pool = two_user_kb
    # get_note: user1 스코프.
    note = await mcp_tools.get_note(pool, 1, "연봉협상")
    assert note and note["title"] == "연봉협상"
    # user2가 user1 노트 요청 → None(스코프).
    assert await mcp_tools.get_note(pool, 2, "연봉협상") is None

    # list_notes: user1은 자기 노트만.
    listed = await mcp_tools.list_notes(pool, 1)
    titles = {n["title"] for n in listed}
    assert "타인노트" not in titles and "연봉협상" in titles

    # update_note: 본문 교체 + 재인덱싱.
    up = await mcp_tools.update_note(pool, 1, "연봉협상", body="연봉 협상 개정판: 데이터 근거 강화.")
    assert up["action"] == "updated"
    got = await mcp_tools.get_note(pool, 1, "연봉협상")
    assert "개정판" in got["body"]
    # 태그 갱신.
    await mcp_tools.update_note(pool, 1, "연봉협상", tags=["커리어", "협상"])
    got2 = await mcp_tools.get_note(pool, 1, "연봉협상")
    assert set(got2["tags"]) == {"커리어", "협상"}
    # 없는 노트 수정 → error.
    assert (await mcp_tools.update_note(pool, 1, "없는노트", body="x")).get("error")

    # delete_note: 스코프 — user2가 user1 노트 삭제 시도 → not found.
    assert (await mcp_tools.delete_note(pool, 2, "연봉협상")).get("error")
    d = await mcp_tools.delete_note(pool, 1, "연봉협상")
    assert d["deleted"] is True
    assert await mcp_tools.get_note(pool, 1, "연봉협상") is None


async def test_tool_requires_auth():
    # 인증 컨텍스트(ContextVar) 미설정 → 도구 래퍼가 거부.
    import pytest

    with pytest.raises(mcp_server.AuthError):
        await mcp_server.tool_search_notes("아무거나")
    with pytest.raises(mcp_server.AuthError):
        await mcp_server.tool_list_notes()
