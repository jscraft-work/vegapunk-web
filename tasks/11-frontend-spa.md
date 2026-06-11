# Task 11: 프론트엔드 SPA

## 목표
기획서 14.1 와이어프레임을 빌드 없는 바닐라 JS SPA로 구현한다: 2-스페이스(채팅·지식) + GNB + 사이드바, 채팅 SSE 소비(출처 칩 먼저→스트리밍 답변), distill 검토 모달, 노트 열람(위키링크/백링크), 버전 이력 모달, 로그인 랜딩, 모바일 오프캔버스. FastAPI가 정적 서빙.

## 선행 조건
- Task 07~10 완료(모든 FE API + 인증). 14.2의 엔드포인트 계약을 따른다.

## 구현 상세

### 11.1 정적 서빙 & 셸 (`static/`)
- `app/main.py`에 `StaticFiles` 마운트(예: `/` → `static/index.html`, `/static/*`). 인증 안 된 접근은 `/login`으로.
- `index.html` + `app.js`(라우팅: 해시 기반 `#/chat`, `#/note/{title}`) + `style.css`. 프레임워크 없음, fetch + EventSource만.
- GNB(💬 채팅 / 📄 지식 탭, 우측 유저=`/auth/me`), 좌측 사이드바, 모바일은 사이드바 오프캔버스 드로어(CSS + 토글).

### 11.2 채팅 화면 (A)
- 사이드바: `GET /api/conversations`. "+ 새 대화"는 `conv=0`.
- 입력 전송 → `EventSource('/api/chat?q=&conv=')`:
  - `conversation` → 신규면 사이드바/URL 갱신.
  - `sources` → **답변 위에 출처 칩 먼저** 렌더(클릭 시 `#/note/{title}` 이동). 검색 중 표시 `🔍 검색 중…`.
  - `answer`(델타) → 말풍선 스트리밍 누적.
  - `done` → 종료. `error` → 사용자 노출 + 부분답변 유지.
- 대화 이름변경(PATCH)·자동제목(retitle)·삭제(DELETE) 연결.

### 11.3 distill 검토 모달 (B)
- "💾 지식으로 저장" → `POST /api/distill {conv_id}` → 후보 카드들.
- 각 후보: 제목/태그 편집, merge_target 있으면 "⚠ 기존 「X」와 NN% 유사" + 라디오 `[기존 병합][새 노트][다른 노트…][버림]`.
- "기존 병합" 선택 시에만 `POST /api/notes/merge-preview` 호출(lazy, 1건씩) → diff 표시 + 직접 수정.
- "모두 저장" → 후보별 `POST /api/ingest`(merge_into 채워). 저장 결과 지식 화면 반영.

### 11.4 지식 화면 (C) + 버전 모달 (D)
- 사이드바 검색(`/api/search`)·태그 필터(`/api/tags`,`/api/pages?tag=`).
- 노트: `GET /api/page/{title}` → 본문 마크다운 렌더, `[[링크]]`를 `titles`로 존재/미존재 칠해 클릭 이동, 백링크 표시. 편집/태그제안(suggest-tags)/삭제.
- 🕘 이력: `versions` 목록 + 버전 미리보기/diff + `restore`.

### 11.5 로그인 랜딩 (E)
- `/login`: 🧠 vegapunk, [카카오로 로그인]→`/auth/login/kakao`, [GitHub로 로그인]→`/auth/login/github`.

## 완료 기준

### 자동 검증 (가능 범위)
- [ ] `tests/test_static.py` — `/` 가 index.html 서빙(인증 시), `/login` 접근 가능, `/static/app.js` 200.
- [ ] (선택) `app.js`의 순수 함수(마크다운/위키링크 렌더, SSE 이벤트 파서)를 분리해 `tests/test_frontend_units.*`로 단위 검증.

### 수동 검증 (UI — 체크리스트)
- [ ] 로그인→채팅 진입, 질문 시 출처 칩이 답변보다 먼저 뜨고 답변이 스트리밍됨.
- [ ] 출처/`[[링크]]` 클릭으로 지식 화면 이동, 백링크 표시.
- [ ] "지식으로 저장" 모달에서 후보 검토·병합 diff·저장이 동작, 지식에 반영.
- [ ] 버전 이력에서 되돌리기 동작.
- [ ] 모바일 폭에서 사이드바 드로어 토글.

**검증 실행 명령어**: `uv run pytest tests/test_static.py -q` + 수동 체크리스트(`uv run uvicorn app.main:app` 후 브라우저).

## 참고사항
- FE는 다시쓰기·검색·요약·병합매칭·인덱싱을 **호출하지 않는다**(14.3) — 서버 내부 처리.
- 마크다운 렌더는 경량 라이브러리(예: marked) CDN 또는 최소 자체 렌더. 빌드 스텝은 두지 않음.
- `suggest` SSE 이벤트는 서버 미발행(07) → FE도 처리 안 함(예약만).
