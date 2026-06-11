# Task 04: 하이브리드 검색 엔진

## 목표
질의를 받아 **글자검색(pg_bigm) + 의미검색(pgvector)** 을 각각 돌리고 **RRF로 융합**, 찾은 노트의 `[[링크]]` 이웃을 **1단계 그래프 확장**, 후보를 넉넉히(~20) 모은 뒤 상위 ~5청크로 **추려서** 반환한다(기획서 8장). 검색 쿼리에는 `query:` prefix가 자동 적용된다.

## 선행 조건
- Task 01~03 완료(스키마·임베딩·인덱싱). 검색 대상 청크/임베딩/edges가 존재.

## 구현 상세

### 4.1 개별 검색 (`app/search.py`)
- `async def _vector_search(conn, query: str, k: int) -> list[(chunk_id, note_id, dist)]`:
  - `embedding.aembed_query(query)` → `SELECT id, note_id, embedding <=> :qvec AS dist FROM chunks ORDER BY dist LIMIT k` (코사인 거리, 작을수록 유사).
- `async def _bigm_search(conn, query: str, k: int) -> list[(chunk_id, note_id, score)]`:
  - `pg_bigm` 사용(2-gram, 한글 단문에 강함): `SELECT id, note_id, bigm_similarity(text, :q) AS score FROM chunks WHERE text LIKE likequery(:q) ORDER BY score DESC LIMIT k`. `LIKE likequery(:q)`가 `idx_chunks_bigm` GIN 인덱스를 탄다. 매칭이 너무 적으면 `text =% :q`(유사도 임계) 폴백 고려 — 임계는 상수/`SET pg_bigm.similarity_limit`로 노출.
  - **검색어는 원문 query**(여기엔 e5 `query:` prefix를 붙이지 않는다 — prefix는 벡터검색 전용. 글자검색에 prefix를 넣으면 "query:"라는 리터럴까지 매칭되어 오염).

### 4.2 RRF 융합
- `rrf(rank_lists, k_const=60) -> ordered chunk_ids` — 각 리스트에서 순위 r(0-based)에 대해 `1/(k_const + r + 1)` 가산, chunk_id별 합산 점수로 내림차순. 양쪽 상위일수록 높음.
- 후보 풀 크기 `CANDIDATES=20`(상수).

### 4.3 그래프 확장
- 융합 상위 후보가 속한 note들의 `edges`(dst_note 해석된 것)에서 **1-hop 이웃 노트**를 모음. 이웃 노트의 청크 중 질의 벡터에 가장 가까운 청크를 소수 추가(이미 후보에 있으면 점수 보너스). 확장은 후보 풀을 과도히 키우지 않게 상한(예: 이웃 노트당 1청크).

### 4.4 추리기 + 결과 형태
- 최종 상위 `TOP_K=5`(상수) 청크 선택.
- `async def search(conn, query: str) -> list[SearchHit]` 반환. `SearchHit = {chunk_id, note_id, note_title, text, score}`.
- 빈 결과 가드: 매칭 0건이면 빈 리스트(07이 "근거 없음" 처리에 사용).

### 4.5 상수/튜닝 노출
- `CANDIDATES`, `TOP_K`, RRF `k_const`, pg_bigm 유사도 임계 등을 모듈 상수(또는 config)로 모아 기획서 10장 파라미터와 매핑. 주석으로 기획서 값 명시.

## 완료 기준

### 자동 검증 (테스트) — 고정 시드 노트 세트로 검증
- [ ] `tests/test_search.py::test_vector` — 의미 유사 질의가 정답 노트를 상위로(글자 안 겹쳐도 매칭).
- [ ] `tests/test_search.py::test_bigm` — 2글자 한글 키워드/부분일치가 글자검색으로 매칭(예: "연봉", "이직").
- [ ] `tests/test_search.py::test_rrf` — 양쪽에서 상위인 청크가 한쪽만 상위인 것보다 최종 순위 높음.
- [ ] `tests/test_search.py::test_graph_expansion` — `[[링크]]`로 연결된 이웃 노트 내용이 후보에 포함됨(직접 매칭 없어도).
- [ ] `tests/test_search.py::test_topk` — 결과가 TOP_K 이하, note_title 채워짐.
- [ ] `tests/test_search.py::test_empty` — 무관 질의 → 빈 리스트.

**검증 실행 명령어**: `uv run pytest tests/test_search.py -q`

## 참고사항
- `query:` prefix는 02 모듈이 책임지므로 여기서 또 붙이지 말 것(이중 prefix 금지).
- 데이터가 적으면 HNSW가 seq scan으로 동작 — 결과 정확성에는 영향 없음.
- 07(채팅)은 이 `search()`를 **다시쓴 쿼리 한 줄**로 호출하고, 결과 note_id를 citations로 저장한다.
- 08(distill 병합매칭)은 이 모듈의 벡터검색 부분을 청크-청크 매칭에 재사용한다.
