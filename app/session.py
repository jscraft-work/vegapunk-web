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
