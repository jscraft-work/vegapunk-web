# Task v2-07: 공개 엔드포인트 하드닝 (rate limit · 토큰 revoke · link TTL)

## 목표
공개 노출된 OAuth/MCP 엔드포인트의 day-1 위생을 보강한다: **rate limit**, **토큰 revoke**(연결 종료 대응), **link 토큰 TTL 조정**. 인증 흐름 자체는 이미 안전(PKCE·redirect 화이트리스트) — 이건 방어심화·운영 편의.

## 선행 조건
- v2-02(AS)·v2-03(MCP) 완료. `oauth_store`(mcptok/mcprt/mcpclient/authreq), Redis 저장소.

## 구현 상세

### 07.1 rate limit
- `/oauth/authorize`·`/oauth/token`·`/oauth/register`·`/mcp`에 IP(+client_id) 기준 rate limit.
- 구현: 경량(Redis 카운터 또는 slowapi 등). 초과 시 429.
- DCR(`/oauth/register`) 남용 방지가 특히 중요(누구나 등록 가능) → 더 빡빡하게.

### 07.2 토큰 revoke
문제: claude.ai "연결 종료"가 서버 토큰을 안 지움 → mcptok/mcprt가 TTL까지 잔존.
- **RFC 7009 `/oauth/revoke`** 엔드포인트(가능하면) + **서버측 revoke 함수**: `oauth_store.revoke(token)` → mcptok/mcprt 삭제.
- 활성 토큰 목록 조회(`oauth_store.list_tokens(user_id)`) — Admin(v2-08)에서 "커넥터 끊기"에 사용.
- (선택) 특정 user의 전 토큰 일괄 폐기.

### 07.3 link 토큰 TTL
- `session.py` `LINK_TOKEN_TTL` 600s(10분) → **1800s(30분)** 로 상향. (Claude→브라우저 전환 UX 여유. 일회용·pop 소멸은 유지.)

## 완료 기준

### 자동 검증
- [ ] `tests/test_hardening.py::test_rate_limit_429` — 임계 초과 요청이 429.
- [ ] `tests/test_hardening.py::test_revoke_access` — revoke 후 그 토큰으로 `/mcp` → 401.
- [ ] `tests/test_hardening.py::test_revoke_refresh` — revoke된 refresh로 token 교환 실패.
- [ ] `tests/test_hardening.py::test_list_tokens_scoped` — list_tokens가 해당 user 토큰만.
- [ ] `tests/test_hardening.py::test_link_ttl` — LINK_TOKEN_TTL 상향 반영.
- [ ] 기존 `tests/test_oauth_as.py`·`test_mcp_tools.py` 무회귀.

**검증**: `uv run pytest tests/test_hardening.py tests/test_oauth_as.py -q`

## 참고사항
- rate limit 수치는 개인용 규모에 맞게 넉넉히(정상 사용 방해 금지), 남용만 차단.
- revoke 관리 UI는 v2-08 Admin. 이 태스크는 그 **백엔드**(revoke/list) 제공까지.
- 재배포 필요.
