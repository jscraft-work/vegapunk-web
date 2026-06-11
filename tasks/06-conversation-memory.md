# Task 06: 대화 기억 / 압축

## 목표
앱이 대화 기억을 소유한다(기획서 7장). 증분 요약(전체 재요약 금지), 배칭(매 턴 안 함), 불변식("버리기 전에 먼저 요약에 접는다")을 구현하고, 답변용 컨텍스트 조립과 다시쓰기 입력 조립을 제공한다. openclaw는 무상태이므로 매 턴 이 모듈이 컨텍스트를 만든다.

## 선행 조건
- Task 01(conversations/messages 스키마), Task 05(LLM 클라이언트) 완료.

## 구현 상세

### 6.1 턴/메시지 로딩 (`app/memory.py`)
- "턴" = user+assistant 1쌍. 헬퍼로 conv의 메시지를 시간순 로딩, `summary_upto_msg_id` 이후의 원문 턴만 "최근 창"으로 취급.
- 파라미터(상수/config, 기획서 10장): `RECENT_TURNS=6`, `SUMMARY_TRIGGER_TURNS=12`, `FOLD_TO_TURNS=6`.

### 6.2 증분 요약 (`maybe_update_summary`)
- `async def maybe_update_summary(pool, llm, conv_id) -> None`:
  - 미요약 원문 턴 수가 `SUMMARY_TRIGGER_TURNS` 초과면 트리거. 아니면 no-op.
  - **밀려날 가장 오래된 턴들만**(최근 `FOLD_TO_TURNS`만 원문으로 남기도록 계산) 골라, 입력 = `기존 summary + 그 턴들 원문` → LLM(`tier="default"`)으로 **갱신된 summary** 생성.
  - `conversations.summary` 갱신 + `summary_upto_msg_id`를 접힌 마지막 메시지 id로 전진. **전체 대화를 통째로 보내지 않는다.**
- **불변식 보장**: summary_upto 전진은 새 summary 저장과 같은 트랜잭션. 즉 원문 창에서 빠지기 전에 반드시 요약에 포함.

### 6.3 답변용 컨텍스트 조립 (`build_answer_context`)
- `build_answer_context(conv) -> {summary, recent_turns}`:
  - `summary`(있으면) + `최근 N턴 원문`(`사용자:`/`비서:` 표기). 07이 여기에 [지시]·[참고자료(RAG)]·[질문]을 더해 최종 prompt 조립.

### 6.4 다시쓰기 입력 조립 (`build_rewrite_input`)
- `build_rewrite_input(conv, question) -> str` — 기획서: `요약 + 직전 1~2턴 + 질문` (작게). 첫 질문이면 None 반환(07이 다시쓰기 생략).

### 6.5 백그라운드 훅
- `maybe_update_summary`는 답변을 막지 않도록 **호출자(07)가 응답 후 BackgroundTask로** 실행. 이 모듈은 동기적으로 안전하게(중복 실행 가드: 이미 접힌 범위 재접기 방지) 동작.

## 완료 기준

### 자동 검증 (테스트, FakeLLM 사용)
- [ ] `tests/test_memory.py::test_no_trigger_below_threshold` — 12턴 이하에서는 요약 호출 0회, summary NULL 유지.
- [ ] `tests/test_memory.py::test_trigger_and_fold` — 12턴 초과 시 1회 요약, 이후 원문 창이 `FOLD_TO_TURNS`로 줄고 `summary_upto_msg_id` 전진.
- [ ] `tests/test_memory.py::test_incremental_input` — 요약 LLM에 들어간 입력에 **전체 대화가 아니라** (기존 summary + 새로 밀려난 턴들)만 포함됨(FakeLLM이 받은 prompt 검사).
- [ ] `tests/test_memory.py::test_invariant` — 임의 시점에 모든 과거 턴이 summary 범위 또는 최근 창 중 하나에 반드시 존재(구멍 없음).
- [ ] `tests/test_memory.py::test_rewrite_input` — 첫 질문이면 None, 이후엔 요약+직전 1~2턴+질문 포함.

**검증 실행 명령어**: `uv run pytest tests/test_memory.py -q`

## 참고사항
- 요약은 *다음* 턴용이라 늦어도 됨 → 절대 답변 경로를 블로킹하지 말 것.
- 깊은 참조 손실은 수용된 트레이드오프(기획서 7장) — 이 태스크에서 해결 대상 아님.
- 07이 이 모듈의 `build_answer_context`/`build_rewrite_input`/`maybe_update_summary`를 파이프라인에 끼운다.
