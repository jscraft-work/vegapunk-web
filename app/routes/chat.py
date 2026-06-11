"""채팅 파이프라인 & SSE + 대화 CRUD (기획서 6장).

매 턴 파이프라인:
  질문 → (이전 대화 있으면) 다시쓰기(low) → 검색 → 답변 컨텍스트 조립 →
  openclaw 스트리밍 → 메시지/citations 저장 → 백그라운드 증분 요약.

다시쓴 쿼리는 **검색용일 뿐** — [질문]에는 항상 원문을 넣는다.
요약은 절대 답변을 막지 않는다(응답 후 BackgroundTask).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from app import memory, search
from app.db import execute, fetch, fetchrow
from app.llm import LLMClient, get_llm

router = APIRouter()

# ── 프롬프트 조각 ──────────────────────────────────────────────
_REWRITE_INSTRUCTION = (
    "다음 맥락을 참고해, 마지막 질문을 지식베이스 검색에 적합한 "
    "독립적인 한 줄 검색어로 바꿔라. 검색어만 출력하라.\n\n"
)
_ANSWER_INSTRUCTION = (
    "[지시]\n당신은 사용자의 개인 지식베이스를 근거로 답하는 비서다. "
    "참고자료에 근거가 없으면 모른다고 솔직히 답하라."
)


def _sse(event: str, payload) -> dict:
    return {"event": event, "data": json.dumps(payload, ensure_ascii=False)}


def _dedupe_sources(hits: list[search.SearchHit]) -> list[dict]:
    """note_id 기준 중복 제거(최고 점수 유지). 답변 전에 발행."""
    best: dict[int, dict] = {}
    for h in hits:
        cur = best.get(h.note_id)
        if cur is None or h.score > cur["score"]:
            best[h.note_id] = {
                "note_id": h.note_id,
                "title": h.note_title,
                "score": h.score,
            }
    return sorted(best.values(), key=lambda s: s["score"], reverse=True)


def _assemble_answer_prompt(ctx: dict, hits: list[search.SearchHit], question: str) -> str:
    """[지시]+[요약?]+[최근 대화?]+[참고자료=이번 RAG만]+[질문=원문]."""
    parts = [_ANSWER_INSTRUCTION]
    if ctx["summary"]:
        parts.append(f"[요약]\n{ctx['summary']}")
    if ctx["recent_turns"]:
        parts.append(f"[최근 대화]\n{ctx['recent_turns']}")
    if hits:
        refs = "\n\n".join(f"- ({h.note_title}) {h.text}" for h in hits)
        parts.append(f"[참고자료]\n{refs}")
    else:
        parts.append("[참고자료]\n(관련 노트 없음)")
    parts.append(f"[질문]\n{question}")
    return "\n\n".join(parts)


async def _insert_message(pool, conv_id, role, content, *, sent_prompt=None) -> int:
    row = await fetchrow(
        pool,
        "INSERT INTO messages (conv_id, role, content, sent_prompt) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (conv_id, role, content, sent_prompt),
    )
    return row["id"]


async def _save_citations(pool, message_id, hits: list[search.SearchHit]) -> None:
    seen: set[int] = set()
    async with pool.connection() as conn:
        for h in hits:
            if h.note_id in seen:
                continue
            seen.add(h.note_id)
            await conn.execute(
                "INSERT INTO message_citations (message_id, note_id, score) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (message_id, h.note_id, h.score),
            )
        await conn.commit()


async def _chat_stream(pool, llm, conv_id, q, *, new_conv, title):
    try:
        if new_conv:
            yield _sse("conversation", {"id": conv_id, "title": title or ""})

        # 2. 다시쓰기(검색용). 첫 질문이면 원문 사용.
        rewrite_in = await memory.build_rewrite_input(pool, conv_id, q)
        if rewrite_in is None:
            search_query = q
        else:
            rq = await llm.complete(_REWRITE_INSTRUCTION + rewrite_in, tier="low")
            search_query = rq.strip() or q

        # 3. 검색 → sources (답변 전에 먼저).
        async with pool.connection() as conn:
            hits = await search.search(conn, search_query)
        yield _sse("sources", _dedupe_sources(hits))

        # 4. 답변 컨텍스트 + 프롬프트 조립(이번 RAG만, 질문은 원문).
        ctx = await memory.build_answer_context(pool, conv_id)
        prompt = _assemble_answer_prompt(ctx, hits, q)

        # 5. user 저장 → 스트리밍.
        await _insert_message(pool, conv_id, "user", q)
        parts: list[str] = []
        async for delta in llm.stream(prompt, tier="default"):
            parts.append(delta)
            yield _sse("answer", {"text": delta})

        # 6. assistant + citations 저장(부분 답변도 저장됨).
        answer = "".join(parts)
        msg_id = await _insert_message(
            pool, conv_id, "assistant", answer, sent_prompt=prompt
        )
        await _save_citations(pool, msg_id, hits)

        # 7. done.
        yield _sse("done", {})
    except Exception as exc:  # noqa: BLE001 — SSE error 이벤트로 전달
        yield _sse("error", {"message": str(exc)})

    # suggest {hint} 이벤트는 이번 태스크 범위 밖(트리거 미정의) — 예약만, 미발행.


@router.get("/api/chat")
async def chat(
    request: Request,
    q: str = Query(...),
    conv: int = Query(0),
    llm: LLMClient = Depends(get_llm),
):
    pool = request.app.state.pool
    new_conv = conv == 0
    if new_conv:
        row = await fetchrow(
            pool,
            "INSERT INTO conversations DEFAULT VALUES RETURNING id, title",
            None,
        )
        conv_id, title = row["id"], row["title"]
    else:
        conv_id, title = conv, None

    return EventSourceResponse(
        _chat_stream(pool, llm, conv_id, q, new_conv=new_conv, title=title),
        # 응답을 막지 않도록 요약은 스트림 종료 후 백그라운드로.
        background=BackgroundTask(memory.maybe_update_summary, pool, llm, conv_id),
    )


# ── 대화 CRUD ──────────────────────────────────────────────────


@router.get("/api/conversations")
async def list_conversations(request: Request) -> dict:
    rows = await fetch(
        request.app.state.pool,
        "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC",
    )
    return {
        "conversations": [
            {"id": r["id"], "title": r["title"], "updated": r["updated_at"].isoformat()}
            for r in rows
        ]
    }


@router.get("/api/conversations/{conv_id}")
async def get_conversation(request: Request, conv_id: int) -> dict:
    pool = request.app.state.pool
    conv = await fetchrow(
        pool, "SELECT id, title FROM conversations WHERE id = %s", (conv_id,)
    )
    if conv is None:
        return {"error": "not found"}
    msgs = await fetch(
        pool,
        "SELECT id, role, content FROM messages WHERE conv_id = %s ORDER BY id",
        (conv_id,),
    )
    # citations: message_id → [{note_id, title, score}].
    cites = await fetch(
        pool,
        "SELECT mc.message_id, mc.note_id, n.title, mc.score "
        "FROM message_citations mc "
        "JOIN messages m ON m.id = mc.message_id "
        "LEFT JOIN notes n ON n.id = mc.note_id "
        "WHERE m.conv_id = %s",
        (conv_id,),
    )
    by_msg: dict[int, list[dict]] = {}
    for c in cites:
        by_msg.setdefault(c["message_id"], []).append(
            {"note_id": c["note_id"], "title": c["title"], "score": c["score"]}
        )
    return {
        "id": conv["id"],
        "title": conv["title"],
        "messages": [
            {
                "role": m["role"],
                "content": m["content"],
                "sources": by_msg.get(m["id"], []),
            }
            for m in msgs
        ],
    }


@router.patch("/api/conversations/{conv_id}")
async def rename_conversation(request: Request, conv_id: int, body: dict) -> dict:
    title = body.get("title")
    await execute(
        request.app.state.pool,
        "UPDATE conversations SET title = %s, updated_at = now() WHERE id = %s",
        (title, conv_id),
    )
    return {"ok": True, "title": title}


@router.post("/api/conversations/{conv_id}/retitle")
async def retitle_conversation(
    request: Request, conv_id: int, llm: LLMClient = Depends(get_llm)
) -> dict:
    pool = request.app.state.pool
    msgs = await fetch(
        pool,
        "SELECT role, content FROM messages WHERE conv_id = %s ORDER BY id LIMIT 6",
        (conv_id,),
    )
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
    prompt = (
        "다음 대화에 어울리는 짧은 제목(10자 내외)을 한 줄로만 출력하라.\n\n" + convo
    )
    title = (await llm.complete(prompt, tier="low")).strip()
    await execute(
        pool,
        "UPDATE conversations SET title = %s, updated_at = now() WHERE id = %s",
        (title, conv_id),
    )
    return {"title": title}


@router.delete("/api/conversations/{conv_id}")
async def delete_conversation(request: Request, conv_id: int) -> dict:
    await execute(
        request.app.state.pool,
        "DELETE FROM conversations WHERE id = %s",
        (conv_id,),
    )
    return {"deleted": True, "id": conv_id}
