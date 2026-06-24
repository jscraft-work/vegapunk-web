"""노트/지식 API (기획서 14.2 C/D).

열람·편집·검색·태그·버전 이력. 저장은 공유 `/api/ingest`(동기 인덱싱 +
버전 백업). 삭제는 03 unresolve_links_to로 인바운드 링크를 NULL 처리.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app import indexing, search
from app import settings as app_settings
from app.db import fetch, fetchrow
from app.deps import require_user
from app.ingest import ingest_note
from app.llm import LLMClient, get_llm
from app.llm_text import parse_tag_list

router = APIRouter()


# ── 목록/검색 ──────────────────────────────────────────────────


@router.get("/api/pages")
async def list_pages(
    request: Request, tag: str | None = None, user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    if tag:
        rows = await fetch(
            pool,
            "SELECT n.id, n.title, n.updated_at FROM notes n "
            "JOIN note_tags nt ON nt.note_id = n.id "
            "JOIN tags t ON t.id = nt.tag_id "
            "WHERE n.user_id = %s AND t.name = %s ORDER BY n.updated_at DESC",
            (user["id"], tag),
        )
    else:
        rows = await fetch(
            pool,
            "SELECT id, title, updated_at FROM notes "
            "WHERE user_id = %s ORDER BY updated_at DESC",
            (user["id"],),
        )
    pages = []
    for r in rows:
        tags = await fetch(
            pool,
            "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
            "WHERE nt.note_id = %s",
            (r["id"],),
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
async def list_tags(request: Request, user: dict = Depends(require_user)) -> dict:
    # 그 유저의 노트에 달린 태그만(INNER JOIN으로 0건 태그·타 유저 태그 제외).
    rows = await fetch(
        request.app.state.pool,
        "SELECT t.name, count(*) AS c FROM tags t "
        "JOIN note_tags nt ON nt.tag_id = t.id "
        "JOIN notes n ON n.id = nt.note_id AND n.user_id = %s "
        "GROUP BY t.name ORDER BY c DESC, t.name",
        (user["id"],),
    )
    return {"tags": [{"tag": r["name"], "count": r["c"]} for r in rows]}


@router.get("/api/search")
async def search_notes(request: Request, q: str, user: dict = Depends(require_user)) -> dict:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        hits = await search.search(conn, q, user["id"])
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
async def manage_list(request: Request, user: dict = Depends(require_user)) -> dict:
    """관리 뷰: 노트별 글자수·아웃링크·백링크·고아 여부."""
    rows = await fetch(
        request.app.state.pool,
        "SELECT n.title, n.updated_at, length(n.body) AS len, "
        "(SELECT count(*) FROM edges e WHERE e.src_note = n.id) AS outlinks, "
        "(SELECT count(*) FROM edges e WHERE e.dst_note = n.id) AS backlinks "
        "FROM notes n WHERE n.user_id = %s ORDER BY n.updated_at DESC",
        (user["id"],),
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
async def backlinks_of(
    request: Request, title: str, user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
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
async def get_page(request: Request, title: str, user: dict = Depends(require_user)) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool,
        "SELECT id, title, body, updated_at FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
    if note is None:
        return {"error": "not found"}
    tags = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "WHERE nt.note_id = %s",
        (note["id"],),
    )
    titles = [
        r["title"]
        for r in await fetch(
            pool, "SELECT title FROM notes WHERE user_id = %s", (user["id"],)
        )
    ]
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
async def ingest(request: Request, body: dict, user: dict = Depends(require_user)) -> dict:
    """노트 저장(신규/수정/병합). 08/09 공유 — merge_into 유무로 분기. 동기 인덱싱."""
    return await ingest_note(
        request.app.state.pool,
        user_id=user["id"],
        title=body["title"],
        body=body["body"],
        tags=body.get("tags", []),
        merge_into=body.get("merge_into"),
        source=body.get("source", "manual"),
    )


@router.post("/api/page/{title}/tags")
async def replace_tags(
    request: Request, title: str, body: dict, user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
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
    request: Request, title: str,
    llm: LLMClient = Depends(get_llm), user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool, "SELECT body FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
    if note is None:
        return {"error": "not found"}
    # 태그 어휘는 그 유저가 이미 쓰는 태그를 우선 재사용하도록 제안.
    existing = [
        r["name"]
        for r in await fetch(
            pool,
            "SELECT DISTINCT t.name FROM tags t "
            "JOIN note_tags nt ON nt.tag_id = t.id "
            "JOIN notes n ON n.id = nt.note_id AND n.user_id = %s",
            (user["id"],),
        )
    ]
    prompt = (
        "다음 노트에 어울리는 태그 3~5개를 쉼표로 구분해 한 줄로만 출력하라. "
        f"가능하면 기존 태그({', '.join(existing) or '없음'})를 재사용하라.\n\n"
        f"제목: {title}\n본문:\n{note['body']}"
    )
    raw = await llm.complete(prompt, tier="low")
    # low-tier가 JSON({"tags":[..]})/코드펜스로 감싸 반환해도 안전하게 추출.
    return {"tags": parse_tag_list(raw)}


@router.delete("/api/page/{title}")
async def delete_page(
    request: Request, title: str, user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
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
async def list_versions(
    request: Request, title: str, user: dict = Depends(require_user)
) -> dict:
    pool = request.app.state.pool
    note = await fetchrow(
        pool, "SELECT id FROM notes WHERE user_id = %s AND title = %s",
        (user["id"], title),
    )
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
async def get_version(
    request: Request, title: str, vid: int, user: dict = Depends(require_user)
) -> dict:
    # 버전의 소속 노트가 그 유저 것인지 확인(타 유저 버전 열람 차단).
    row = await fetchrow(
        request.app.state.pool,
        "SELECT v.body FROM note_versions v JOIN notes n ON n.id = v.note_id "
        "WHERE v.id = %s AND n.user_id = %s",
        (vid, user["id"]),
    )
    if row is None:
        return {"error": "not found"}
    return {"body": row["body"]}


@router.post("/api/page/{title}/restore")
async def restore_version(
    request: Request, title: str, body: dict, user: dict = Depends(require_user)
) -> dict:
    """버전 본문으로 되돌리기 = 저장의 일종(현재본 백업 + 재인덱싱)."""
    pool = request.app.state.pool
    version_id = body["version_id"]
    ver = await fetchrow(
        pool,
        "SELECT v.body FROM note_versions v JOIN notes n ON n.id = v.note_id "
        "WHERE v.id = %s AND n.user_id = %s",
        (version_id, user["id"]),
    )
    if ver is None:
        return {"error": "version not found"}
    result = await ingest_note(
        pool, user_id=user["id"], title=title, body=ver["body"],
        tags=await _current_tags(pool, user["id"], title), source="restore",
    )
    return {"ok": True, "action": "restored", "note_id": result["note_id"]}


async def _current_tags(pool, user_id: int, title: str) -> list[str]:
    rows = await fetch(
        pool,
        "SELECT t.name FROM tags t JOIN note_tags nt ON nt.tag_id = t.id "
        "JOIN notes n ON n.id = nt.note_id WHERE n.user_id = %s AND n.title = %s",
        (user_id, title),
    )
    return [r["name"] for r in rows]
