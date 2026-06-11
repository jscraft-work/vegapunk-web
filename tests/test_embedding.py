"""임베딩 서비스 테스트.

모델 로드는 수 초~수십 초 걸리므로 세션 스코프로 1회만 워밍업한다.
"""

import pytest

from app import embedding


@pytest.fixture(scope="session", autouse=True)
def _warm_model():
    # 모델을 세션 시작 시 1회 로드(이후 테스트는 로드 비용 없음).
    embedding._get_model()


def test_dim():
    vec = embedding.embed_query("안녕")
    assert len(vec) == 1024


def test_batch():
    vecs = embedding.embed_passages(["a", "b", "c"])
    assert len(vecs) == 3
    assert all(len(v) == 1024 for v in vecs)


def test_prefix_effect():
    # 같은 문장을 passage/query로 임베딩하면 prefix 차이로 결과가 달라야 한다.
    text = "비건은 인공지능 연구자다"
    as_passage = embedding.embed_passages([text])[0]
    as_query = embedding.embed_query(text)
    assert as_passage != as_query


def test_empty():
    assert embedding.embed_passages([]) == []
