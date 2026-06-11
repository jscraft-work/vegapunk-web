"""노트 저장(ingest) 공유 로직 (Task 08/09 공용).

`/api/ingest`는 08(distill 저장)과 09(수동 편집 저장)가 한 구현을 공유한다.
저장 트랜잭션: (병합/수정이면) `note_versions`에 **이전 본문 백업** → notes upsert →
tags/note_tags 정규화 → 인덱싱(03). append 금지(본문 통째 교체).

인덱싱 정책:
- 단건(수동/단일 후보) → 동기(`index_after_save`, 저장 직후 검색 가능).
- distill 다건 → BackgroundTasks 비동기(완료 전 그 노트만 잠시 검색서 빠짐).
"""

from __future__ import annotations

from app import indexing
from app.db import fetchrow


async def _set_tags(conn, note_id: int, tags: list[str]) -> list[str]:
    """note_tags를 주어진 태그로 교체(기존 태그 재사용, 없으면 생성)."""
    await conn.execute("DELETE FROM note_tags WHERE note_id = %s", (note_id,))
    normalized: list[str] = []
    for raw in tags or []:
        name = raw.strip()
        if not name or name in normalized:
            continue
        normalized.append(name)
        row = await (
            await conn.execute(
                "INSERT INTO tags (name) VALUES (%s) "
                "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
                (name,),
            )
        ).fetchone()
        await conn.execute(
            "INSERT INTO note_tags (note_id, tag_id) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            (note_id, row[0]),
        )
    return normalized


async def _write_note(
    pool, *, title: str, body: str, tags: list[str], merge_into: int | None, source: str
) -> tuple[int, str, str, bool]:
    """DB 쓰기(단일 트랜잭션). (note_id, title, action, is_new) 반환."""
    async with pool.connection() as conn:
        async with conn.transaction():
            if merge_into is not None:
                # 병합: 대상 노트 본문 교체(이전 본문 백업). 정체성은 대상 노트.
                old = await (
                    await conn.execute(
                        "SELECT title, body FROM notes WHERE id = %s", (merge_into,)
                    )
                ).fetchone()
                if old is None:
                    raise ValueError(f"merge target {merge_into} not found")
                await conn.execute(
                    "INSERT INTO note_versions (note_id, body, source) VALUES (%s, %s, %s)",
                    (merge_into, old[1], source),
                )
                await conn.execute(
                    "UPDATE notes SET body = %s WHERE id = %s", (body, merge_into)
                )
                await _set_tags(conn, merge_into, tags)
                return merge_into, old[0], "merged", False

            # merge_into 없음 → 제목 기준 신규/수정.
            existing = await (
                await conn.execute(
                    "SELECT id, body FROM notes WHERE title = %s", (title,)
                )
            ).fetchone()
            if existing is None:
                row = await (
                    await conn.execute(
                        "INSERT INTO notes (title, body) VALUES (%s, %s) RETURNING id",
                        (title, body),
                    )
                ).fetchone()
                note_id, action, is_new = row[0], "created", True
            else:
                note_id = existing[0]
                await conn.execute(
                    "INSERT INTO note_versions (note_id, body, source) VALUES (%s, %s, %s)",
                    (note_id, existing[1], source),
                )
                await conn.execute(
                    "UPDATE notes SET body = %s WHERE id = %s", (body, note_id)
                )
                action, is_new = "updated", False
            await _set_tags(conn, note_id, tags)
            return note_id, title, action, is_new


async def ingest_note(
    pool,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    merge_into: int | None = None,
    source: str = "manual",
    background_tasks=None,
) -> dict:
    """노트 저장 + 인덱싱. `background_tasks` 주어지면 비동기 인덱싱."""
    note_id, final_title, action, is_new = await _write_note(
        pool,
        title=title,
        body=body,
        tags=tags or [],
        merge_into=merge_into,
        source=source,
    )
    if background_tasks is not None:
        background_tasks.add_task(
            indexing.index_after_save, pool, note_id, is_new=is_new
        )
    else:
        await indexing.index_after_save(pool, note_id, is_new=is_new)
    return {"note_id": note_id, "title": final_title, "action": action}
