"""distill 병합 대상 찾기 (기획서 12장).

후보 1개 → 대상 1개 원칙. **후보 본문 통째 임베딩 금지**(벡터 흐려짐).
신호 두 가지를 결합한다:
  1. 제목 신호: 후보 title vs 기존 title 정규화 일치(공백/대소문자 무시) → 강함.
  2. 청크-청크: 후보 본문을 청크로 쪼개 각 청크로 벡터검색 → 어느 note_id에
     매칭이 몰리는지 집계.
확신 없으면 합치지 않는다(오병합이 중복보다 나쁨) → 임계 미만은 None.
"""

from __future__ import annotations

from app import search
from app.chunking import split_into_chunks
from app.db import fetch

# 청크 매칭으로 인정할 최대 코사인 거리(실측: 관련≈0.12~0.18, 무관≈0.21+).
MATCH_DIST = 0.19
# 대상으로 판정할 최소 득표 비율(매칭된 청크 / 전체 청크).
CONCENTRATION_RATIO = 0.34


def _norm(title: str) -> str:
    return "".join(title.split()).casefold()


async def find_merge_target(pool, user_id: int, title: str, body: str) -> dict | None:
    """후보의 병합 대상을 찾는다(유저 노트 한정). {note_id, title, similarity} 또는 None."""
    notes = await fetch(pool, "SELECT id, title FROM notes WHERE user_id = %s", (user_id,))

    # 1) 제목 신호(정규화 일치) — 강한 신호.
    cand = _norm(title)
    for n in notes:
        if _norm(n["title"]) == cand:
            return {"note_id": n["id"], "title": n["title"], "similarity": 1.0}

    # 2) 청크-청크 집중도.
    chunks = split_into_chunks(body)
    if not chunks:
        return None

    votes: dict[int, int] = {}
    async with pool.connection() as conn:
        for ch in chunks:
            hits = await search._vector_search(conn, ch, 1, user_id)  # 청크별 최근접 1건
            if hits and hits[0][2] <= MATCH_DIST:
                nid = hits[0][1]
                votes[nid] = votes.get(nid, 0) + 1

    if not votes:
        return None

    nid, count = max(votes.items(), key=lambda kv: kv[1])
    ratio = count / len(chunks)
    if ratio < CONCENTRATION_RATIO:
        return None  # 약한 매칭 → 새 노트

    title_row = next((n["title"] for n in notes if n["id"] == nid), None)
    return {"note_id": nid, "title": title_row, "similarity": round(ratio, 3)}
