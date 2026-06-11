"""하이브리드 검색 테스트 (고정 시드 노트 세트)."""

import pytest
import pytest_asyncio

from app import indexing, search
from app.db import fetchrow

# 고정 시드: 서로 다른 주제 + 위키링크로 연결된 이웃.
_NOTES = {
    "반려동물": "고양이와 개는 사랑받는 반려동물이다. 함께 산책하고 교감하며 지낸다.",
    "연봉협상": "이직할 때 연봉 협상이 중요하다. 경력과 시장 가치를 근거로 제시한다.",
    "우주": "블랙홀은 빛조차 빠져나오지 못하는 천체다. 중력이 극도로 강하다.",
    # '취업준비'는 직접 매칭은 면접/자소서 쪽이고, [[연봉협상]]으로 이웃 연결된다.
    "취업준비": "면접 준비와 자기소개서 작성이 핵심이다. 이후 [[연봉협상]] 단계로 이어진다.",
}


@pytest_asyncio.fixture
async def seeded_kb(clean_db):
    pool = clean_db
    ids: dict[str, int] = {}
    for title, body in _NOTES.items():
        row = await fetchrow(
            pool,
            "INSERT INTO notes (title, body) VALUES (%s, %s) RETURNING id",
            (title, body),
        )
        ids[title] = row["id"]
        # is_new=True 로 인덱싱해야 인바운드 [[링크]]가 해소된다.
        await indexing.index_after_save(pool, row["id"], is_new=True)
    return pool, ids


async def test_vector(seeded_kb):
    pool, ids = seeded_kb
    # 글자가 안 겹치는 의미 유사 질의("강아지 키우기" vs 본문 "개/고양이").
    async with pool.connection() as conn:
        hits = await search._vector_search(conn, "강아지를 집에서 키우는 법", search.PER_LIST_K)
    assert hits, "벡터 검색 결과가 있어야 함"
    top_note = hits[0][1]
    assert top_note == ids["반려동물"]


async def test_bigm(seeded_kb):
    pool, ids = seeded_kb
    async with pool.connection() as conn:
        hits = await search._bigm_search(conn, "연봉", search.PER_LIST_K)
    note_ids = {nid for _, nid, _ in hits}
    assert ids["연봉협상"] in note_ids


def test_rrf():
    # chunk 1은 양쪽 리스트 상위, chunk 2는 한쪽만 상위.
    fused = dict(search.rrf([[1, 2, 3], [1, 4, 5]]))
    assert fused[1] > fused[2]
    assert fused[1] > fused[4]


async def test_relevance_gate(seeded_kb):
    """관련성 게이트: 직접 관련 노트만 남고 무관한 노트는 제외.

    (그래프 확장으로 끌어온 '비후보' 이웃은 게이트 면제 — 코드 경로 유지. 다만
    이 소규모 시드는 모든 노트가 이미 검색 후보라 면제가 발동하지 않는다.)
    """
    pool, ids = seeded_kb
    async with pool.connection() as conn:
        hits = await search.search(conn, "면접과 자기소개서 준비")
    note_ids = {h.note_id for h in hits}
    assert ids["취업준비"] in note_ids   # 직접 관련(글자·의미) → 포함
    assert ids["우주"] not in note_ids    # 무관 → 게이트로 제외


async def test_topk(seeded_kb):
    pool, _ = seeded_kb
    async with pool.connection() as conn:
        hits = await search.search(conn, "연봉 협상과 이직")
    assert len(hits) <= search.TOP_K
    assert all(h.note_title for h in hits)


async def test_empty(clean_db):
    # 인덱싱된 청크가 전혀 없으면(근거 없음) 빈 리스트.
    pool = clean_db
    async with pool.connection() as conn:
        hits = await search.search(conn, "아무 질의나")
    assert hits == []
