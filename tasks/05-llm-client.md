# Task 05: openclaw LLM 클라이언트

## 목표
openclaw를 **무상태(prompt 문자열 하나 → 답 하나)** 입구로만 쓰는 클라이언트를 만든다. session_id 미사용. 스트리밍 응답과 모델 등급(`low`/기본) 지정을 지원하고, 외부 의존이므로 테스트에서 주입 가능한 인터페이스로 설계한다(기획서 6장).

## 선행 조건
- Task 01 완료. (openclaw 실제 엔드포인트/호출 방식은 호스트 환경에 따름 — 환경변수로 주입.)

## 구현 상세

### 5.1 클라이언트 인터페이스 (`app/llm.py`)
- 추상 인터페이스 `LLMClient`:
  - `async def complete(prompt: str, *, tier: str = "default") -> str` — 비스트리밍(다시쓰기·요약·distill·태그제안용).
  - `async def stream(prompt: str, *, tier: str = "default") -> AsyncIterator[str]` — 토큰/델타 스트리밍(채팅 답변용).
- `tier`: `"low"`(빠름, 다시쓰기/제목/태그제안) / `"default"`(답변/요약/distill 병합). 실제 openclaw 모델 매핑은 config로.
- **prompt는 이미 조립된 한 덩이 문자열** — 이 모듈은 messages 배열을 만들지 않는다. 역할 구분(`사용자:`/`비서:`)은 호출자(06/07)가 텍스트로 넣는다.

### 5.2 openclaw 어댑터 (`OpenclawClient`)
- 호스트 래퍼 호출 방식을 환경변수로 구성: `OPENCLAW_BASE_URL`, `OPENCLAW_API_KEY`(있으면), 모델 등급 매핑(`OPENCLAW_MODEL_LOW`, `OPENCLAW_MODEL_DEFAULT`).
- `httpx.AsyncClient`로 호출. **session_id 절대 전송 안 함**(무상태 원칙). 요청 바디는 `prompt` 문자열 + 모델 등급.
- `stream`은 openclaw의 스트리밍 응답(SSE/chunked)을 델타 문자열로 yield. 타임아웃·재시도(짧게 1회) 처리.
- 실패 시 명확한 예외(`LLMError`) — 07의 SSE `error` 이벤트로 전달됨.

### 5.3 테스트용 Fake (`app/llm.py` 또는 `tests/`)
- `FakeLLMClient(complete_fn, stream_chunks)` — 주입한 함수/청크를 그대로 반환. 의존성 주입으로 앱에 바인딩(`get_llm()` 의존성, 테스트에서 override).

### 5.4 의존성 와이어링
- FastAPI `Depends(get_llm)`로 주입. 기본은 `OpenclawClient`, 테스트 환경(`APP_ENV=test`)은 Fake 또는 override.

## 완료 기준

### 자동 검증 (테스트)
- [ ] `tests/test_llm.py::test_complete` — Fake로 `complete` 반환값 확인, tier 전달 확인.
- [ ] `tests/test_llm.py::test_stream` — `stream`이 주입 청크를 순서대로 yield.
- [ ] `tests/test_llm.py::test_no_session_id` — OpenclawClient가 만든 요청 페이로드에 session_id/대화상태 키가 없음(요청 모킹으로 검증).
- [ ] `tests/test_llm.py::test_error` — openclaw 비정상 응답 시 `LLMError`.

**검증 실행 명령어**: `uv run pytest tests/test_llm.py -q`

## 참고사항
- 실제 openclaw 호출 규약은 호스트마다 다를 수 있으니 **어댑터 한 곳에 격리**하고 나머지 코드는 `LLMClient` 추상에만 의존.
- 06(요약/다시쓰기)·07(답변 스트리밍)·08(distill)·09(태그제안)이 모두 이 클라이언트를 쓴다.
- 비용 0이지만 호출 횟수는 최소화 설계(다시쓰기 1 + 답변 1, 요약은 가끔)를 깨지 않도록 호출자가 관리.
