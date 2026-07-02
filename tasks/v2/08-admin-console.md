# Task v2-08: Admin 콘솔 (계정·토큰·지식 관리) + 웹앱 재편

## 목표
웹앱을 **"Admin 우선 + Chat 보조"** 로 재편한다. MCP(claude.ai)가 일상 대화·저장·검색의 주 무대가 됐으니, 웹앱의 새 주력은 **데이터 오너의 소유·관리·점검** — claude.ai가 못 보여주는 것(연결된 신원, 커넥터/토큰, 노트 구조)을 눈으로 관리한다.

## 배경 (방향)
[[webapp-direction]]: claude.ai=일상 대화, 웹 Admin=관리·점검(주력), 웹 Chat=메모+distill 공부용(보조·유지).

## 선행 조건
- v2-01(identities)·v2-07(토큰 revoke/list) 백엔드. 기존 notes/tags/manage API(v1) 재사용.

## 구현 상세

### 08.1 계정/신원 관리 (백엔드 + UI)
- `GET /api/account/identities` → 연결된 신원 목록 `[{provider, sub, email, created_at}]`
- `POST /api/account/link/start`(존재) → 붙이기 시작(브라우저 OAuth)
- `DELETE /api/account/identities/{id}` → **신원 해제(unlink)**
  - ⚠️ **마지막 신원 해제 금지**(계정 잠김 방지) → 최소 1개 유지 가드.
- `POST /api/account/merge` → 계정 병합(`merge_users`, 툼스톤). 신중한 확인 UX.
- UI: "연결된 계정" 목록 + 붙이기/떼기/병합 버튼.

### 08.2 커넥터/토큰 관리
- `GET /api/account/tokens` → 활성 MCP 커넥터/토큰 목록(client, 발급시각 등; v2-07 list_tokens).
- `POST /api/account/tokens/revoke` → 선택 토큰 revoke(v2-07). "이 커넥터 끊기".

### 08.3 지식 관리 (기존 재사용 정리)
- 기존 `/api/pages`·`/api/tags`·`/api/manage`·`/api/search`·`/api/page/{title}`(CRUD)를 **Admin 지식 뷰**로 묶음: 노트 브라우징·편집·삭제, 태그, 백링크·고아노트.
- 런타임 설정(`/api/settings`, 검색 임계값)도 Admin에.

### 08.4 웹앱 네비 재편
- SPA를 **Admin 우선** 구조로: [계정] [지식] [설정] + [채팅(보조)].
- Chat은 기존 그대로 유지, 네비에서 부차적 위치로.

## 완료 기준

### 자동 검증
- [ ] `tests/test_admin.py::test_list_identities_scoped` — 본인 신원만 반환.
- [ ] `tests/test_admin.py::test_unlink_identity` — 신원 해제 동작.
- [ ] `tests/test_admin.py::test_unlink_last_blocked` — 마지막 신원 해제 거부.
- [ ] `tests/test_admin.py::test_list_tokens_scoped` — 본인 토큰만.
- [ ] `tests/test_admin.py::test_revoke_token` — revoke 후 그 토큰 `/mcp` 401.
- [ ] `tests/test_admin.py::test_merge_ui_endpoint` — merge 엔드포인트가 데이터 이전+툼스톤.

**검증**: `uv run pytest tests/test_admin.py -q`

## 참고사항
- Admin은 **본인 계정 self-service**(크로스유저 슈퍼어드민 아님). 전부 `require_user` + user_id 스코프.
- 프론트는 기존 바닐라 JS SPA 확장(빌드 없음).
- Chat은 손대지 않음 — 자리만 내림.
- 이 태스크가 v2의 마무리: 오늘까지 쌓인 관리 기능(신원·토큰·링크)에 "눈과 손"을 붙인다.
