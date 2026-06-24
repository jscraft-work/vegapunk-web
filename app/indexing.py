"""노트 부분 재인덱싱 (기획서 13장).

노트가 생기거나 바뀌면 **그 노트만** 재인덱싱한다: 청크 교체 + 배치 임베딩,
위키링크 → edges 재생성. 핵심 함수 `reindex_note`는 **호출자가 연 트랜잭션
안에서** 동작하며 스스로 commit 하지 않는다(트랜잭션 경계는 호출자 소유).
단일 트랜잭션이어야 검색(04)이 "청크는 새것, 임베딩은 빈것" 같은 중간 상태를
보지 않는다.
"""

from __future__ import annotations

from app import embedding
from app.chunking import split_into_chunks
from app.wikilink import extract_links


async def reindex_note(conn, note_id: int) -> None:
    """호출자 트랜잭션 안에서 단일 노트를 재인덱싱(commit 안 함)."""
    cur = await conn.execute(
        "SELECT title, body, user_id FROM notes WHERE id = %s", (note_id,)
    )
    row = await cur.fetchone()
    if row is None:
        raise ValueError(f"note {note_id} not found")
    _title, body, user_id = row[0], row[1], row[2]

    # 기존 청크 제거(gin/hnsw 인덱스는 자동 정리).
    await conn.execute("DELETE FROM chunks WHERE note_id = %s", (note_id,))

    # 청크 분할 → 한 번에 배치 임베딩(1개씩 호출 금지).
    texts = split_into_chunks(body)
    if texts:
        embeddings = await embedding.aembed_passages(texts)
        for ord_, (text, emb) in enumerate(zip(texts, embeddings)):
            await conn.execute(
                "INSERT INTO chunks (note_id, ord, text, embedding) "
                "VALUES (%s, %s, %s, %s)",
                (note_id, ord_, text, emb),
            )

    # edges 재생성: 기존 src 링크 삭제 후 본문 링크로 다시 채움.
    await conn.execute("DELETE FROM edges WHERE src_note = %s", (note_id,))
    for dst_title in extract_links(body):
        cur = await conn.execute(
            "SELECT id FROM notes WHERE user_id = %s AND title = %s",
            (user_id, dst_title),
        )
        dst = await cur.fetchone()
        dst_note = dst[0] if dst else None  # 같은 유저 동명 노트 없으면 미해결(NULL)
        await conn.execute(
            "INSERT INTO edges (src_note, dst_title, dst_note) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (src_note, dst_title, kind) "
            "DO UPDATE SET dst_note = EXCLUDED.dst_note",
            (note_id, dst_title, dst_note),
        )

    await conn.execute(
        "UPDATE notes SET updated_at = now() WHERE id = %s", (note_id,)
    )


async def resolve_inbound_links(conn, title: str, note_id: int, user_id: int) -> None:
    """신규 노트 생성 시: 이 제목을 가리키던 미해결 edges를 채운다.

    같은 유저의 노트(src_note)가 건 링크만 해결한다(타 유저 동명 노트와 격리).
    """
    await conn.execute(
        "UPDATE edges e SET dst_note = %s "
        "FROM notes s "
        "WHERE e.src_note = s.id AND s.user_id = %s "
        "AND e.dst_title = %s AND e.dst_note IS NULL",
        (note_id, user_id, title),
    )


async def unresolve_links_to(conn, note_id: int) -> None:
    """노트 삭제 직전 호출: 이 노트를 가리키던 edges의 dst_note만 NULL로.

    edges 행 자체는 유지한다. (edges.dst_note 는 ON DELETE CASCADE 이므로,
    노트를 실제 삭제하기 *전에* 호출해야 인바운드 링크 행이 보존된다.)
    """
    await conn.execute(
        "UPDATE edges SET dst_note = NULL WHERE dst_note = %s", (note_id,)
    )


async def index_after_save(pool, note_id: int, *, is_new: bool) -> None:
    """동기 저장 경로(09)용 편의 진입점: 트랜잭션을 열고 재인덱싱 후 commit."""
    async with pool.connection() as conn:
        async with conn.transaction():
            await reindex_note(conn, note_id)
            if is_new:
                cur = await conn.execute(
                    "SELECT title, user_id FROM notes WHERE id = %s", (note_id,)
                )
                title, user_id = await cur.fetchone()
                await resolve_inbound_links(conn, title, note_id, user_id)
