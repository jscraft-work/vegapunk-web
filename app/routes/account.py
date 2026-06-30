"""계정 연동 (link) 시작 엔드포인트.

이미 로그인된 user가 새 신원(다른 provider)을 자기 계정에 붙이려면, 그 신원으로도
로그인 가능함을 증명해야 안전하다 → 반드시 브라우저 OAuth를 한 바퀴 탄다.

흐름: POST /api/account/link/start (require_user)
  → 일회용 link 토큰 발급(현재 user_id 담음, 짧은 만료, 세션 저장소)
  → { url: "/auth/login/{provider}?link=<token>" }
콜백(/auth/callback/{provider}?...&link=<token>)에서 현재 user에 identity를 붙인다.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.deps import require_user
from app.session import stash_link_token

router = APIRouter()

_PROVIDERS = {"github", "kakao", "google"}


@router.post("/api/account/link/start")
async def link_start(request: Request, user: dict = Depends(require_user)) -> JSONResponse:
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — 본문 없을 수 있음
        body = {}
    provider = (body or {}).get("provider")
    if provider not in _PROVIDERS:
        return JSONResponse({"detail": "unknown provider"}, status_code=400)
    token = secrets.token_urlsafe(24)
    await stash_link_token(request.app.state.session_store, token, user["id"])
    return JSONResponse({"url": f"/auth/login/{provider}?link={token}"})
