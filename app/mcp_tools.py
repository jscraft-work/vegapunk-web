"""MCP 도구 구현 (Task v2-03) — 서버측 LLM 0회.

기존 서비스 함수(search·distill_match·ingest·indexing)를 호출하는 *얇은 래퍼*.
검색/병합/인덱싱 로직을 재작성하지 않는다. 모든 함수는 인증된 `user_id`로 스코프한다
(멀티유저 데이터 누수 방지). 여기 함수들은 (pool, user_id, ...) 시그니처라 테스트가
직접 호출·검증할 수 있다. HTTP/토큰 인증은 app/mcp_server.py가 감싼다.
"""

from __future__ import annotations

from app import distill_match, indexing, search
from app.config import get_settings
from app.db import fetch, fetchrow
from app.ingest import _set_tags, ingest_note as _ingest_note
from app.session import stash_link_token

# search_notes 기본 반환 개수(게이트 없음). 상한은 search.MCP_TOP_K_LIMIT(30).
DEFAULT_LIMIT = 15
SNIPPET_LEN = 800


async def search_notes(pool, user_id: int, query: str, limit: int | None = None) -> list[dict]:
    """글자+벡터+RRF+그래프 → 노트/청크 원문 반환(게이트·다시쓰기·답변 없음).

    빈 결과는 []로 명확히(호출측이 '해당 노트 없음'이라 답하도록). note_id 기준 중복
    제거(최고 점수 청크를 snippet으로). 전체 본문이 필요하면 get_note(title)를 쓴다.
    """
    limit = int(limit) if limit else DEFAULT_LIMIT
    async with pool.connection() as conn:
        hits = await search.search(
            conn, query, user_id, apply_gate=False, top_k=limit
        )
    best: dict[int, dict] = {}
    for h in hits:
        cur = best.get(h.note_id)
        if cur is None or h.score > cur["score"]:
            best[h.note_id] = {
                "note_id": h.note_id,
                "title": h.note_title,
                "snippet": h.text[:SNIPPET_LEN],
                "score": round(h.score, 4),
            }
    return sorted(best.values(), key=lambda r: r["score"], reverse=True)


async def find_merge_target(pool, user_id: int, title: str, body: str) -> dict | None:
    """병합 대상 노트만 찾는다(LLM 없음). {note_id, title, similarity} 또는 None."""
    return await distill_match.find_merge_target(pool, user_id, title, body)


async def ingest_note(
    pool,
    user_id: int,
    title: str,
    body: str,
    tags: list[str] | None = None,
    merge_into: int | None = None,
) -> dict:
    """노트 저장 + 동기 인덱싱(저장 직후 검색 가능). {note_id, title, action}."""
    return await _ingest_note(
        pool,
        user_id=user_id,
        title=title,
        body=body,
        tags=tags or [],
        merge_into=merge_into,
    )


async def get_note(pool, user_id: int, title: str) -> dict | None:
    """제목으로 노트 열람(유저 스코프). 없으면 None."""
    note = await fetchrow(
        pool,
        "SELECT id, title, body, updated_at FROM notes WHERE user_id = %s AND title = %s",
        (user_id, title),
    )
    if note is None:
        return None
    tags = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = %s",
        (note["id"],),
    )
    return {
        "note_id": note["id"],
        "title": note["title"],
        "body": note["body"],
        "tags": [t["name"] for t in tags],
        "updated": note["updated_at"].isoformat(),
    }


async def list_notes(pool, user_id: int, tag: str | None = None) -> list[dict]:
    """노트 목록(유저 스코프). tag 주면 그 태그만."""
    if tag:
        rows = await fetch(
            pool,
            "SELECT n.id, n.title, n.updated_at FROM notes n "
            "JOIN note_tags nt ON nt.note_id = n.id "
            "JOIN tags t ON t.id = nt.tag_id "
            "WHERE n.user_id = %s AND t.name = %s ORDER BY n.updated_at DESC",
            (user_id, tag),
        )
    else:
        rows = await fetch(
            pool,
            "SELECT id, title, updated_at FROM notes "
            "WHERE user_id = %s ORDER BY updated_at DESC",
            (user_id,),
        )
    return [
        {"note_id": r["id"], "title": r["title"], "updated": r["updated_at"].isoformat()}
        for r in rows
    ]


async def update_note(
    pool,
    user_id: int,
    title: str,
    body: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    """노트 수정(본문/태그). 본문 변경 시 재인덱싱. 유저 스코프. 없으면 {"error":...}."""
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s", (user_id, title)
    )
    if note is None:
        return {"error": "not found", "title": title}
    note_id = note["id"]
    async with pool.connection() as conn:
        async with conn.transaction():
            if body is not None:
                await conn.execute(
                    "UPDATE notes SET body = %s WHERE id = %s", (body, note_id)
                )
            if tags is not None:
                await _set_tags(conn, note_id, tags)
    if body is not None:
        # 본문이 바뀌면 청크/링크 재인덱싱(is_new=False → 인바운드 링크 재해소 생략).
        await indexing.index_after_save(pool, note_id, is_new=False)
    return {"note_id": note_id, "title": title, "action": "updated"}


async def delete_note(pool, user_id: int, title: str) -> dict:
    """노트 삭제(유저 스코프). 인바운드 링크는 NULL 처리(행 보존)."""
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s", (user_id, title)
    )
    if note is None:
        return {"error": "not found", "title": title}
    async with pool.connection() as conn:
        async with conn.transaction():
            await indexing.unresolve_links_to(conn, note["id"])
            await conn.execute("DELETE FROM notes WHERE id = %s", (note["id"],))
    return {"deleted": True, "title": title}


async def link_account(store, user_id: int, provider: str = "github") -> dict:
    """다른 신원(provider)을 현재 계정에 연동 시작 — 일회용 링크 URL 반환(Task 01)."""
    import secrets

    token = secrets.token_urlsafe(24)
    await stash_link_token(store, token, user_id)
    base = get_settings().OAUTH_REDIRECT_BASE
    return {"url": f"{base}/auth/login/{provider}?link={token}"}
