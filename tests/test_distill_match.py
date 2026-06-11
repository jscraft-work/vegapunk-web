"""distill 병합 대상 탐색 테스트 (실제 임베딩)."""

import pytest

from app import distill_match
from app.ingest import ingest_note


async def _seed(pool, title, body):
    await ingest_note(pool, title=title, body=body, tags=[])


async def test_title_signal(clean_db):
    pool = clean_db
    await _seed(pool, "연봉협상", "이직 시 연봉 협상 전략과 시장가치 산정.")
    # 공백만 다른 제목 → 정규화 일치 → 강한 신호.
    target = await distill_match.find_merge_target(
        pool, "연봉 협상", "아무 본문"
    )
    assert target is not None
    assert target["title"] == "연봉협상"
    assert target["similarity"] == 1.0


async def test_chunk_concentration(clean_db):
    pool = clean_db
    await _seed(
        pool,
        "블랙홀",
        "블랙홀은 빛조차 빠져나오지 못하는 천체다. 사건의 지평선 너머는 관측할 수 없다. "
        "중력이 극도로 강해 시공간이 휘어진다.",
    )
    # 제목은 다르지만 내용이 강하게 겹침 → 청크 집중도로 매칭.
    target = await distill_match.find_merge_target(
        pool,
        "우주의 신비",
        "사건의 지평선 너머는 관측할 수 없다. 빛조차 빠져나오지 못하는 천체.",
    )
    assert target is not None
    assert target["title"] == "블랙홀"


async def test_below_threshold_new(clean_db):
    pool = clean_db
    await _seed(pool, "블랙홀", "블랙홀은 빛조차 빠져나오지 못하는 천체다.")
    # 전혀 무관한 후보 → 매칭 없음 → None(새 노트).
    target = await distill_match.find_merge_target(
        pool, "김치찌개 레시피", "돼지고기와 신김치를 넣고 끓인다. 두부를 더한다."
    )
    assert target is None
