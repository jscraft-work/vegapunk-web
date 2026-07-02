"""OAuth 2.1 인가서버(AS) 저장소 — Redis 재사용 (Task v2-02).

vegapunk가 발급하는 *자기 토큰*(claude.ai 커넥터용)을 불투명(opaque) 토큰 + Redis
조회 방식으로 보관한다. 세션(session.py)과 동일한 `setex/get/delete` 인터페이스만
요구하므로 `redis.asyncio.Redis` / `MemoryStore` 폴백을 그대로 쓴다.

키/TTL:
  mcpclient:<id>   → {redirect_uris, client_name}          (DCR 등록 클라이언트, 30d)
  authreq:<id>     → {client_id, redirect_uri, code_challenge,
                      code_challenge_method, scope, state}  (authorize 재개용, 10m)
  code:<v>         → {client_id, user_id, redirect_uri,
                      code_challenge, code_challenge_method, scope}  (1회용, 60s)
  mcptok:<v>       → {user_id, client_id, scope}            (access token, 1h)
  mcprt:<v>        → {user_id, client_id, scope}            (refresh token, 30d, 회전)
"""

from __future__ import annotations

import json

from authlib.common.security import generate_token

# ── 프리픽스/TTL ──────────────────────────────────────────────
_CLIENT_PREFIX = "mcpclient:"
_AUTHREQ_PREFIX = "authreq:"
_CODE_PREFIX = "code:"
_TOKEN_PREFIX = "mcptok:"
_REFRESH_PREFIX = "mcprt:"

CLIENT_TTL = 60 * 60 * 24 * 30  # 30일
AUTHREQ_TTL = 600  # 10분
CODE_TTL = 60  # 60초(1회용, 짧게)
ACCESS_TTL = 60 * 60  # 1시간
REFRESH_TTL = 60 * 60 * 24 * 30  # 30일


async def _put(store, key: str, ttl: int, value: dict) -> None:
    await store.setex(key, ttl, json.dumps(value))


async def _get(store, key: str) -> dict | None:
    raw = await store.get(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def _pop(store, key: str) -> dict | None:
    """1회용 조회: 값을 꺼내고 즉시 폐기."""
    data = await _get(store, key)
    await store.delete(key)
    return data


# ── 등록 클라이언트 (DCR) ─────────────────────────────────────


async def register_client(store, redirect_uris: list[str], client_name: str = "") -> dict:
    client_id = generate_token(24)
    record = {"redirect_uris": redirect_uris, "client_name": client_name}
    await _put(store, _CLIENT_PREFIX + client_id, CLIENT_TTL, record)
    return {"client_id": client_id, **record}


async def get_client(store, client_id: str) -> dict | None:
    return await _get(store, _CLIENT_PREFIX + client_id)


# ── authorize 재개 요청 ───────────────────────────────────────


async def save_authreq(store, params: dict) -> str:
    authreq_id = generate_token(24)
    await _put(store, _AUTHREQ_PREFIX + authreq_id, AUTHREQ_TTL, params)
    return authreq_id


async def pop_authreq(store, authreq_id: str) -> dict | None:
    return await _pop(store, _AUTHREQ_PREFIX + authreq_id)


# ── authorization code (1회용) ────────────────────────────────


async def issue_code(store, data: dict) -> str:
    code = generate_token(36)
    await _put(store, _CODE_PREFIX + code, CODE_TTL, data)
    return code


async def pop_code(store, code: str) -> dict | None:
    return await _pop(store, _CODE_PREFIX + code)


# ── access / refresh 토큰 ─────────────────────────────────────


async def issue_access(store, user_id: int, client_id: str, scope: str) -> str:
    token = generate_token(42)
    await _put(
        store,
        _TOKEN_PREFIX + token,
        ACCESS_TTL,
        {"user_id": user_id, "client_id": client_id, "scope": scope},
    )
    return token


async def resolve_access(store, token: str) -> dict | None:
    """Bearer 토큰 → {user_id, client_id, scope}. 오프라인·빠른 조회."""
    return await _get(store, _TOKEN_PREFIX + token)


async def issue_refresh(store, user_id: int, client_id: str, scope: str) -> str:
    token = generate_token(42)
    await _put(
        store,
        _REFRESH_PREFIX + token,
        REFRESH_TTL,
        {"user_id": user_id, "client_id": client_id, "scope": scope},
    )
    return token


async def pop_refresh(store, token: str) -> dict | None:
    """refresh 토큰을 꺼내고 폐기(회전). 담긴 {user_id, client_id, scope} 반환."""
    return await _pop(store, _REFRESH_PREFIX + token)
