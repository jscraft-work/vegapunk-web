"""하이브리드 검색 (기획서 8장).

글자검색(pg_bigm) + 의미검색(pgvector)을 각각 돌려 **RRF로 융합**하고,
찾은 노트의 `[[링크]]` 이웃을 **1-hop 그래프 확장**한 뒤 상위 ``TOP_K`` 청크로
추려서 반환한다.

prefix 규약: 질의의 ``query:`` prefix는 02(embedding) 모듈이 책임진다.
글자검색에는 prefix를 붙이지 않는다("query:" 리터럴까지 매칭되어 오염되므로).
"""

from __future__ import annotations

from dataclasses import dataclass

from pgvector import Vector

from app import embedding

# ── 튜닝 상수 (기획서 10장 파라미터) ───────────────────────────
CANDIDATES = 20  # 융합 후보 풀 크기
TOP_K = 5  # 최종 반환 청크 수
RRF_K = 60  # RRF 상수(클수록 순위 차 완만)
PER_LIST_K = CANDIDATES  # 개별 검색이 가져올 후보 수
BIGM_SIMILARITY_LIMIT = 0.1  # pg_bigm 유사도 임계(짧은 한글 단문 대응)
GRAPH_NEIGHBOR_BONUS = 1.0 / (RRF_K + 1)  # 이웃 청크 가산 점수(상위 1건 상당)


@dataclass
class SearchHit:
    chunk_id: int
    note_id: int
    note_title: str
    text: str
    score: float


async def _vector_search(conn, query: str, k: int) -> list[tuple[int, int, float]]:
    """의미검색: 코사인 거리(작을수록 유사). (chunk_id, note_id, dist)."""
    qvec = Vector(await embedding.aembed_query(query))
    cur = await conn.execute(
        "SELECT id, note_id, embedding <=> %s AS dist "
        "FROM chunks WHERE embedding IS NOT NULL "
        "ORDER BY dist LIMIT %s",
        (qvec, k),
    )
    return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


async def _bigm_search(conn, query: str, k: int) -> list[tuple[int, int, float]]:
    """글자검색: pg_bigm 2-gram 유사도. (chunk_id, note_id, score).

    ``LIKE likequery(:q)``가 GIN(idx_chunks_bigm)을 탄다. 원문 query 사용
    (e5 prefix 금지). 짧은 질의로 LIKE 매칭이 비면 유사도(=%) 폴백.
    """
    # LIKE likequery 가 GIN(idx_chunks_bigm)을 타며 정확 부분일치를 잡고,
    # bigm_similarity 임계는 부분일치가 빌 때의 2-gram 유사도 폴백.
    # (=% 연산자 대신 함수형을 써서 % 이스케이프/스키마 모호성을 피한다.)
    cur = await conn.execute(
        "SELECT id, note_id, bigm_similarity(text, %(q)s) AS score "
        "FROM chunks "
        "WHERE text LIKE likequery(%(q)s) "
        "OR bigm_similarity(text, %(q)s) >= %(thr)s "
        "ORDER BY score DESC LIMIT %(k)s",
        {"q": query, "k": k, "thr": BIGM_SIMILARITY_LIMIT},
    )
    return [(r[0], r[1], r[2]) for r in await cur.fetchall()]


def rrf(rank_lists: list[list[int]], k_const: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion. 각 리스트 순위 r(0-based)에 1/(k+r+1) 가산.

    (chunk_id, score) 를 score 내림차순으로 반환. 양쪽 상위일수록 높음.
    """
    scores: dict[int, float] = {}
    for ranking in rank_lists:
        for r, chunk_id in enumerate(ranking):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k_const + r + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


async def _graph_expand(
    conn, note_ids: list[int], query: str, scores: dict[int, float]
) -> None:
    """1-hop 이웃 노트의 질의 최근접 청크를 후보(scores)에 가산.

    이웃 노트당 1청크로 상한. 이미 후보면 보너스만 더한다(in-place 갱신).
    """
    if not note_ids:
        return
    cur = await conn.execute(
        "SELECT DISTINCT dst_note FROM edges "
        "WHERE src_note = ANY(%s) AND dst_note IS NOT NULL "
        "AND dst_note <> ALL(%s)",
        (note_ids, note_ids),
    )
    neighbors = [r[0] for r in await cur.fetchall()]
    if not neighbors:
        return

    qvec = await embedding.aembed_query(query)
    for nid in neighbors:
        # 이웃 노트에서 질의에 가장 가까운 청크 1건.
        c = await conn.execute(
            "SELECT id FROM chunks "
            "WHERE note_id = %s AND embedding IS NOT NULL "
            "ORDER BY embedding <=> %s LIMIT 1",
            (nid, qvec),
        )
        row = await c.fetchone()
        if row is None:
            continue
        chunk_id = row[0]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + GRAPH_NEIGHBOR_BONUS


async def search(conn, query: str) -> list[SearchHit]:
    """하이브리드 검색 진입점. 상위 ``TOP_K`` SearchHit 반환(매칭 0건이면 [])."""
    vec = await _vector_search(conn, query, PER_LIST_K)
    big = await _bigm_search(conn, query, PER_LIST_K)
    if not vec and not big:
        return []

    note_of = {cid: nid for cid, nid, _ in vec}
    note_of.update({cid: nid for cid, nid, _ in big})

    fused = rrf([[c for c, _, _ in vec], [c for c, _, _ in big]])
    scores = dict(fused[:CANDIDATES])

    # 후보 노트 기준 1-hop 그래프 확장.
    cand_notes = list({note_of[cid] for cid in scores if cid in note_of})
    await _graph_expand(conn, cand_notes, query, scores)

    top = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:TOP_K]
    if not top:
        return []

    # 청크 메타(note_title, text) 로드.
    chunk_ids = [cid for cid, _ in top]
    cur = await conn.execute(
        "SELECT c.id, c.note_id, n.title, c.text "
        "FROM chunks c JOIN notes n ON n.id = c.note_id "
        "WHERE c.id = ANY(%s)",
        (chunk_ids,),
    )
    meta = {r[0]: (r[1], r[2], r[3]) for r in await cur.fetchall()}

    hits: list[SearchHit] = []
    for cid, score in top:
        if cid not in meta:
            continue
        note_id, title, text = meta[cid]
        hits.append(
            SearchHit(
                chunk_id=cid,
                note_id=note_id,
                note_title=title,
                text=text,
                score=score,
            )
        )
    return hits
