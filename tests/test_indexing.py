"""인덱싱 파이프라인 테스트(DB + 임베딩 모델 사용)."""

import pytest

from app import indexing
from app.db import execute, fetch, fetchrow


async def _insert_note(pool, title: str, body: str) -> int:
    row = await fetchrow(
        pool,
        "INSERT INTO notes (title, body) VALUES (%s, %s) RETURNING id",
        (title, body),
    )
    return row["id"]


async def test_reindex(clean_db):
    pool = clean_db
    body = "# 제목\n\n비건은 연구자다. [[비건]] 참고.\n\n둘째 문단이다."
    nid = await _insert_note(pool, "노트A", body)

    await indexing.index_after_save(pool, nid, is_new=True)

    rows = await fetch(
        pool,
        "SELECT text, embedding FROM chunks WHERE note_id = %s ORDER BY ord",
        (nid,),
    )
    assert len(rows) > 0
    for r in rows:
        assert r["embedding"] is not None
        assert len(r["embedding"]) == 1024

    edges = await fetch(
        pool, "SELECT dst_title FROM edges WHERE src_note = %s", (nid,)
    )
    assert {e["dst_title"] for e in edges} == {"비건"}


async def test_partial(clean_db):
    pool = clean_db
    a = await _insert_note(pool, "A", "첫 본문이다. 길게 적는다.")
    b = await _insert_note(pool, "B", "다른 노트 본문이다.")
    await indexing.index_after_save(pool, a, is_new=True)
    await indexing.index_after_save(pool, b, is_new=True)

    old_a = {
        r["id"] for r in await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (a,))
    }
    old_b = {
        r["id"] for r in await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (b,))
    }
    assert old_a and old_b

    await execute(pool, "UPDATE notes SET body=%s WHERE id=%s", ("완전히 새 본문.", a))
    await indexing.index_after_save(pool, a, is_new=False)

    new_a = {
        r["id"] for r in await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (a,))
    }
    # A의 옛 청크는 전부 교체되고, B의 청크는 불변.
    assert old_a.isdisjoint(new_a)
    new_b = {
        r["id"] for r in await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (b,))
    }
    assert old_b == new_b


async def test_link_resolution(clean_db):
    pool = clean_db
    a = await _insert_note(pool, "출발", "[[미래노트]] 를 참고.")
    await indexing.index_after_save(pool, a, is_new=True)

    e = await fetchrow(
        pool,
        "SELECT dst_note FROM edges WHERE src_note=%s AND dst_title=%s",
        (a, "미래노트"),
    )
    assert e["dst_note"] is None  # 동명 노트 없음 → 미해결

    # 동명 노트 생성 시 자동 해소.
    b = await _insert_note(pool, "미래노트", "내용이다.")
    await indexing.index_after_save(pool, b, is_new=True)
    e = await fetchrow(
        pool,
        "SELECT dst_note FROM edges WHERE src_note=%s AND dst_title=%s",
        (a, "미래노트"),
    )
    assert e["dst_note"] == b

    # 그 노트 unresolve 시 다시 NULL(행은 유지).
    async with pool.connection() as conn:
        async with conn.transaction():
            await indexing.unresolve_links_to(conn, b)
    e = await fetchrow(
        pool,
        "SELECT dst_note FROM edges WHERE src_note=%s AND dst_title=%s",
        (a, "미래노트"),
    )
    assert e["dst_note"] is None


async def test_transaction(clean_db):
    pool = clean_db
    a = await _insert_note(pool, "A", "본문 문장 하나. 둘. 셋.")

    with pytest.raises(RuntimeError):
        async with pool.connection() as conn:
            async with conn.transaction():
                await indexing.reindex_note(conn, a)
                raise RuntimeError("boom")  # 롤백 유발

    # 예외로 롤백 → 청크가 절반만 들어가지 않음.
    rows = await fetch(pool, "SELECT id FROM chunks WHERE note_id=%s", (a,))
    assert rows == []
