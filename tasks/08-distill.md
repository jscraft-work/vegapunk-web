# Task 08: distill (지식 저장)

## 목표
대화를 읽어 **노트 후보**를 만들고(기획서 12장), 각 후보의 **병합 대상**을 검색엔진 재사용으로 찾고, 병합 시 **LLM 통합(재작성)** 미리보기+diff를 제공하며, 최종 저장(`/api/ingest`)에서 신규/병합/수정 후 인덱싱한다. append 금지, 3중 안전망(보존규칙·diff·버전백업) 준수.

## 선행 조건
- Task 03(인덱싱), 04(검색), 05(LLM) 완료. (07은 권장이나 독립 가능.)

## 구현 상세

### 8.1 후보 생성 (`app/routes/distill.py`)
- `POST /api/distill { conv_id }` → `{ candidates: [{title, body, tags[], merge_target}] }`.
- distill 프롬프트(기획서 12장 골자): 입력=대화 전체 + 기존 노트 제목 목록 + 기존 태그 목록. 주제별 후보 분할, 인사·잡담 제외(빈 배열), 관련 노트는 본문에 `[[제목]]`, 태그는 기존 우선 재사용. **JSON 배열로 응답**.
- **관대한 JSON 파싱**: 앞뒤 잡소리 허용하고 `[...]`만 추출. body에 실제 줄바꿈 포함 → 엄격 파서 금지(견고한 추출 함수 `extract_json_array`).

### 8.2 병합 대상 찾기 (`app/distill_match.py`) — 각 후보별
- **후보 본문 통째 임베딩 금지**(벡터 흐려짐).
1. **제목 신호**: 후보 title vs 기존 title 정확/근접 일치(+ title만 임베딩해 유사 제목 탐색) → 강한 신호.
2. **청크-청크**: 후보 본문을 `chunking.split_into_chunks`로 쪼개, 각 청크로 04 벡터검색 → 매칭이 **어느 note_id에 몰리는지** 집계.
3. **판정**: (집중도 + 제목 가중)≥임계 → `merge_target={note_id,title,similarity}`. 미만 → `null`(새 노트 기본 — 확신 없으면 안 합침).
- **후보 1개 → 대상 1개 원칙**.

### 8.3 병합 미리보기
- `POST /api/notes/merge-preview { target_note_id, candidate_body }` → `{ merged_body, diff }`.
- LLM(`tier="default"`) **보존규칙 프롬프트**: 기존 정보 모두 보존(임의 삭제 금지)/추가는 관련 위치 통합(끝에 붙이기 금지)/중복 병합/모순 시 최신+표시(`연봉 6천 (이전 5천에서 변경)`)/마크다운·`[[링크]]`·표 보존/통합본 본문만 출력.
- **긴 노트**: 영향받는 `##` 섹션만 통합, 나머지 보존. 짧은 노트는 통째. (길이 임계 상수.)
- `diff`는 옛 본문 vs 통합본 라인 diff(서버에서 계산).

### 8.4 저장 (ingest)
- `POST /api/ingest { title, body, tags, merge_into: note_id|null }` → `{ note_id, title, action: "created"|"merged"|"updated" }`.
- 저장 트랜잭션: (병합/수정이면) `note_versions`에 **이전 본문 백업** → notes upsert → tags/note_tags 정규화(기존 태그 재사용, 없으면 생성).
- **인덱싱**:
  - 단건(수동/단일 후보) → **동기**(03 `index_after_save`, 저장 직후 검색 가능).
  - distill 다건 → **FastAPI BackgroundTasks 비동기**(완료 전 그 노트만 잠시 검색서 빠짐).

## 완료 기준

### 자동 검증 (테스트, FakeLLM)
- [ ] `tests/test_distill.py::test_lenient_json` — 앞뒤 잡소리+본문 줄바꿈 있는 응답에서 후보 배열 정확 추출; 잡담만 있으면 빈 배열.
- [ ] `tests/test_distill_match.py::test_title_signal` — 동일/유사 제목 후보 → 해당 노트가 merge_target.
- [ ] `tests/test_distill_match.py::test_chunk_concentration` — 제목 다르고 내용 겹치는 후보 → 매칭 몰린 note가 target.
- [ ] `tests/test_distill_match.py::test_below_threshold_new` — 약한 매칭 → merge_target null(새 노트).
- [ ] `tests/test_distill.py::test_merge_preview` — 보존규칙 프롬프트 전달 + merged_body/diff 반환.
- [ ] `tests/test_distill.py::test_ingest_versions` — 병합 저장 시 note_versions에 이전 본문 적재, action 정확.
- [ ] `tests/test_distill.py::test_ingest_reindex` — 저장 후 새 본문이 검색에 반영(동기 경로).

**검증 실행 명령어**: `uv run pytest tests/test_distill.py tests/test_distill_match.py -q`

## 참고사항
- 오병합이 중복보다 나쁨 → 임계 미달은 항상 새 노트.
- append 절대 금지(노트 부패 → RAG 품질 저하).
- merge-preview는 사용자가 "기존 병합" 택한 후보에 한해 호출(lazy) — N+1 LLM 호출 누적 주의(FE는 11에서 1건씩 호출).
