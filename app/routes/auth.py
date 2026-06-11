"""인증 (카카오 + GitHub OAuth) — 전부 서버세션(Redis).

OAuth state는 Redis에 보관(SessionMiddleware/쿠키 세션 미사용).
콜백 → code 교환 → 프로필 → users upsert → 서버세션 생성 → 쿠키(불투명 토큰) → /.
외부 호출은 `_fetch_profile`에 격리(테스트는 이 함수를 모킹).
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.db import fetchrow
from app.session import (
    COOKIE_NAME,
    create_session,
    destroy_session,
    get_session,
    pop_oauth_state,
    stash_oauth_state,
)

router = APIRouter()

# 프로바이더별 엔드포인트/스코프.
_PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "scope": "read:user user:email",
    },
    "kakao": {
        "authorize": "https://kauth.kakao.com/oauth/authorize",
        "token": "https://kauth.kakao.com/oauth/token",
        "scope": "account_email profile_nickname",
    },
}


def _creds(settings, provider: str) -> tuple[str, str]:
    if provider == "github":
        return settings.GH_CLIENT_ID, settings.GH_CLIENT_SECRET
    return settings.KAKAO_REST_API_KEY, settings.KAKAO_CLIENT_SECRET


def _redirect_uri(settings, provider: str) -> str:
    return f"{settings.OAUTH_REDIRECT_BASE}/auth/callback/{provider}"


# ── authorize 리다이렉트 ───────────────────────────────────────


@router.get("/auth/login/{provider}")
async def login(request: Request, provider: str):
    if provider not in _PROVIDERS:
        return JSONResponse({"detail": "unknown provider"}, status_code=404)
    settings = get_settings()
    client_id, _ = _creds(settings, provider)
    if not client_id:
        return JSONResponse({"detail": f"{provider} 미설정"}, status_code=404)
    state = secrets.token_urlsafe(24)
    await stash_oauth_state(request.app.state.session_store, state, provider)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _redirect_uri(settings, provider),
        "scope": _PROVIDERS[provider]["scope"],
        "state": state,
    }
    return RedirectResponse(f"{_PROVIDERS[provider]['authorize']}?{urlencode(params)}")


# ── 콜백 (code 교환 + 프로필) ─────────────────────────────────


async def _fetch_profile(settings, provider: str, code: str) -> dict:
    """code → access_token → 정규화 프로필 {provider, sub, email, name}.

    실제 외부 호출. 테스트는 이 함수를 모킹한다.
    """
    client_id, client_secret = _creds(settings, provider)
    async with httpx.AsyncClient(timeout=15) as http:
        tok = await http.post(
            _PROVIDERS[provider]["token"],
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": _redirect_uri(settings, provider),
            },
            headers={"Accept": "application/json"},
        )
        access = tok.json().get("access_token")
        if not access:
            raise RuntimeError(f"token exchange 실패: {tok.text}")
        if provider == "github":
            gh = {"Authorization": f"Bearer {access}", "Accept": "application/vnd.github+json"}
            prof = (await http.get("https://api.github.com/user", headers=gh)).json()
            email = prof.get("email")
            if not email:  # private email → /user/emails 의 primary·verified
                emails = (await http.get("https://api.github.com/user/emails", headers=gh)).json()
                if isinstance(emails, list):
                    email = next(
                        (e["email"] for e in emails if e.get("primary") and e.get("verified")),
                        None,
                    )
            return {
                "provider": "github",
                "sub": str(prof["id"]),
                "email": email,
                "name": prof.get("name") or prof.get("login"),
            }
        # kakao
        ka = {"Authorization": f"Bearer {access}"}
        info = (await http.get("https://kapi.kakao.com/v2/user/me", headers=ka)).json()
        account = info.get("kakao_account", {})
        return {
            "provider": "kakao",
            "sub": str(info["id"]),
            "email": account.get("email"),
            "name": (account.get("profile") or {}).get("nickname"),
        }


@router.get("/auth/callback/{provider}")
async def callback(request: Request, provider: str, code: str = "", state: str = ""):
    if provider not in _PROVIDERS:
        return JSONResponse({"detail": "unknown provider"}, status_code=404)
    if not code or not state:
        return JSONResponse({"detail": "missing code/state"}, status_code=400)
    issued = await pop_oauth_state(request.app.state.session_store, state)
    if issued != provider:  # 우리가 발급하지 않은/만료된 state
        return JSONResponse({"detail": "invalid/expired state"}, status_code=400)
    settings = get_settings()
    try:
        profile = await _fetch_profile(settings, provider, code)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"detail": f"oauth 실패: {e}"}, status_code=502)
    return await _login_and_redirect(request, profile)


# ── user upsert + 서버세션 ────────────────────────────────────


async def _upsert_user(pool, profile: dict) -> dict:
    # 이메일 미동의(카카오 등) → placeholder 키로 유니크 충족.
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
        COOKIE_NAME, token, httponly=True, samesite="lax", max_age=settings.SESSION_TTL
    )
    return resp


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
