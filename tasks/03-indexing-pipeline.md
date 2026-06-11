# Task 03: 인덱싱 파이프라인

## 목표
노트가 생기거나 바뀌면 **그 노트만** 부분 재인덱싱한다(기획서 13장): 본문을 청크로 분할 → 기존 청크 삭제 → 새 청크 INSERT + 임베딩 → 본문의 `[[링크]]` 파싱 → edges 재생성(dst_note 해석) → 신규 노트면 미해결 edges 채움. 전 과정을 단일 트랜잭션으로 수행한다.

## 선행 조건
- Task 01(스키마), Task 02(임베딩) 완료.

## 구현 상세

### 3.1 청크 분할 (`app/chunking.py`)
- `split_into_chunks(body: str) -> list[str]`:
  - 1차 경계: 마크다운 헤딩(`#`~`######`)과 빈 줄 기준 문단.
  - 2차 분할: 한 청크가 너무 길면(기준 ~수백 토큰 ≈ **약 500자**, 상수로 노출) 문장/줄 경계로 추가 분할.
  - 빈/공백 청크 제거, 원문 순서 유지.
- 코드블록(```)은 분할 도중 쪼개지지 않게 한 덩이로 보존(가능한 범위).

### 3.2 위키링크 파싱 (`app/wikilink.py`)
- `extract_links(body: str) -> list[str]` — `[[제목]]` 안의 제목 텍스트를 순서·중복 제거하여 반환. `[[제목|별칭]]` 형식이면 제목부만 취함. 코드블록·인라인코드 내부는 제외.

### 3.3 재인덱싱 (`app/indexing.py`)
- `async def reindex_note(conn, note_id: int) -> None` — **호출자가 연 트랜잭션 안에서** 동작(자체 commit 안 함; 호출자가 트랜잭션 경계 소유):
  1. `notes`에서 title·body 로드.
  2. `DELETE FROM chunks WHERE note_id=...` (gin/hnsw 인덱스는 자동 정리).
  3. `split_into_chunks(body)` → 각 청크 `INSERT chunks(note_id, ord, text)`.
  4. `embedding.aembed_passages(texts)` 배치 → 각 청크 `UPDATE ... SET embedding=...` (또는 INSERT 시 함께). pgvector 어댑터로 list→vector.
  5. `extract_links(body)` → 기존 `edges WHERE src_note=...` 삭제 후 재생성. 각 dst_title에 대해 `notes`에서 동명 노트 조회 → 있으면 `dst_note=id`, 없으면 NULL(미해결).
  6. `UPDATE notes SET updated_at=now()`.
- `async def resolve_inbound_links(conn, title: str, note_id: int)` — 신규 노트 생성 시 호출: `UPDATE edges SET dst_note=:note_id WHERE dst_title=:title AND dst_note IS NULL`.
- `async def unresolve_links_to(conn, note_id: int)` — 노트 삭제 시: 그 노트를 가리키던 `edges`의 `dst_note`만 NULL로(행 자체는 유지). (실제 삭제 호출은 09에서.)
- 편의 진입점: `async def index_after_save(pool, note_id, *, is_new: bool)` — 트랜잭션 열고 `reindex_note` + (`is_new`면) `resolve_inbound_links` 실행 후 commit. 동기 저장 경로(09)가 사용.

### 3.4 배치 임베딩 보장
- 청크가 수십 개여도 임베딩은 **1회 배치 호출**. 1개씩 호출 금지.

## 완료 기준

### 자동 검증 (테스트)
- [ ] `tests/test_chunking.py` — 헤딩/문단 경계 분할, 장문 2차 분할, 코드블록 보존, 빈 청크 제거.
- [ ] `tests/test_wikilink.py` — `[[A]]`, `[[B|별칭]]`, 코드블록 내부 무시, 중복 제거.
- [ ] `tests/test_indexing.py::test_reindex` — 노트 저장 후 chunks 행수>0, 모든 embedding NOT NULL(길이 1024), edges가 본문 링크와 일치.
- [ ] `tests/test_indexing.py::test_partial` — 노트 본문 수정 후 재인덱싱 시 옛 청크 전부 교체(이전 chunk id 미존재), 다른 노트의 청크는 불변.
- [ ] `tests/test_indexing.py::test_link_resolution` — `[[없는제목]]`은 dst_note NULL → 동명 노트 생성 시 자동 채움; 그 노트 삭제 시(unresolve) 다시 NULL.
- [ ] `tests/test_indexing.py::test_transaction` — reindex 중 예외 시 롤백되어 청크가 절반만 들어가지 않음.

**검증 실행 명령어**: `uv run pytest tests/test_chunking.py tests/test_wikilink.py tests/test_indexing.py -q`

## 참고사항
- 재인덱싱이 단일 트랜잭션이어야 검색(04)이 "청크는 새것, 임베딩은 빈것" 같은 중간 상태를 안 본다.
- `message_citations`는 note_id 기준이라 청크 교체와 무관하게 유지된다(여기선 손대지 않음).
- 동기/비동기 호출 정책(수동 저장=동기, distill 다건=BackgroundTasks)은 09/08에서 이 모듈을 호출하며 결정. 이 태스크는 함수 제공까지.
