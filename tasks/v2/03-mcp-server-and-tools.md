# Task v2-03: MCP 서버 마운트 & 도구

## 목표
공식 MCP SDK로 원격 MCP 서버를 **기존 FastAPI 프로세스에 `/mcp`로 마운트**하고, 기존 서비스 함수를 호출하는 **얇은 도구**들을 노출한다. 서버측 LLM 0회. 모든 도구는 Task 02가 복원한 `user_id`로 스코프.

## 선행 조건
- Task v2-01(user_id) + v2-02(토큰→user_id 매핑). 기존 `search`/`distill_match`/`ingest`/`notes` 서비스 함수.

## 구현 상세

### 03.1 마운트 (같은 프로세스)
- 공식 `mcp` SDK로 서버 인스턴스 생성, ASGI 앱으로 변환해 `app.mount("/mcp", mcp_asgi)`(`app/main.py`의 `create_app`).
- DB 풀(`app.state.pool`)·fastembed 싱글톤·서비스 함수를 **그대로 공유**(별도 프로세스 금지).
- 각 도구 호출 컨텍스트에서 Bearer 토큰 → `user_id` 복원(02.2). 누락/무효 토큰 → 인증 오류.

### 03.2 검색 게이트 플래그 (`app/search.py` 소폭 리팩터)
- 현재 `search(conn, query, user_id)`는 관련성 게이트 적용 + TOP_K=5로 잘라 반환(웹 채팅용).
- MCP는 "후보를 그대로" 원하므로 파라미터화:
```python
async def search(conn, query, user_id, *, apply_gate=True, top_k=TOP_K):
    ...
    # apply_gate=False면 _relevant 필터 생략, top_k까지 반환(상한 30 권장)
```
- 웹 입구: `apply_gate=True`(임계 0.18 유지). MCP 입구: `apply_gate=False`, `top_k` 확대(예 15~30, 상한 둠).

### 03.3 도구 세트 (전부 LLM 없는 래퍼)
| 도구 | 내부 재사용 | 동작 |
|------|------------|------|
| `search_notes(query, topic?, limit?)` | `search.search(apply_gate=False, top_k=limit)` | 글자+벡터+RRF+그래프 → 노트/청크 **원문** 반환. 게이트·다시쓰기·답변 없음 |
| `find_merge_target(title, body)` | `distill_match.find_merge_target` | 병합 대상 노트만 찾음(LLM 없음) |
| `ingest_note(title, body, tags?, merge_into?)` | `ingest.ingest_note` | 저장 + 인덱싱 |
| `get_note(title)` / `list_notes(tag?)` | notes 라우트 로직 | 열람 |
| `update_note(title, body?, tags?)` / `delete_note(title)` | 기존 CRUD | 수정·삭제 |
| `link_account()` | `/api/account/link/start` (Task 01) | 연동 시작용 일회용 링크 URL 반환 |

- **저장 흐름**: Claude가 노트 후보·병합 글쓰기를 **직접** 한 뒤 → `find_merge_target`으로 대상 확인 → 사용자 확인 → `ingest_note`. 서버측 openclaw 0회. `save_or_distill` 같은 통합 도구는 **만들지 않는다**(역할 겹침·서버 LLM 유발).
- **도구 설명(description)** 에 가드레일 명시: "검색 결과가 비면 추측하지 말고 '해당 노트 없음'이라고 답하라", "비번·계좌번호·API 키 등 접근수단은 저장하지 말라".

### 03.3.1 반환 형식
- `search_notes`: `[{note_id, title, snippet/body, score}]` — 원문(body 또는 충분한 청크 텍스트) 포함(Claude가 ANSWER에서 읽음). 빈 결과는 `[]`로 명확히.
- `ingest_note`: `{note_id, title, action}` (기존 `ingest_note` 반환 그대로).
- `find_merge_target`: `{note_id, title, similarity} | null`.

### 03.4 로컬 테스트 (배포 전)
- Claude Desktop 또는 로컬 MCP 클라이언트로 각 도구 호출 확인. (정식 교차검증은 Task 04.)

## 완료 기준

### 자동 검증 (테스트, 토큰→user_id는 fixture 주입)
- [ ] `tests/test_mcp_tools.py::test_search_notes_scoped` — 다른 user 노트가 결과에 안 섞임(user_id 스코프).
- [ ] `tests/test_mcp_tools.py::test_search_no_gate_returns_more` — `apply_gate=False`가 게이트 적용보다 후보를 더(또는 같게) 반환, 상한 준수.
- [ ] `tests/test_mcp_tools.py::test_search_empty_returns_empty` — 무관 쿼리 → `[]`(환각 유도 텍스트 없음).
- [ ] `tests/test_mcp_tools.py::test_ingest_then_search` — `ingest_note` 후 동기 인덱싱 → `search_notes`로 즉시 검색됨.
- [ ] `tests/test_mcp_tools.py::test_find_merge_target` — 유사 노트 있으면 대상 반환, 없으면 null.
- [ ] `tests/test_mcp_tools.py::test_get_list_update_delete` — CRUD 도구가 user_id 스코프로 동작.
- [ ] `tests/test_mcp_tools.py::test_tool_requires_auth` — 토큰 없는 도구 호출 거부.
- [ ] `tests/test_search.py` 기존 통과 — 게이트 플래그 추가가 웹 검색(`apply_gate=True`)을 깨지 않음.

**검증 실행 명령어**: `uv run pytest tests/test_mcp_tools.py tests/test_search.py -q`

## 참고사항
- 도구는 기존 서비스 함수 **호출만**. 검색/병합/인덱싱 로직 재작성 금지.
- distill 후보생성·merge_preview(openclaw 호출)는 MCP에서 **쓰지 않는다** → 추출 부담 없음.
- `search_notes`가 청크 원문을 돌려줄지 노트 body 전체를 돌려줄지는 토큰량 보고 조정(기본: 매칭 청크 텍스트 + note_id로 필요 시 `get_note`).
