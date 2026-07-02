"""MCP 서버 마운트 (Task v2-03).

공식 `mcp` SDK(FastMCP)로 원격 MCP 서버를 만들어 기존 FastAPI 프로세스에 `/mcp`로
마운트한다(별도 프로세스 금지 — DB 풀·fastembed 싱글톤·서비스 함수 공유).

인증: Task 02가 발급한 Bearer 토큰 → user_id(오프라인 Redis 조회). 순수 ASGI
미들웨어(`McpAuthMiddleware`)가 /mcp 요청을 가로채 토큰을 검증하고, 유효하면
요청 컨텍스트(ContextVar)에 user_id/pool/store를 심는다 → 도구는 이를 읽어 스코프한다.
무효/누락 토큰은 401 + WWW-Authenticate(보호 리소스 표준)로 즉시 거부한다.

도구는 전부 app/mcp_tools.py의 얇은 래퍼(서버측 LLM 0회).
"""

from __future__ import annotations

import contextvars
import json

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from app import mcp_tools, oauth_store

# 요청 컨텍스트: 미들웨어가 심고 도구가 읽는다(요청 태스크 내 전파).
_ctx_user_id: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "mcp_user_id", default=None
)
_ctx_pool: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "mcp_pool", default=None
)
_ctx_store: contextvars.ContextVar[object | None] = contextvars.ContextVar(
    "mcp_store", default=None
)


class AuthError(PermissionError):
    """인증 컨텍스트 없이 도구가 호출됨."""


def _require_ctx() -> tuple[object, int]:
    pool = _ctx_pool.get()
    user_id = _ctx_user_id.get()
    if pool is None or user_id is None:
        raise AuthError("인증 필요: 유효한 Bearer 토큰이 없습니다")
    return pool, user_id


# ── 도구 래퍼(컨텍스트 → mcp_tools 위임) ─────────────────────
# description에 가드레일 명시(빈 결과 환각 금지 / 민감 접근수단 저장 금지).


async def tool_search_notes(query: str, topic: str | None = None, limit: int = 15) -> list[dict]:
    """내 노트를 하이브리드 검색(글자+의미)해 원문 스니펫을 반환한다.

    결과가 비면 []가 오며, 그럴 땐 추측하지 말고 '해당 노트 없음'이라고 답하라.
    전체 본문이 필요하면 note_id의 제목으로 get_note를 호출하라."""
    pool, user_id = _require_ctx()
    q = f"{topic} {query}".strip() if topic else query
    return await mcp_tools.search_notes(pool, user_id, q, limit)


async def tool_find_merge_target(title: str, body: str) -> dict | None:
    """이 노트 후보가 병합될 기존 노트를 찾는다(없으면 null). 저장 전 중복 확인용."""
    pool, user_id = _require_ctx()
    return await mcp_tools.find_merge_target(pool, user_id, title, body)


async def tool_ingest_note(
    title: str, body: str, tags: list[str] | None = None, merge_into: int | None = None
) -> dict:
    """노트를 저장하고 즉시 인덱싱한다. merge_into(note_id) 주면 그 노트 본문을 교체(병합).

    비밀번호·계좌번호·API 키 등 접근수단은 저장하지 말라."""
    pool, user_id = _require_ctx()
    return await mcp_tools.ingest_note(pool, user_id, title, body, tags, merge_into)


async def tool_get_note(title: str) -> dict | None:
    """제목으로 노트 전체(본문·태그)를 연다. 없으면 null."""
    pool, user_id = _require_ctx()
    return await mcp_tools.get_note(pool, user_id, title)


async def tool_list_notes(tag: str | None = None) -> list[dict]:
    """내 노트 목록(최근 수정순). tag를 주면 그 태그의 노트만."""
    pool, user_id = _require_ctx()
    return await mcp_tools.list_notes(pool, user_id, tag)


async def tool_update_note(
    title: str, body: str | None = None, tags: list[str] | None = None
) -> dict:
    """기존 노트의 본문/태그를 수정한다(본문 변경 시 재인덱싱)."""
    pool, user_id = _require_ctx()
    return await mcp_tools.update_note(pool, user_id, title, body, tags)


async def tool_delete_note(title: str) -> dict:
    """제목으로 노트를 삭제한다(인바운드 링크는 보존·NULL 처리)."""
    pool, user_id = _require_ctx()
    return await mcp_tools.delete_note(pool, user_id, title)


async def tool_link_account(provider: str = "github") -> dict:
    """다른 로그인(github/kakao/google)을 현재 계정에 연동하는 일회용 링크 URL을 반환한다."""
    _pool, user_id = _require_ctx()
    store = _ctx_store.get()
    return await mcp_tools.link_account(store, user_id, provider)


def build_mcp() -> FastMCP:
    """도구를 등록한 FastMCP 인스턴스. streamable_http_path='/'로 마운트 시 정확히 /mcp."""
    # DNS 리바인딩 보호는 끈다: /mcp는 리버스 프록시 뒤 원격 엔드포인트 + Bearer 인증이라
    # (로컬 서버 대상) 리바인딩 위험이 실질적으로 없음. SDK 기본 Host 검사만 비활성화.
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=["*"],
        allowed_origins=["*"],
    )
    mcp = FastMCP(
        "vegapunk",
        instructions="vegapunk 노트 저장소(제2의 뇌) 도구. 검색·저장·병합·CRUD.",
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=security,
    )
    mcp.tool(name="search_notes")(tool_search_notes)
    mcp.tool(name="find_merge_target")(tool_find_merge_target)
    mcp.tool(name="ingest_note")(tool_ingest_note)
    mcp.tool(name="get_note")(tool_get_note)
    mcp.tool(name="list_notes")(tool_list_notes)
    mcp.tool(name="update_note")(tool_update_note)
    mcp.tool(name="delete_note")(tool_delete_note)
    mcp.tool(name="link_account")(tool_link_account)
    return mcp


# ── 순수 ASGI 인증 미들웨어 ───────────────────────────────────


def _www_authenticate(base: str) -> str:
    meta = f"{base}/.well-known/oauth-protected-resource"
    return f'Bearer error="invalid_token", resource_metadata="{meta}"'


async def _send_401(send, base: str) -> None:
    body = json.dumps({"error": "invalid_token"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", _www_authenticate(base).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class McpAuthMiddleware:
    """/mcp 경로만 가로채 Bearer→user_id 검증 후 ContextVar에 컨텍스트를 심는다.

    BaseHTTPMiddleware가 아니라 순수 ASGI 미들웨어를 쓰는 이유: MCP 스트리밍(SSE)과
    ContextVar 전파를 깨지 않기 위함. 다른 경로는 그대로 통과."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/mcp"):
            return await self.app(scope, receive, send)

        parent = scope.get("app")
        state = getattr(parent, "state", None)
        store = getattr(state, "session_store", None)
        pool = getattr(state, "pool", None)
        base = _scheme_host(scope)

        principal = None
        if store is not None:
            for name, value in scope.get("headers", []):
                if name == b"authorization":
                    auth = value.decode()
                    if auth.lower().startswith("bearer "):
                        token = auth[7:].strip()
                        if token:
                            principal = await oauth_store.resolve_access(store, token)
                    break
        if principal is None:
            return await _send_401(send, base)

        # Mount('/mcp')는 내부 라우트가 '/'라 bare '/mcp'를 '/mcp/'로 307 리다이렉트한다.
        # 커넥터가 /mcp를 그대로 쓰도록 경로를 정규화해 리다이렉트를 없앤다.
        if scope["path"] == "/mcp":
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}

        t_uid = _ctx_user_id.set(principal["user_id"])
        t_pool = _ctx_pool.set(pool)
        t_store = _ctx_store.set(store)
        try:
            await self.app(scope, receive, send)
        finally:
            _ctx_user_id.reset(t_uid)
            _ctx_pool.reset(t_pool)
            _ctx_store.reset(t_store)


def _scheme_host(scope) -> str:
    """401 WWW-Authenticate의 resource_metadata용 공개 베이스.

    프록시 뒤 scope는 http로 잡히므로 정식 공개 오리진(OAUTH_REDIRECT_BASE)을 우선 쓴다
    (오auth 디스커버리 _base와 일관). 미설정 시 요청 Host로 폴백."""
    from app.config import get_settings

    configured = get_settings().OAUTH_REDIRECT_BASE.rstrip("/")
    if configured:
        return configured
    headers = dict(scope.get("headers", []))
    host = headers.get(b"host", b"localhost").decode()
    scheme = scope.get("scheme", "http")
    return f"{scheme}://{host}"
