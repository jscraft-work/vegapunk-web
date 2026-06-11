"""openclaw LLM 클라이언트 (기획서 6장).

openclaw를 **무상태**(prompt 문자열 하나 → 답 하나) 입구로만 쓴다.
session_id/대화상태는 절대 전송하지 않는다 — 대화 맥락은 호출자(06/07)가
프롬프트 문자열에 직접 조립해 넣는다.

- `tier="low"`  : 빠른 모델(다시쓰기/제목/태그제안)
- `tier="default"`: 기본 모델(답변/요약/distill 병합)

외부 의존이므로 추상 `LLMClient`에만 의존하고, 실제 호출은 `OpenclawClient`
한 곳에 격리한다. 테스트는 `FakeLLMClient`를 주입한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable

import httpx

from app.config import get_settings


class LLMError(Exception):
    """openclaw 호출 실패. 07의 SSE `error` 이벤트로 전달된다."""


class LLMClient(ABC):
    """LLM 추상 인터페이스. 나머지 코드는 이 추상에만 의존한다."""

    @abstractmethod
    async def complete(self, prompt: str, *, tier: str = "default") -> str:
        """비스트리밍 단건 응답(다시쓰기·요약·distill·태그제안)."""

    @abstractmethod
    def stream(
        self, prompt: str, *, tier: str = "default"
    ) -> AsyncIterator[str]:
        """델타 문자열 스트리밍(채팅 답변)."""


# ── openclaw 어댑터 ────────────────────────────────────────────


class OpenclawClient(LLMClient):
    """호스트 openclaw 래퍼 어댑터. 호출 규약을 여기에만 가둔다."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        model_low: str = "normal",
        model_default: str = "high",
        timeout: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._models = {"low": model_low, "default": model_default}
        self._timeout = timeout
        # 테스트에서 httpx MockTransport를 주입해 요청 페이로드를 검증한다.
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)

    def _model_for(self, tier: str) -> str:
        # 알 수 없는 tier는 default로 안전하게 강등.
        return self._models.get(tier, self._models["default"])

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _payload(self, prompt: str, tier: str) -> dict:
        # 실제 openclaw 계약: POST /ask {prompt, level, timeout_seconds}.
        # 무상태 원칙: session_id 등 대화상태 키는 절대 넣지 않는다.
        return {
            "prompt": prompt,
            "level": self._model_for(tier),
            "timeout_seconds": int(self._timeout),
        }

    async def _ask(self, prompt: str, tier: str) -> str:
        payload = self._payload(prompt, tier)
        # 외부 호출은 짧게 1회 재시도.
        last_exc: Exception | None = None
        for _ in range(2):
            try:
                async with self._http() as client:
                    resp = await client.post(
                        f"{self._base_url}/ask",
                        json=payload,
                        headers=self._headers(),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                text = data.get("text", "")
                if not text:
                    raise LLMError(f"openclaw 응답에 text 없음: {data}")
                return text
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
        raise LLMError(f"openclaw /ask 실패: {last_exc}") from last_exc

    async def complete(self, prompt: str, *, tier: str = "default") -> str:
        return await self._ask(prompt, tier)

    async def stream(
        self, prompt: str, *, tier: str = "default"
    ) -> AsyncIterator[str]:
        # openclaw는 비스트리밍(단일 응답) → 전체 답을 한 덩이로 yield.
        # FE는 answer 델타가 1개여도 정상 처리(기획서 6장: 래퍼 비스트리밍).
        text = await self._ask(prompt, tier)
        yield text


# ── 테스트용 Fake ──────────────────────────────────────────────


class FakeLLMClient(LLMClient):
    """주입한 함수/청크를 그대로 반환. 의존성 주입으로 앱에 바인딩.

    `complete_fn(prompt, tier) -> str` 로 호출 인자를 검증할 수 있다.
    """

    def __init__(
        self,
        complete_fn: Callable[[str, str], str | Awaitable[str]] | None = None,
        stream_chunks: Iterable[str] | None = None,
    ) -> None:
        self._complete_fn = complete_fn
        self._stream_chunks = list(stream_chunks or [])
        # 마지막 호출 인자 기록(테스트 검증용).
        self.last_prompt: str | None = None
        self.last_tier: str | None = None

    async def complete(self, prompt: str, *, tier: str = "default") -> str:
        self.last_prompt, self.last_tier = prompt, tier
        if self._complete_fn is None:
            return ""
        result = self._complete_fn(prompt, tier)
        if isinstance(result, Awaitable):
            return await result
        return result

    async def stream(
        self, prompt: str, *, tier: str = "default"
    ) -> AsyncIterator[str]:
        self.last_prompt, self.last_tier = prompt, tier
        for chunk in self._stream_chunks:
            yield chunk


# ── 의존성 와이어링 ────────────────────────────────────────────

_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """FastAPI `Depends(get_llm)` 진입점. 기본은 OpenclawClient 싱글톤.

    테스트는 `app.dependency_overrides[get_llm]`로 Fake를 주입한다.
    """
    global _client
    if _client is None:
        s = get_settings()
        _client = OpenclawClient(
            s.OPENCLAW_BASE_URL,
            api_key=s.OPENCLAW_API_KEY,
            model_low=s.OPENCLAW_MODEL_LOW,
            model_default=s.OPENCLAW_MODEL_DEFAULT,
        )
    return _client
