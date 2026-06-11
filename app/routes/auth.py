"""인증 (카카오 + GitHub OAuth) (Task 10).

OAuth 외부 호출은 `_complete_oauth`에 격리(테스트는 이 함수를 모킹).
콜백 → 프로필 → users upsert → 세션 생성 → 쿠키 발급 → / 리다이렉트.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.db import fetchrow
from app.session import (
    COOKIE_NAME,
    create_session,
    destroy_session,
    get_session,
)

router = APIRouter()


async def _complete_oauth(request: Request, provider: str) -> dict:
    """OAuth 콜백 완료 → 정규화 프로필 {provider, sub, email, name}.

    실제 호출은 authlib 레지스트리(app.state.oauth)로. 테스트는 이 함수를 모킹.
    """
    oauth = request.app.state.oauth
    client = oauth.create_client(provider)
    token = await client.authorize_access_token(request)
    if provider == "github":
        resp = await client.get("user", token=token)
        info = resp.json()
        return {
            "provider": "github",
            "sub": str(info["id"]),
            "email": info.get("email"),
            "name": info.get("name") or info.get("login"),
        }
    # kakao
    resp = await client.get("https://kapi.kakao.com/v2/user/me", token=token)
    info = resp.json()
    account = info.get("kakao_account", {})
    return {
        "provider": "kakao",
        "sub": str(info["id"]),
        "email": account.get("email"),
        "name": (account.get("profile") or {}).get("nickname"),
    }


async def _upsert_user(pool, profile: dict) -> dict:
    # 카카오 등 이메일 미동의 → placeholder 키로 유니크 충족.
    email = profile.get("email") or f"{profile['provider']}:{profile['sub']}"
    name = profile.get("name") or email
    return await fetchrow(
        pool,
        "INSERT INTO users (email, name) VALUES (%s, %s) "
        "ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id, email, name",
        (email, name),
    )


async def _login_and_redirect(request: Request, profile: dict) -> RedirectResponse:
    settings = get_settings()
    user = await _upsert_user(request.app.state.pool, profile)
    token = await create_session(
        request.app.state.session_store, user["id"], settings.SESSION_TTL
    )
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=settings.SESSION_TTL,
    )
    return resp


# ── authorize 리다이렉트 ───────────────────────────────────────


@router.get("/auth/login/{provider}")
async def login(request: Request, provider: str):
    settings = get_settings()
    redirect_uri = f"{settings.OAUTH_REDIRECT_BASE}/auth/callback/{provider}"
    client = request.app.state.oauth.create_client(provider)
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback/{provider}")
async def callback(request: Request, provider: str):
    profile = await _complete_oauth(request, provider)
    return await _login_and_redirect(request, profile)


# ── 세션 조회/로그아웃 ─────────────────────────────────────────


@router.get("/auth/me")
async def me(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return {"user": None}
    sess = await get_session(request.app.state.session_store, token)
    if sess is None:
        return {"user": None}
    user = await fetchrow(
        request.app.state.pool,
        "SELECT id, email, name FROM users WHERE id = %s",
        (sess["user_id"],),
    )
    return {"user": dict(user) if user else None}


@router.get("/auth/logout")
async def logout(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        await destroy_session(request.app.state.session_store, token)
    resp = RedirectResponse(url="/login", status_code=302)
    resp.delete_cookie(COOKIE_NAME)
    return resp
