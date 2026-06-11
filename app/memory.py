"""대화 기억 / 증분 압축 (기획서 7장).

openclaw는 무상태이므로 매 턴 이 모듈이 컨텍스트를 조립한다. 핵심 원칙:
- **증분 요약**: 전체 재요약 금지. 밀려나는 오래된 턴만 기존 요약에 접는다.
- **배칭**: 매 턴 요약하지 않고 임계(`SUMMARY_TRIGGER_TURNS`) 초과 시에만.
- **불변식**: "버리기 전에 먼저 요약에 접는다" — summary 갱신과 `summary_upto_msg_id`
  전진을 같은 트랜잭션으로 처리해, 원문 창에서 빠지기 전에 반드시 요약에 포함.

"턴" = user+assistant 1쌍. `summary_upto_msg_id` 이후의 원문 턴만 "최근 창".
"""

from __future__ import annotations

from app.db import fetch, fetchrow
from app.llm import LLMClient

# ── 파라미터 (기획서 10장) ─────────────────────────────────────
RECENT_TURNS = 6  # 답변 컨텍스트에 원문으로 싣는 최근 턴 수
SUMMARY_TRIGGER_TURNS = 12  # 미요약 원문 턴이 이보다 많아지면 요약 트리거
FOLD_TO_TURNS = 6  # 요약 후 원문으로 남길 최근 턴 수

_ROLE_LABEL = {"user": "사용자", "assistant": "비서"}


def _group_turns(msgs: list[dict]) -> list[list[dict]]:
    """시간순 메시지를 턴(user로 시작하는 묶음)으로 그룹화."""
    turns: list[list[dict]] = []
    cur: list[dict] = []
    for m in msgs:
        if m["role"] == "user":
            if cur:
                turns.append(cur)
            cur = [m]
        else:
            cur.append(m)
    if cur:
        turns.append(cur)
    return turns


def _format_turns(turns: list[list[dict]]) -> str:
    """턴들을 `사용자:`/`비서:` 표기 텍스트로."""
    lines: list[str] = []
    for turn in turns:
        for m in turn:
            label = _ROLE_LABEL.get(m["role"], m["role"])
            lines.append(f"{label}: {m['content']}")
    return "\n".join(lines)


async def _load_conv(pool, conv_id: int) -> dict:
    row = await fetchrow(
        pool,
        "SELECT id, summary, summary_upto_msg_id FROM conversations WHERE id = %s",
        (conv_id,),
    )
    if row is None:
        raise ValueError(f"conversation {conv_id} not found")
    return row


async def _unsummarized_msgs(pool, conv_id: int, upto: int | None) -> list[dict]:
    """`summary_upto_msg_id` 이후(원문 창에 해당)의 메시지만 시간순 로딩."""
    if upto is None:
        return await fetch(
            pool,
            "SELECT id, role, content FROM messages "
            "WHERE conv_id = %s ORDER BY id",
            (conv_id,),
        )
    return await fetch(
        pool,
        "SELECT id, role, content FROM messages "
        "WHERE conv_id = %s AND id > %s ORDER BY id",
        (conv_id, upto),
    )


async def maybe_update_summary(pool, llm: LLMClient, conv_id: int) -> None:
    """미요약 턴이 임계 초과면 1회 증분 요약(아니면 no-op).

    답변 경로를 막지 않도록 호출자(07)가 응답 후 BackgroundTask로 실행한다.
    """
    conv = await _load_conv(pool, conv_id)
    upto = conv["summary_upto_msg_id"]
    msgs = await _unsummarized_msgs(pool, conv_id, upto)
    turns = _group_turns(msgs)

    # 트리거 미달이면 아무것도 안 함(중복 실행 가드: 이미 접힌 범위는 upto로 배제).
    if len(turns) <= SUMMARY_TRIGGER_TURNS:
        return

    # 최근 FOLD_TO_TURNS 턴만 원문으로 남기고 나머지(오래된 것)를 접는다.
    folded = turns[:-FOLD_TO_TURNS]
    if not folded:
        return
    new_upto = folded[-1][-1]["id"]  # 접힌 마지막 메시지 id

    prev_summary = conv["summary"] or "(없음)"
    prompt = (
        "다음 [기존 요약]과 [추가 대화]를 통합해 갱신된 대화 요약을 작성하라. "
        "전체를 나열하지 말고 핵심 사실·맥락만 간결히 누적하라.\n\n"
        f"[기존 요약]\n{prev_summary}\n\n"
        f"[추가 대화]\n{_format_turns(folded)}"
    )
    new_summary = await llm.complete(prompt, tier="default")

    # 불변식: summary 갱신과 upto 전진을 한 트랜잭션으로(원문에서 빠지기 전에 접힘 보장).
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE conversations "
                "SET summary = %s, summary_upto_msg_id = %s, updated_at = now() "
                "WHERE id = %s",
                (new_summary, new_upto, conv_id),
            )


async def build_answer_context(pool, conv_id: int) -> dict:
    """답변용 컨텍스트: 요약 + 최근 N턴 원문.

    07이 여기에 [지시]·[참고자료(RAG)]·[질문]을 더해 최종 prompt를 조립한다.
    """
    conv = await _load_conv(pool, conv_id)
    msgs = await _unsummarized_msgs(pool, conv_id, conv["summary_upto_msg_id"])
    recent = _group_turns(msgs)[-RECENT_TURNS:]
    return {
        "summary": conv["summary"],
        "recent_turns": _format_turns(recent),
    }


async def build_rewrite_input(pool, conv_id: int, question: str) -> str | None:
    """다시쓰기 입력: 요약 + 직전 1~2턴 + 질문 (작게).

    첫 질문(이전 대화·요약 모두 없음)이면 None → 07이 다시쓰기를 생략.
    """
    conv = await _load_conv(pool, conv_id)
    msgs = await _unsummarized_msgs(pool, conv_id, conv["summary_upto_msg_id"])
    turns = _group_turns(msgs)

    if not turns and not conv["summary"]:
        return None  # 첫 질문

    parts: list[str] = []
    if conv["summary"]:
        parts.append(f"[요약]\n{conv['summary']}")
    if turns:
        parts.append(f"[직전 대화]\n{_format_turns(turns[-2:])}")
    parts.append(f"[질문]\n{question}")
    return "\n\n".join(parts)
