"""노트/지식 API (기획서 14.2 C/D).

열람·편집·검색·태그·버전 이력. 저장은 공유 `/api/ingest`(동기 인덱싱 +
버전 백업). 삭제는 03 unresolve_links_to로 인바운드 링크를 NULL 처리.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request

from app import indexing, search
from app import settings as app_settings
from app.db import execute, fetch, fetchrow
from app.ingest import ingest_note
from app.llm import LLMClient, get_llm

router = APIRouter()


# ── 목록/검색 ──────────────────────────────────────────────────


@router.get("/api/pages")
async def list_pages(request: Request, tag: str | None = None) -> dict:
    pool = request.app.state.pool
    if tag:
        rows = await fetch(
            pool,
            "SELECT n.title, n.updated_at FROM notes n "
            "JOIN note_tags nt ON nt.note_id = n.id "
            "JOIN tags t ON t.id = nt.tag_id "
            "WHERE t.name = %s ORDER BY n.updated_at DESC",
            (tag,),
        )
    else:
        rows = await fetch(
            pool, "SELECT title, updated_at FROM notes ORDER BY updated_at DESC"
        )
    pages = []
    for r in rows:
        tags = await fetch(
            pool,
            "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
            "JOIN notes n ON n.id = nt.note_id WHERE n.title = %s",
            (r["title"],),
        )
        pages.append(
            {
                "title": r["title"],
                "tags": [t["name"] for t in tags],
                "updated": r["updated_at"].isoformat(),
            }
        )
    return {"pages": pages}


@router.get("/api/tags")
async def list_tags(request: Request) -> dict:
    rows = await fetch(
        request.app.state.pool,
        "SELECT t.name, count(nt.note_id) AS c FROM tags t "
        "LEFT JOIN note_tags nt ON nt.tag_id = t.id "
        "GROUP BY t.name ORDER BY c DESC, t.name",
    )
    return {"tags": [{"tag": r["name"], "count": r["c"]} for r in rows]}


@router.get("/api/search")
async def search_notes(request: Request, q: str) -> dict:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        hits = await search.search(conn, q)
    # note_id 기준 중복 제거(최고 점수 청크를 snippet으로).
    best: dict[int, dict] = {}
    for h in hits:
        cur = best.get(h.note_id)
        if cur is None or h.score > cur["score"]:
            best[h.note_id] = {
                "note_id": h.note_id,
                "title": h.note_title,
                "snippet": h.text[:200],
                "score": h.score,
            }
    results = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    return {"results": results}


# ── 관리 테이블 / 백링크 (옛 프론트 호환) ──────────────────────


@router.get("/api/manage")
async def manage_list(request: Request) -> dict:
    """관리 뷰: 노트별 글자수·아웃링크·백링크·고아 여부."""
    rows = await fetch(
        request.app.state.pool,
        "SELECT n.title, n.updated_at, length(n.body) AS len, "
        "(SELECT count(*) FROM edges e WHERE e.src_note = n.id) AS outlinks, "
        "(SELECT count(*) FROM edges e WHERE e.dst_note = n.id) AS backlinks "
        "FROM notes n ORDER BY n.updated_at DESC",
    )
    return {
        "pages": [
            {
                "title": r["title"],
                "updated": r["updated_at"].isoformat(),
                "len": r["len"],
                "outlinks": r["outlinks"],
                "backlinks": r["backlinks"],
                "orphan": r["backlinks"] == 0,
            }
            for r in rows
        ]
    }


@router.get("/api/backlinks/{title}")
async def backlinks_of(request: Request, title: str) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(pool, "SELECT id FROM notes WHERE title = %s", (title,))
    if note is None:
        return {"backlinks": []}
    rows = await fetch(
        pool,
        "SELECT n.title FROM edges e JOIN notes n ON n.id = e.src_note "
        "WHERE e.dst_note = %s",
        (note["id"],),
    )
    return {"backlinks": [r["title"] for r in rows]}


# ── 런타임 설정 (즉시반영 + Redis 백업) ────────────────────────


@router.get("/api/settings")
async def read_settings(request: Request) -> dict:
    return app_settings.all_settings()


@router.post("/api/settings")
async def write_settings(request: Request, body: dict) -> dict:
    redis = getattr(request.app.state, "session_store", None)
    for key in app_settings.all_settings():
        if key in body:
            await app_settings.update(redis, key, float(body[key]))
    return app_settings.all_settings()


# ── 노트 상세 (위키링크/백링크) ────────────────────────────────


@router.get("/api/page/{title}")
async def get_page(request: Request, title: str) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool,
        "SELECT id, title, body, updated_at FROM notes WHERE title = %s",
        (title,),
    )
    if note is None:
        return {"error": "not found"}
    tags = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = %s",
        (note["id"],),
    )
    titles = [r["title"] for r in await fetch(pool, "SELECT title FROM notes")]
    backlinks = await fetch(
        pool,
        "SELECT n.title FROM edges e JOIN notes n ON n.id = e.src_note "
        "WHERE e.dst_note = %s",
        (note["id"],),
    )
    return {
        "page": {
            "title": note["title"],
            "body": note["body"],
            "tags": [t["name"] for t in tags],
            "updated": note["updated_at"].isoformat(),
            "note_id": note["id"],
        },
        "titles": titles,
        "backlinks": [b["title"] for b in backlinks],
    }


# ── 편집/태그/삭제 ─────────────────────────────────────────────


@router.post("/api/ingest")
async def ingest(request: Request, body: dict) -> dict:
    """노트 저장(신규/수정/병합). 08/09 공유 — merge_into 유무로 분기. 동기 인덱싱."""
    return await ingest_note(
        request.app.state.pool,
        title=body["title"],
        body=body["body"],
        tags=body.get("tags", []),
        merge_into=body.get("merge_into"),
        source=body.get("source", "manual"),
    )


@router.post("/api/page/{title}/tags")
async def replace_tags(request: Request, title: str, body: dict) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(pool, "SELECT id FROM notes WHERE title = %s", (title,))
    if note is None:
        return {"error": "not found"}
    tags = body.get("tags", [])
    async with pool.connection() as conn:
        async with conn.transaction():
            from app.ingest import _set_tags

            normalized = await _set_tags(conn, note["id"], tags)
    return {"ok": True, "tags": normalized}


@router.post("/api/page/{title}/suggest-tags")
async def suggest_tags(
    request: Request, title: str, llm: LLMClient = Depends(get_llm)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(pool, "SELECT body FROM notes WHERE title = %s", (title,))
    if note is None:
        return {"error": "not found"}
    existing = [r["name"] for r in await fetch(pool, "SELECT name FROM tags")]
    prompt = (
        "다음 노트에 어울리는 태그 3~5개를 쉼표로 구분해 한 줄로만 출력하라. "
        f"가능하면 기존 태그({', '.join(existing) or '없음'})를 재사용하라.\n\n"
        f"제목: {title}\n본문:\n{note['body']}"
    )
    raw = await llm.complete(prompt, tier="low")
    tags = [t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()]
    return {"tags": tags}


@router.delete("/api/page/{title}")
async def delete_page(request: Request, title: str) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(pool, "SELECT id FROM notes WHERE title = %s", (title,))
    if note is None:
        return {"error": "not found"}
    async with pool.connection() as conn:
        async with conn.transaction():
            # 삭제 전 인바운드 edges의 dst_note를 NULL로(행은 보존).
            await indexing.unresolve_links_to(conn, note["id"])
            # 노트 삭제 → chunks/edges(src) CASCADE 정리.
            await conn.execute("DELETE FROM notes WHERE id = %s", (note["id"],))
    return {"deleted": True, "title": title}


# ── 버전 이력 / 되돌리기 ───────────────────────────────────────


@router.get("/api/page/{title}/versions")
async def list_versions(request: Request, title: str) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(pool, "SELECT id FROM notes WHERE title = %s", (title,))
    if note is None:
        return {"error": "not found"}
    rows = await fetch(
        pool,
        "SELECT id, source, created_at FROM note_versions "
        "WHERE note_id = %s ORDER BY id DESC",
        (note["id"],),
    )
    return {
        "versions": [
            {"id": r["id"], "source": r["source"], "created_at": r["created_at"].isoformat()}
            for r in rows
        ]
    }


@router.get("/api/page/{title}/versions/{vid}")
async def get_version(request: Request, title: str, vid: int) -> dict:
    row = await fetchrow(
        request.app.state.pool,
        "SELECT body FROM note_versions WHERE id = %s", (vid,),
    )
    if row is None:
        return {"error": "not found"}
    return {"body": row["body"]}


@router.post("/api/page/{title}/restore")
async def restore_version(request: Request, title: str, body: dict) -> dict:
    """버전 본문으로 되돌리기 = 저장의 일종(현재본 백업 + 재인덱싱)."""
    pool = request.app.state.pool
    version_id = body["version_id"]
    ver = await fetchrow(
        pool, "SELECT body FROM note_versions WHERE id = %s", (version_id,)
    )
    if ver is None:
        return {"error": "version not found"}
    result = await ingest_note(
        pool, title=title, body=ver["body"], tags=await _current_tags(pool, title),
        source="restore",
    )
    return {"ok": True, "action": "restored", "note_id": result["note_id"]}


async def _current_tags(pool, title: str) -> list[str]:
    rows = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "JOIN notes n ON n.id = nt.note_id WHERE n.title = %s",
        (title,),
    )
    return [r["name"] for r in rows]
