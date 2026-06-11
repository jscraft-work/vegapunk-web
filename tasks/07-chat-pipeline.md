# Task 07: 채팅 파이프라인 & SSE

## 목표
기획서 6장의 매 턴 파이프라인을 `GET /api/chat` SSE로 구현한다: 질문 → (이전 대화 있으면) 다시쓰기(low) → 검색 → 답변 조립 → openclaw 스트리밍 → 메시지/citations 저장 → 백그라운드 요약. SSE 이벤트 계약(`conversation`/`sources`/`answer`/`done`/`error`)을 지킨다. 대화 CRUD도 포함.

## 선행 조건
- Task 04(검색), 05(LLM), 06(기억) 완료.

## 구현 상세

### 7.1 채팅 SSE (`app/routes/chat.py`)
- `GET /api/chat?q=&conv=` (`conv=0`→새 대화), `sse-starlette`의 `EventSourceResponse`. FE는 `q`/`conv`만 보냄. 단계:
  1. `conv=0`이면 conversation 생성 → `conversation {id, title}` 이벤트(title은 임시/빈값, 자동제목은 별도).
  2. 이전 턴 있으면 `memory.build_rewrite_input` → `llm.complete(tier="low")`로 **검색쿼리 한 줄** 생성. 첫 질문이면 원문 사용.
  3. `search.search(쿼리)` → 상위 청크. → `sources [{note_id, title, score}]` 이벤트(**답변 전에 먼저**).
  4. `memory.build_answer_context` + [지시] + [참고자료=이번 RAG만] + [질문=원문] 을 **한 덩이 문자열**로 조립(기획서 6장 형식). 옛 RAG는 안 넣음.
  5. user 메시지 저장 → `llm.stream(prompt, tier="default")` → `answer {text}`(델타 분할 가능) 스트리밍.
  6. 스트림 종료 후 assistant 메시지 저장 + `message_citations`(note_id 기준) 저장. `sent_prompt`에 실제 조립 문자열 저장(디버그).
  7. `done {}` 이벤트. 그 뒤 **BackgroundTask로 `memory.maybe_update_summary`**(응답 비차단).
- 예외 시 `error {message}` 이벤트 후 종료. 스트림 도중 끊김도 가드(부분 답변까지 저장).

### 7.2 SSE 이벤트 계약 (정확히 준수)
```
conversation { id, title }
sources      [{ note_id, title, score }]
answer       { text }      // 델타 가능
done         {}
error        { message }
```
- `suggest {hint}`는 **이번 태스크 범위 밖**(트리거 미정의 — 기획서 빈틈). 이벤트 타입만 예약하고 발행하지 않음(주석으로 표기).

### 7.3 대화 CRUD
```
GET    /api/conversations            → { conversations:[{id,title,updated}] }
GET    /api/conversations/{id}       → { id,title, messages:[{role,content,sources}] }
PATCH  /api/conversations/{id}       { title } → { ok, title }
POST   /api/conversations/{id}/retitle → { title }   // LLM(low)로 대화 제목 자동생성
DELETE /api/conversations/{id}       → { deleted, id }
```
- 상세 조회의 `messages[].sources`는 `message_citations`를 note_id→title 조인해 구성.

## 완료 기준

### 자동 검증 (테스트, FakeLLM + 시드 노트)
- [ ] `tests/test_chat.py::test_sse_event_order` — 이벤트가 `conversation?`→`sources`→`answer`(1+)→`done` 순.
- [ ] `tests/test_chat.py::test_first_question_no_rewrite` — 첫 질문은 다시쓰기 LLM 호출 0회, 원문으로 검색.
- [ ] `tests/test_chat.py::test_followup_rewrite` — 후속 질문에서 다시쓰기 1회 발생, 검색쿼리가 원문과 다름.
- [ ] `tests/test_chat.py::test_prompt_assembly` — 답변 prompt에 [지시]+[요약?]+[최근턴]+[참고자료]+[원문질문] 포함, **옛 RAG 미포함**.
- [ ] `tests/test_chat.py::test_citations_note_id` — 답변 후 message_citations가 note_id 기준 저장.
- [ ] `tests/test_chat.py::test_crud` — 대화 목록/상세/이름변경/삭제 동작.
- [ ] `tests/test_chat.py::test_error_event` — LLM 실패 시 `error` 이벤트.

**검증 실행 명령어**: `uv run pytest tests/test_chat.py -q`

## 참고사항
- 다시쓴 쿼리는 **검색용일 뿐** — [질문]에는 반드시 원문을 넣는다(기획서 6장).
- 요약은 절대 답변을 막지 않는다(BackgroundTask).
- FE는 다시쓰기·검색·기억을 모르고 `q`만 보낸다(14.3) — 모든 로직은 서버.
