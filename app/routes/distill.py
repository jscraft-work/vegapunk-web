"""distill (지식 저장) 라우트 (기획서 12장).

대화 → 노트 후보 생성, 후보별 병합 대상 탐색, 병합 미리보기(LLM 통합+diff).
최종 저장은 공유 `/api/ingest`(notes.py) 사용.
"""

from __future__ import annotations

import difflib
import json

from fastapi import APIRouter, Depends, Request

from app import distill_match
from app.db import fetch, fetchrow
from app.llm import LLMClient, get_llm

router = APIRouter()

# 긴 노트 통합 시 섹션 단위 처리로 전환하는 길이 임계(문자).
LONG_NOTE_CHARS = 1500


_CTRL_ESCAPE = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def extract_json_array(text: str) -> list:
    """관대한 JSON 배열 추출. 앞뒤 잡소리/본문 줄바꿈 허용.

    첫 '['부터 문자열 상태를 추적하며 짝이 맞는 ']'까지 자른다. 이때 **문자열
    내부의 실제 제어문자(줄바꿈/탭)는 이스케이프**해 엄격 파서가 받아들이게
    한다(LLM이 body에 생 줄바꿈을 넣어도 견딤). 실패하면 빈 배열.
    """
    start = text.find("[")
    if start == -1:
        return []
    depth = 0
    in_str = False
    esc = False
    out: list[str] = []
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
                out.append(c)
            elif c == "\\":
                esc = True
                out.append(c)
            elif c == '"':
                in_str = False
                out.append(c)
            elif c in _CTRL_ESCAPE:
                out.append(_CTRL_ESCAPE[c])  # 생 제어문자 → 이스케이프
            else:
                out.append(c)
            continue
        out.append(c)
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads("".join(out))
                    return parsed if isinstance(parsed, list) else []
                except (ValueError, TypeError):
                    return []
    return []


def _build_distill_prompt(convo: str, titles: list[str], tags: list[str]) -> str:
    return (
        "다음 대화에서 장기 보관할 가치가 있는 지식을 주제별 노트 후보로 추출하라.\n"
        "- 인사·잡담만 있으면 빈 배열 []을 출력.\n"
        "- 관련 노트는 본문에 [[제목]]으로 링크. 태그는 기존 태그를 우선 재사용.\n"
        "- 각 후보: {\"title\":..,\"body\":..,\"tags\":[..]}. JSON 배열로만 응답.\n\n"
        f"[기존 노트 제목]\n{', '.join(titles) or '(없음)'}\n\n"
        f"[기존 태그]\n{', '.join(tags) or '(없음)'}\n\n"
        f"[대화]\n{convo}"
    )


@router.post("/api/distill")
async def distill(request: Request, body: dict, llm: LLMClient = Depends(get_llm)) -> dict:
    pool = request.app.state.pool
    conv_id = body["conv_id"]

    msgs = await fetch(
        pool,
        "SELECT role, content FROM messages WHERE conv_id = %s ORDER BY id",
        (conv_id,),
    )
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
    titles = [r["title"] for r in await fetch(pool, "SELECT title FROM notes")]
    tags = [r["name"] for r in await fetch(pool, "SELECT name FROM tags")]

    raw = await llm.complete(
        _build_distill_prompt(convo, titles, tags), tier="default"
    )
    candidates = extract_json_array(raw)

    out = []
    for c in candidates:
        if not isinstance(c, dict) or not c.get("title") or not c.get("body"):
            continue
        target = await distill_match.find_merge_target(pool, c["title"], c["body"])
        out.append(
            {
                "title": c["title"],
                "body": c["body"],
                "tags": c.get("tags", []),
                "merge_target": target,
            }
        )
    return {"candidates": out}


@router.post("/api/notes/merge-preview")
async def merge_preview(
    request: Request, body: dict, llm: LLMClient = Depends(get_llm)
) -> dict:
    pool = request.app.state.pool
    target_note_id = body["target_note_id"]
    candidate_body = body["candidate_body"]

    note = await fetchrow(
        pool, "SELECT body FROM notes WHERE id = %s", (target_note_id,)
    )
    if note is None:
        return {"error": "target not found"}
    old_body = note["body"]

    scope = (
        "노트가 길면 영향받는 ## 섹션만 통합하고 나머지는 그대로 보존하라."
        if len(old_body) > LONG_NOTE_CHARS
        else "노트가 짧으니 전체를 자연스럽게 통합하라."
    )
    prompt = (
        "다음 [기존 노트]에 [새 정보]를 통합한 본문을 만들어라. 보존 규칙:\n"
        "- 기존 정보를 임의로 삭제하지 마라.\n"
        "- 새 정보는 관련 위치에 통합하라(끝에 붙이지 마라).\n"
        "- 중복은 병합, 모순은 최신을 쓰되 변경을 표시하라(예: 연봉 6천 (이전 5천에서 변경)).\n"
        "- 마크다운/[[링크]]/표를 보존하라.\n"
        f"- {scope}\n"
        "- 통합된 본문만 출력하라.\n\n"
        f"[기존 노트]\n{old_body}\n\n[새 정보]\n{candidate_body}"
    )
    merged_body = await llm.complete(prompt, tier="default")

    diff = "\n".join(
        difflib.unified_diff(
            old_body.splitlines(),
            merged_body.splitlines(),
            fromfile="old",
            tofile="merged",
            lineterm="",
        )
    )
    return {"merged_body": merged_body, "diff": diff}
