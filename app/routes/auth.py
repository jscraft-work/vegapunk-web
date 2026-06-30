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
from app.db import execute, fetchrow
from app.session import (
    COOKIE_NAME,
    create_session,
    destroy_session,
    get_session,
    pop_link_state,
    pop_link_token,
    pop_oauth_state,
    stash_link_state,
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
async def login(request: Request, provider: str, link: str = ""):
    if provider not in _PROVIDERS:
        return JSONResponse({"detail": "unknown provider"}, status_code=404)
    settings = get_settings()
    client_id, _ = _creds(settings, provider)
    if not client_id:
        return JSONResponse({"detail": f"{provider} 미설정"}, status_code=404)
    state = secrets.token_urlsafe(24)
    await stash_oauth_state(request.app.state.session_store, state, provider)
    # 계정 연동 흐름: link 토큰을 state에 묶어 콜백에서 복원(OAuth는 link를 안 돌려줌).
    if link:
        await stash_link_state(request.app.state.session_store, state, link)
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
async def callback(
    request: Request, provider: str, code: str = "", state: str = "", link: str = ""
):
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
    # 연동 흐름이면 link 토큰을 state로 복원(쿼리에 직접 온 link도 허용).
    store = request.app.state.session_store
    link = link or (await pop_link_state(store, state)) or ""
    if link:
        return await _link_and_respond(request, profile, link)
    return await _login_and_redirect(request, profile)


# ── find-or-create (신원 기반 식별) ──────────────────────────


async def resolve_user(pool, profile: dict) -> int:
    """프로필(provider/sub/email/name) → user_id. 신원 기반으로 수렴시킨다."""
    # 1) 아는 신원?
    row = await fetchrow(
        pool,
        "SELECT user_id FROM identities WHERE provider=%s AND sub=%s",
        (profile["provider"], profile["sub"]),
    )
    if row:
        return row["user_id"]
    # 2) 일회용 브리지: identity 0개인 레거시 user와 email 일치 시 흡수(탈취 위험 없음).
    user_id = None
    if profile.get("email"):
        legacy = await fetchrow(
            pool,
            "SELECT u.id FROM users u WHERE u.email=%s AND u.status='active' "
            "AND NOT EXISTS (SELECT 1 FROM identities i WHERE i.user_id=u.id)",
            (profile["email"],),
        )
        if legacy:
            user_id = legacy["id"]
    # 3) 신규 가입(user 생성). 이메일 미동의 → placeholder 키.
    if user_id is None:
        u = await fetchrow(
            pool,
            "INSERT INTO users (email, name) VALUES (%s,%s) RETURNING id",
            (
                profile.get("email") or f'{profile["provider"]}:{profile["sub"]}',
                profile.get("name"),
            ),
        )
        user_id = u["id"]
    await execute(
        pool,
        "INSERT INTO identities (user_id, provider, sub, email) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (provider, sub) DO NOTHING",
        (user_id, profile["provider"], profile["sub"], profile.get("email")),
    )
    return user_id


async def merge_users(pool, src_id: int, dst_id: int) -> None:
    """src 계정을 dst로 흡수(삭제 금지, 툼스톤). 데이터 이전 후 src를 merged 처리."""
    async with pool.connection() as conn:
        async with conn.transaction():
            # user_memo는 PK가 user_id라 dst에 이미 행이 있으면 충돌 → dst 우선(src 행 삭제).
            await conn.execute(
                "DELETE FROM user_memo WHERE user_id=%s "
                "AND EXISTS (SELECT 1 FROM user_memo WHERE user_id=%s)",
                (src_id, dst_id),
            )
            for tbl in ("notes", "conversations", "identities", "user_memo"):
                await conn.execute(
                    f"UPDATE {tbl} SET user_id=%s WHERE user_id=%s", (dst_id, src_id)
                )
            await conn.execute(
                "UPDATE users SET status='merged', merged_into=%s, merged_at=now() "
                "WHERE id=%s",
                (dst_id, src_id),
            )


async def _link_and_respond(request: Request, profile: dict, link_token: str):
    """연동 콜백: link 토큰의 current_user에 새 신원을 붙인다(충돌 시 거부)."""
    store = request.app.state.session_store
    pool = request.app.state.pool
    current_user_id = await pop_link_token(store, link_token)
    if current_user_id is None:
        return JSONResponse(
            {"status": "error", "detail": "invalid/expired link token"}, status_code=400
        )
    existing = await fetchrow(
        pool,
        "SELECT user_id FROM identities WHERE provider=%s AND sub=%s",
        (profile["provider"], profile["sub"]),
    )
    if existing and existing["user_id"] != current_user_id:
        # 자동병합 금지 → 병합 플로우 안내.
        return JSONResponse(
            {"status": "conflict", "other_user": existing["user_id"]}
        )
    await execute(
        pool,
        "INSERT INTO identities (user_id, provider, sub, email) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (provider, sub) DO NOTHING",
        (current_user_id, profile["provider"], profile["sub"], profile.get("email")),
    )
    return JSONResponse({"status": "linked", "user_id": current_user_id})


# ── 서버세션 ──────────────────────────────────────────────────


async def _login_and_redirect(request: Request, profile: dict) -> RedirectResponse:
    settings = get_settings()
    user_id = await resolve_user(request.app.state.pool, profile)
    token = await create_session(
        request.app.state.session_store, user_id, settings.SESSION_TTL
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
