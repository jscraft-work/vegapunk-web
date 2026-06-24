"""라우트 보호 의존성 (Task 10).

`Depends(require_user)`를 `/api/*`에 적용한다. `/health`·`/login`·`/auth/*`·
정적파일은 공개. 멀티유저: 각 핸들러가 `user = Depends(require_user)`로 받은
`user["id"]`로 노트/대화/메모를 소유권 필터한다(0002 마이그레이션에서 user_id 도입).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from app.db import fetchrow
from app.session import COOKIE_NAME, get_session


async def require_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="인증 필요")
    store = request.app.state.session_store
    sess = await get_session(store, token)
    if sess is None:
        raise HTTPException(status_code=401, detail="세션 만료")
    user = await fetchrow(
        request.app.state.pool,
        "SELECT id, email, name FROM users WHERE id = %s",
        (sess["user_id"],),
    )
    if user is None:
        raise HTTPException(status_code=401, detail="사용자 없음")
    return user
