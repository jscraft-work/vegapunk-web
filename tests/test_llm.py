"""LLM 클라이언트 테스트."""

import json

import httpx
import pytest

from app.llm import FakeLLMClient, LLMError, OpenclawClient


async def test_complete():
    seen = {}

    def fn(prompt, tier):
        seen["prompt"], seen["tier"] = prompt, tier
        return f"답변:{prompt}"

    client = FakeLLMClient(complete_fn=fn)
    out = await client.complete("질문", tier="low")
    assert out == "답변:질문"
    assert seen == {"prompt": "질문", "tier": "low"}
    # Fake도 호출 인자를 기록한다.
    assert client.last_tier == "low"


async def test_stream():
    client = FakeLLMClient(stream_chunks=["안", "녕", "하세요"])
    got = [c async for c in client.stream("프롬프트")]
    assert got == ["안", "녕", "하세요"]


async def test_no_session_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url.path
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"text": "ok"})

    client = OpenclawClient(
        "http://openclaw.test",
        transport=httpx.MockTransport(handler),
    )
    await client.complete("안녕", tier="default")

    assert captured["url"] == "/ask"
    body = captured["body"]
    assert body["prompt"] == "안녕"
    assert body["level"] == "high"
    # 무상태 원칙: 대화상태 키가 절대 없어야 한다.
    for forbidden in ("session_id", "session", "conversation_id", "history"):
        assert forbidden not in body


async def test_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = OpenclawClient(
        "http://openclaw.test",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(LLMError):
        await client.complete("질문")
