"""OAuth 2.1 인가서버(AS) — claude.ai 커스텀 커넥터용 (Task v2-02).

vegapunk를 OAuth 2.1 AS로 노출한다. 기존 kakao/github OAuth(app/routes/auth.py)는
*상류 인증*(사람 식별)으로 재사용하고, 여기서 그 결과(user_id)를 Claude가 쓰는
vegapunk 자기 토큰으로 번역한다.

보안 핵심(PKCE S256 검증·토큰 생성)은 authlib 헬퍼에 위임한다:
  - create_s256_code_challenge: code_verifier → challenge (S256) 검증
  - generate_token: 불투명 토큰/식별자 생성 (oauth_store)

엔드포인트:
  GET  /.well-known/oauth-protected-resource     보호 리소스 → AS 위치 안내
  GET  /.well-known/oauth-authorization-server   RFC 8414 메타데이터
  POST /oauth/register                           DCR(동적 클라이언트 등록)
  GET  /oauth/authorize                          상류 OAuth로 보냄 → code 발급
  POST /oauth/token                              code/refresh → access token
  GET  /mcp                                       (placeholder) Bearer → user_id
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import oauth_store
from app.config import get_settings
from app.session import (
    COOKIE_NAME,
    get_session,
    stash_authreq_state,
    stash_oauth_state,
)

router = APIRouter()

# 상류 로그인 세션이 없을 때 기본으로 태울 provider(“기본” 흐름).
_DEFAULT_PROVIDER = "github"
# 콜백 허용 URL(정확 일치). 향후 claude.com 추가 대비.
_ALLOWED_EXACT = {
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
}
# Claude Code loopback 허용 호스트(포트 가변).
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SCOPE_DEFAULT = "mcp"


def _base(request: Request) -> str:
    """디스커버리/issuer/콜백의 공개 URL 베이스.

    TLS 종단 리버스 프록시 뒤에선 request.base_url이 http로 잡혀(uvicorn --proxy-headers
    미사용) 디스커버리가 http를 광고 → Claude OAuth가 깨진다. 그래서 배포의 정식 공개
    오리진(OAUTH_REDIRECT_BASE, https)을 우선 쓰고, 미설정(테스트/dev) 시 요청값으로 폴백."""
    configured = get_settings().OAUTH_REDIRECT_BASE.rstrip("/")
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def _redirect_uri_allowed(uri: str) -> bool:
    if uri in _ALLOWED_EXACT:
        return True
    p = urlparse(uri)
    # Claude Code loopback: http + localhost/127.0.0.1(포트·경로 가변).
    return p.scheme == "http" and p.hostname in _LOOPBACK_HOSTS


def _oauth_error(error: str, status: int = 400, description: str = "") -> JSONResponse:
    body = {"error": error}
    if description:
        body["error_description"] = description
    return JSONResponse(body, status_code=status)


# ── 디스커버리 ────────────────────────────────────────────────


@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request) -> dict:
    base = _base(request)
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> dict:
    base = _base(request)
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [_SCOPE_DEFAULT],
    }


# ── DCR (동적 클라이언트 등록) ────────────────────────────────


@router.post("/oauth/register")
async def register(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — 본문 파싱 실패
        return _oauth_error("invalid_client_metadata", 400, "본문(JSON) 필요")
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return _oauth_error("invalid_redirect_uri", 400, "redirect_uris 필요")
    for uri in redirect_uris:
        if not isinstance(uri, str) or not _redirect_uri_allowed(uri):
            return _oauth_error("invalid_redirect_uri", 400, f"허용되지 않은 redirect_uri: {uri}")
    client = await oauth_store.register_client(
        request.app.state.session_store,
        redirect_uris,
        client_name=body.get("client_name", ""),
    )
    return JSONResponse(
        {
            "client_id": client["client_id"],
            "redirect_uris": client["redirect_uris"],
            "client_name": client["client_name"],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
        },
        status_code=201,
    )


# ── authorize (상류 인증 끼워넣기) ────────────────────────────


@router.get("/oauth/authorize")
async def authorize(request: Request):
    store = request.app.state.session_store
    q = request.query_params
    client_id = q.get("client_id", "")
    redirect_uri = q.get("redirect_uri", "")
    code_challenge = q.get("code_challenge", "")
    method = q.get("code_challenge_method", "")
    state = q.get("state", "")
    scope = q.get("scope", "") or _SCOPE_DEFAULT
    response_type = q.get("response_type", "code")

    # 1) 클라이언트/리다이렉트 검증 — 여기서 실패하면 redirect 하지 않는다(오픈 리다이렉트 방지).
    client = await oauth_store.get_client(store, client_id)
    if client is None:
        return _oauth_error("invalid_client", 400, "미등록 client_id")
    if redirect_uri not in client["redirect_uris"]:
        return _oauth_error("invalid_request", 400, "redirect_uri 불일치")
    if response_type != "code":
        return _redirect_err(redirect_uri, "unsupported_response_type", state)
    # 2) PKCE S256 필수.
    if not code_challenge or method != "S256":
        return _redirect_err(redirect_uri, "invalid_request", state, "PKCE S256 필수")

    pending = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": method,
        "scope": scope,
        "state": state,
    }

    # 3) 이미 상류 로그인 세션이 있으면 바로 code 발급.
    token = request.cookies.get(COOKIE_NAME)
    sess = await get_session(store, token) if token else None
    if sess is not None:
        return await _issue_and_redirect(store, sess["user_id"], pending)

    # 4) 세션 없음 → authreq를 저장하고 상류 OAuth(기본 provider)로 보낸다.
    #    콜백에서 이 authreq를 복원해 authorize를 재개한다(app/routes/auth.py).
    authreq_id = await oauth_store.save_authreq(store, pending)
    return RedirectResponse(f"/auth/login/{_DEFAULT_PROVIDER}?authreq={authreq_id}")


def _redirect_err(redirect_uri: str, error: str, state: str, desc: str = ""):
    params = {"error": error}
    if desc:
        params["error_description"] = desc
    if state:
        params["state"] = state
    return RedirectResponse(f"{redirect_uri}?{urlencode(params)}")


async def _issue_and_redirect(store, user_id: int, pending: dict) -> RedirectResponse:
    """authorization code를 발급하고 클라이언트 redirect_uri로 302."""
    code = await oauth_store.issue_code(
        store,
        {
            "client_id": pending["client_id"],
            "user_id": user_id,
            "redirect_uri": pending["redirect_uri"],
            "code_challenge": pending["code_challenge"],
            "code_challenge_method": pending["code_challenge_method"],
            "scope": pending["scope"],
        },
    )
    params = {"code": code}
    if pending.get("state"):
        params["state"] = pending["state"]
    return RedirectResponse(f"{pending['redirect_uri']}?{urlencode(params)}")


async def resume_authorize(request: Request, user_id: int, authreq_id: str):
    """상류 콜백(auth.py)에서 호출: 저장한 authreq를 복원해 code 발급.

    반환값이 None이면 authreq가 없거나 만료된 것(호출측이 폴백 처리)."""
    store = request.app.state.session_store
    pending = await oauth_store.pop_authreq(store, authreq_id)
    if pending is None:
        return None
    return await _issue_and_redirect(store, user_id, pending)


# ── token (code/refresh → access token) ───────────────────────


async def _read_form(request: Request) -> dict:
    """application/x-www-form-urlencoded 본문 파싱.

    starlette request.form()은 python-multipart를 요구하므로(멀티파트 전용 의존성),
    urlencoded는 직접 파싱해 불필요한 의존성을 피한다."""
    raw = (await request.body()).decode("utf-8", "ignore")
    return dict(parse_qsl(raw, keep_blank_values=True))


@router.post("/oauth/token")
async def token(request: Request) -> JSONResponse:
    form = await _read_form(request)
    grant_type = form.get("grant_type", "")
    store = request.app.state.session_store
    if grant_type == "authorization_code":
        return await _grant_authorization_code(store, form)
    if grant_type == "refresh_token":
        return await _grant_refresh_token(store, form)
    return _oauth_error("unsupported_grant_type", 400)


async def _grant_authorization_code(store, form) -> JSONResponse:
    code = form.get("code", "")
    verifier = form.get("code_verifier", "")
    client_id = form.get("client_id", "")
    redirect_uri = form.get("redirect_uri", "")
    if not code or not verifier:
        return _oauth_error("invalid_request", 400, "code·code_verifier 필요")
    data = await oauth_store.pop_code(store, code)  # 1회용: 즉시 폐기
    if data is None:
        return _oauth_error("invalid_grant", 400, "code 없음/만료")
    if client_id and data["client_id"] != client_id:
        return _oauth_error("invalid_grant", 400, "client_id 불일치")
    if redirect_uri and data["redirect_uri"] != redirect_uri:
        return _oauth_error("invalid_grant", 400, "redirect_uri 불일치")
    # PKCE S256 검증(authlib) — verifier로 challenge 재계산해 비교.
    if create_s256_code_challenge(verifier) != data["code_challenge"]:
        return _oauth_error("invalid_grant", 400, "PKCE 검증 실패")
    return await _token_response(store, data["user_id"], data["client_id"], data["scope"])


async def _grant_refresh_token(store, form) -> JSONResponse:
    refresh = form.get("refresh_token", "")
    if not refresh:
        return _oauth_error("invalid_request", 400, "refresh_token 필요")
    data = await oauth_store.pop_refresh(store, refresh)  # 회전: 옛 refresh 폐기
    if data is None:
        return _oauth_error("invalid_grant", 400, "refresh_token 없음/만료")
    client_id = form.get("client_id", "")
    if client_id and data["client_id"] != client_id:
        return _oauth_error("invalid_grant", 400, "client_id 불일치")
    return await _token_response(store, data["user_id"], data["client_id"], data["scope"])


async def _token_response(store, user_id: int, client_id: str, scope: str) -> JSONResponse:
    access = await oauth_store.issue_access(store, user_id, client_id, scope)
    refresh = await oauth_store.issue_refresh(store, user_id, client_id, scope)
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": oauth_store.ACCESS_TTL,
            "refresh_token": refresh,
            "scope": scope,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


# ── 보호 리소스 헬퍼 + placeholder /mcp ───────────────────────


def _www_authenticate(request: Request) -> str:
    meta = f"{_base(request)}/.well-known/oauth-protected-resource"
    return f'Bearer error="invalid_token", resource_metadata="{meta}"'


async def resolve_bearer_user(request: Request) -> dict | None:
    """Authorization: Bearer <token> → {user_id, client_id, scope}. 없으면 None.

    Task 03의 /mcp 도구가 재사용할 오프라인 인증 진입점."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    return await oauth_store.resolve_access(request.app.state.session_store, token)


# 실제 보호 리소스 /mcp는 app/mcp_server.py의 MCP 서버가 마운트한다(Task 03).
# 미인증 접근 시 401 + WWW-Authenticate는 McpAuthMiddleware가 반환한다.
