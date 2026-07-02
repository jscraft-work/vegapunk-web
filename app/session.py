"""세션 저장소 (Task 10).

세션 = 서버 생성 불투명 토큰(`secrets.token_urlsafe`) → 저장소에 `{user_id}`
(TTL). 브라우저엔 HttpOnly·SameSite=Lax 쿠키로 토큰만 전달.

저장소는 `setex(key, ttl, value)` / `get(key)` / `delete(key)` 비동기 인터페이스만
요구한다 → `redis.asyncio.Redis`를 그대로 쓰거나, Redis 미가용 시 `MemoryStore`로
폴백(단일 사용자 dev/테스트).
"""

from __future__ import annotations

import json
import secrets

_PREFIX = "sess:"
COOKIE_NAME = "session"


class MemoryStore:
    """Redis 미가용 시 폴백 / 테스트용 인메모리 저장소(TTL 무시)."""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._d[key] = value

    async def get(self, key: str) -> str | None:
        return self._d.get(key)

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)


async def create_session(store, user_id: int, ttl: int) -> str:
    token = secrets.token_urlsafe(32)
    await store.setex(_PREFIX + token, ttl, json.dumps({"user_id": user_id}))
    return token


async def get_session(store, token: str) -> dict | None:
    raw = await store.get(_PREFIX + token)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)


async def destroy_session(store, token: str) -> None:
    await store.delete(_PREFIX + token)


# ── OAuth state (로그인 핸드셰이크 위조 방지값, 서버 보관) ──────
_OAUTH_PREFIX = "oauthstate:"
OAUTH_STATE_TTL = 600  # 10분


async def stash_oauth_state(store, state: str, provider: str) -> None:
    await store.setex(_OAUTH_PREFIX + state, OAUTH_STATE_TTL, provider)


async def pop_oauth_state(store, state: str) -> str | None:
    """state를 꺼내고 제거(1회용). 발급한 provider를 반환, 없으면 None."""
    key = _OAUTH_PREFIX + state
    raw = await store.get(key)
    await store.delete(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


# ── 계정 연동(link) 토큰 ───────────────────────────────────────
# 이미 로그인된 user가 새 신원을 자기 계정에 붙일 때 쓰는 일회용 토큰.
# 토큰은 현재 user_id를 담고 짧게 만료된다(브라우저 OAuth 한 바퀴 도는 동안만).
_LINK_PREFIX = "linktoken:"
_LINKSTATE_PREFIX = "linkstate:"
LINK_TOKEN_TTL = 600  # 10분


async def stash_link_token(store, token: str, user_id: int) -> None:
    await store.setex(_LINK_PREFIX + token, LINK_TOKEN_TTL, json.dumps({"user_id": user_id}))


async def pop_link_token(store, token: str) -> int | None:
    """link 토큰을 꺼내고 제거(1회용). 담긴 user_id를 반환, 없으면 None."""
    key = _LINK_PREFIX + token
    raw = await store.get(key)
    await store.delete(key)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)["user_id"]


async def stash_link_state(store, state: str, token: str) -> None:
    """OAuth state ↔ link 토큰 매핑. 실제 OAuth는 link 파라미터를 되돌려주지
    않으므로, state로 link 토큰을 복원할 수 있게 함께 보관한다."""
    await store.setex(_LINKSTATE_PREFIX + state, OAUTH_STATE_TTL, token)


async def pop_link_state(store, state: str) -> str | None:
    key = _LINKSTATE_PREFIX + state
    raw = await store.get(key)
    await store.delete(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


# ── OAuth AS authorize 재개(authreq) 매핑 ─────────────────────
# vegapunk가 AS일 때, /oauth/authorize에서 상류 로그인 세션이 없으면 상류 OAuth로
# 보냈다가 콜백에서 authorize를 재개해야 한다. 상류 OAuth는 우리 authreq 파라미터를
# 되돌려주지 않으므로, 상류 state ↔ authreq_id를 함께 보관해 콜백에서 복원한다.
_AUTHREQ_STATE_PREFIX = "authreqstate:"


async def stash_authreq_state(store, state: str, authreq_id: str) -> None:
    await store.setex(_AUTHREQ_STATE_PREFIX + state, OAUTH_STATE_TTL, authreq_id)


async def pop_authreq_state(store, state: str) -> str | None:
    key = _AUTHREQ_STATE_PREFIX + state
    raw = await store.get(key)
    await store.delete(key)
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else raw
