"""대화 기억 / 증분 요약 테스트 (FakeLLM 사용)."""

import pytest
import pytest_asyncio

from app import memory
from app.db import fetch, fetchrow
from app.llm import FakeLLMClient


async def _new_conv(pool) -> int:
    row = await fetchrow(
        pool, "INSERT INTO conversations (user_id) VALUES (1) RETURNING id", None
    )
    return row["id"]


async def _add_turns(pool, conv_id: int, n: int, *, start: int = 0) -> None:
    """user/assistant 쌍 n턴 추가. 내용에 턴 번호를 박아 추적 가능."""
    async with pool.connection() as conn:
        for i in range(start, start + n):
            await conn.execute(
                "INSERT INTO messages (conv_id, role, content) VALUES (%s,'user',%s)",
                (conv_id, f"질문{i}"),
            )
            await conn.execute(
                "INSERT INTO messages (conv_id, role, content) VALUES (%s,'assistant',%s)",
                (conv_id, f"답변{i}"),
            )
        await conn.commit()


def _counting_llm(return_value: str = "갱신된요약"):
    """호출 횟수와 마지막 prompt를 기록하는 FakeLLM."""
    state = {"calls": 0, "prompt": None}

    def fn(prompt, tier):
        state["calls"] += 1
        state["prompt"] = prompt
        return return_value

    return FakeLLMClient(complete_fn=fn), state


async def test_no_trigger_below_threshold(clean_db):
    pool = clean_db
    conv = await _new_conv(pool)
    await _add_turns(pool, conv, memory.SUMMARY_TRIGGER_TURNS)  # 정확히 12턴
    llm, state = _counting_llm()

    await memory.maybe_update_summary(pool, llm, conv)

    assert state["calls"] == 0
    row = await fetchrow(
        pool, "SELECT summary, summary_upto_msg_id FROM conversations WHERE id=%s", (conv,)
    )
    assert row["summary"] is None
    assert row["summary_upto_msg_id"] is None


async def test_trigger_and_fold(clean_db):
    pool = clean_db
    conv = await _new_conv(pool)
    await _add_turns(pool, conv, 13)  # 12 초과 → 트리거
    llm, state = _counting_llm()

    await memory.maybe_update_summary(pool, llm, conv)

    assert state["calls"] == 1
    row = await fetchrow(
        pool, "SELECT summary, summary_upto_msg_id FROM conversations WHERE id=%s", (conv,)
    )
    assert row["summary"] == "갱신된요약"
    assert row["summary_upto_msg_id"] is not None

    # 요약 후 원문 창은 FOLD_TO_TURNS(6)으로 줄어야 한다.
    msgs = await memory._unsummarized_msgs(pool, conv, row["summary_upto_msg_id"])
    assert len(memory._group_turns(msgs)) == memory.FOLD_TO_TURNS


async def test_incremental_input(clean_db):
    pool = clean_db
    conv = await _new_conv(pool)
    # 기존 요약이 이미 있는 상태에서 증분 요약이 되는지.
    await _add_turns(pool, conv, 13)
    llm, state = _counting_llm()
    await memory.maybe_update_summary(pool, llm, conv)

    prompt = state["prompt"]
    # 접힌(오래된) 턴은 입력에 포함.
    assert "질문0" in prompt and "답변0" in prompt
    # 최근 창(FOLD_TO_TURNS=6)으로 남는 마지막 턴(턴12)은 입력에 없어야 한다(전체 X).
    assert "질문12" not in prompt
    assert "답변12" not in prompt


async def test_invariant(clean_db):
    pool = clean_db
    conv = await _new_conv(pool)
    await _add_turns(pool, conv, 13)
    llm, _ = _counting_llm()
    await memory.maybe_update_summary(pool, llm, conv)

    row = await fetchrow(
        pool, "SELECT summary_upto_msg_id FROM conversations WHERE id=%s", (conv,)
    )
    upto = row["summary_upto_msg_id"]
    all_ids = {
        r["id"]
        for r in await fetch(pool, "SELECT id FROM messages WHERE conv_id=%s", (conv,))
    }
    folded = {i for i in all_ids if i <= upto}  # 요약 범위
    window = {i for i in all_ids if i > upto}  # 최근 창
    # 모든 메시지가 둘 중 하나에 정확히 한 번 — 구멍/중복 없음.
    assert folded | window == all_ids
    assert folded & window == set()


async def test_rewrite_input(clean_db):
    pool = clean_db
    conv = await _new_conv(pool)

    # 첫 질문: 이전 대화/요약 없음 → None.
    first = await memory.build_rewrite_input(pool, conv, "첫 질문")
    assert first is None

    # 이후: 요약+직전 턴+질문 포함.
    await _add_turns(pool, conv, 2)
    out = await memory.build_rewrite_input(pool, conv, "다음 질문")
    assert out is not None
    assert "다음 질문" in out
    assert "질문1" in out  # 직전 턴 원문 포함
