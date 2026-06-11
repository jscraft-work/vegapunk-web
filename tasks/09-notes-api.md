# Task 09: 노트/지식 API

## 목표
기획서 14.2(C)(D)의 지식 열람·편집·검색·태그·버전 이력 API를 구현한다. 노트 저장 시 동기 인덱싱, 버전 백업, 미해결 링크 해결을 03/08과 일관되게 처리한다.

## 선행 조건
- Task 03(인덱싱), 04(검색) 완료. Task 08의 `/api/ingest`는 공유(편집 저장도 같은 엔드포인트 재사용).

## 구현 상세

### 9.1 목록/검색 (`app/routes/notes.py`)
```
GET /api/pages?tag=   → { pages:[{title,tags,updated}] }     // tag 옵션 필터
GET /api/tags         → { tags:[{tag,count}] }               // note_tags 집계
GET /api/search?q=    → { results:[{note_id,title,snippet,score}] }  // 04 검색 재사용, snippet=매칭 청크 발췌
```

### 9.2 노트 상세 (위키링크·백링크 렌더용)
```
GET /api/page/{title} → { page:{title,body,tags,updated},
                          titles:[존재하는 노트 제목들],   // FE 위키링크 해석/렌더용
                          backlinks:[title…] }            // edges에서 dst_note=이 노트인 src
```
- `backlinks`는 `edges`(dst_note 해석)로 조회. `titles`는 위키링크가 가리키는 제목의 존재 여부를 FE가 칠하기 위함.

### 9.3 편집/태그/삭제
```
POST   /api/ingest { title, body, tags }   → { note_id, title, action }   // 08과 동일 엔드포인트, merge_into 없음=신규/수정, 동기 인덱싱
POST   /api/page/{title}/tags { tags }      → { ok, tags }                 // 태그 교체(정규화)
POST   /api/page/{title}/suggest-tags       → { tags:[...] }               // LLM(low) 태그 제안
DELETE /api/page/{title}                    → { deleted, title }           // 03 unresolve_links_to 호출(가리키던 edges dst_note→NULL), CASCADE로 chunks/edges(src) 정리
```
- 수동 저장은 **동기 인덱싱**(저장 직후 검색 가능). 저장 시 `note_versions`에 이전 본문 백업(`source='manual'`).

### 9.4 버전 이력 / 되돌리기
```
GET  /api/page/{title}/versions          → { versions:[{id,source,created_at}] }
GET  /api/page/{title}/versions/{vid}    → { body }                       // 미리보기/diff용
POST /api/page/{title}/restore { version_id } → { ok, action:"restored" } // 본문 교체 → 재인덱싱, 현재본도 새 버전으로 백업
```

### 9.5 노트 식별 규약
- 경로는 **제목 기준**(제목 유일). 응답엔 `note_id`도 함께 반환(안정 식별, citations 호환). 제목 변경(rename)은 `[[옛 제목]]` 미해결 처리 정책(기획서 13장) 유지.

## 완료 기준

### 자동 검증 (테스트)
- [ ] `tests/test_notes.py::test_pages_and_tags` — 태그 필터 목록, 태그 집계 count 정확.
- [ ] `tests/test_notes.py::test_search_snippet` — 검색 결과에 note_id/title/snippet/score.
- [ ] `tests/test_notes.py::test_page_backlinks` — `[[A]]`를 가진 노트 저장 시 A의 backlinks에 등장; titles에 존재 제목 포함.
- [ ] `tests/test_notes.py::test_ingest_sync_index` — 저장 직후 `/api/search`에 즉시 반영.
- [ ] `tests/test_notes.py::test_tags_replace_and_suggest` — 태그 교체 반영, suggest-tags(FakeLLM) 반환.
- [ ] `tests/test_notes.py::test_delete_unresolves` — 노트 삭제 시 그를 가리키던 edges dst_note=NULL, 청크 CASCADE 삭제.
- [ ] `tests/test_notes.py::test_versions_and_restore` — 편집마다 버전 적재, restore가 본문 교체+재인덱싱+현재본 백업.

**검증 실행 명령어**: `uv run pytest tests/test_notes.py -q`

## 참고사항
- `/api/ingest`는 08과 한 구현을 공유 — 중복 구현하지 말 것(merge_into 유무로 분기).
- `GET /api/backlinks/{title}`는 page 응답에 backlinks가 포함되어 FE엔 불필요(14.3) — 외부 직접링크용으로만 선택적 제공(생략 가능).
- restore도 저장의 일종 → 버전 백업+인덱싱 경로 재사용.
