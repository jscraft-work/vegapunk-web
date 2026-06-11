"""로컬 임베딩 서비스 (multilingual-e5-large, 1024차원).

e5 모델은 입력에 역할 prefix를 요구한다:
  - 문서(인덱싱 대상): ``"passage: "``
  - 질의(검색):        ``"query: "``
이 prefix를 **호출자에게 맡기지 않고 이 모듈이 강제로 붙인다**. 03(인덱싱)·
04(검색)·08(distill)이 각각 붙이면 한 곳만 빠져도 검색이 조용히 망가지기 때문.

정규화 정책: e5는 코사인 유사도를 쓴다. 04의 검색이 pgvector
``vector_cosine_ops``(``<=>``)를 사용하므로 크기와 무관하며, fastembed 기본
동작을 그대로 따른다(추가 정규화 불필요).

모델은 무거우므로 전역 싱글톤으로 1회만 lazy 로드한다(앱 기동 블로킹 방지).
"""

from __future__ import annotations

import os

import anyio

_MODEL_NAME = "intfloat/multilingual-e5-large"
_DIM = 1024

_model = None


def _get_model():
    """fastembed 모델 싱글톤. 최초 호출 시 1회 로드(수 초~수십 초 소요)."""
    global _model
    if _model is None:
        # import도 lazy: fastembed/onnx 로딩 비용을 최초 사용 시점까지 미룬다.
        from fastembed import TextEmbedding

        cache_dir = os.environ.get("FASTEMBED_CACHE", "./.fastembed_cache")
        _model = TextEmbedding(model_name=_MODEL_NAME, cache_dir=cache_dir)
    return _model


def _embed(prefixed: list[str]) -> list[list[float]]:
    """prefix가 이미 붙은 텍스트들을 배치 임베딩하고 1024차원 검증."""
    model = _get_model()
    vectors = [vec.tolist() for vec in model.embed(prefixed)]
    for vec in vectors:
        if len(vec) != _DIM:
            raise ValueError(
                f"임베딩 차원이 {_DIM}이어야 하는데 {len(vec)}임 (모델: {_MODEL_NAME})"
            )
    return vectors


def embed_passages(texts: list[str]) -> list[list[float]]:
    """문서 텍스트들을 배치 임베딩한다. 각 텍스트에 ``"passage: "`` 접두.

    빈 리스트 입력 → 빈 리스트 반환(호출자 가드 단순화).
    """
    if not texts:
        return []
    return _embed([f"passage: {t}" for t in texts])


def embed_query(text: str) -> list[float]:
    """질의 텍스트를 단건 임베딩한다. ``"query: "`` 접두."""
    return _embed([f"query: {text}"])[0]


# ── 비동기 래퍼 ────────────────────────────────────────────────
# fastembed는 동기/CPU 바운드. FastAPI 비동기 컨텍스트에서 이벤트 루프를
# 막지 않도록 워커 스레드에서 실행한다.


async def aembed_passages(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return await anyio.to_thread.run_sync(embed_passages, texts)


async def aembed_query(text: str) -> list[float]:
    return await anyio.to_thread.run_sync(embed_query, text)
