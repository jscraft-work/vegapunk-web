"""메모 API — 서버 저장(localStorage 대체).

두 종류: 사용자별 글로벌 메모(`user_memo`) + 대화별 메모(`conversations.memo`).
프론트는 디바운스로 PUT 한다. 대화별은 대화가 생성된 뒤에만 저장 가능
(새 대화의 임시 메모는 클라가 들고 있다가 첫 응답으로 conv_id를 받으면 flush).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.db import execute, fetchrow
from app.deps import require_user

router = APIRouter()


# ── 글로벌 메모(사용자별) ──────────────────────────────────────


@router.get("/api/memo")
async def get_global_memo(request: Request, user: dict = Depends(require_user)) -> dict:
    row = await fetchrow(
        request.app.state.pool,
        "SELECT body FROM user_memo WHERE user_id = %s",
        (user["id"],),
    )
    return {"body": row["body"] if row else ""}


@router.put("/api/memo")
async def put_global_memo(
    request: Request, body: dict, user: dict = Depends(require_user)
) -> dict:
    await execute(
        request.app.state.pool,
        "INSERT INTO user_memo (user_id, body) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET body = EXCLUDED.body, updated_at = now()",
        (user["id"], body.get("body", "")),
    )
    return {"ok": True}


# ── 대화별 메모 ────────────────────────────────────────────────


@router.get("/api/conversations/{conv_id}/memo")
async def get_conv_memo(
    request: Request, conv_id: int, user: dict = Depends(require_user)
) -> dict:
    row = await fetchrow(
        request.app.state.pool,
        "SELECT memo FROM conversations WHERE id = %s AND user_id = %s",
        (conv_id, user["id"]),
    )
    if row is None:
        return {"error": "not found"}
    return {"body": row["memo"] or ""}


@router.put("/api/conversations/{conv_id}/memo")
async def put_conv_memo(
    request: Request, conv_id: int, body: dict, user: dict = Depends(require_user)
) -> dict:
    row = await fetchrow(
        request.app.state.pool,
        "UPDATE conversations SET memo = %s WHERE id = %s AND user_id = %s RETURNING id",
        (body.get("body", ""), conv_id, user["id"]),
    )
    if row is None:
        return {"error": "not found"}
    return {"ok": True}
