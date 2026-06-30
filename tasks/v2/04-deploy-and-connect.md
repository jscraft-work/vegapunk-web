# Task v2-04: 배포 & 커넥터 연결 & 교차검증

## 목표
MCP 입구가 포함된 vegapunk를 공개 인터넷에 배포하고, claude.ai에 커스텀 커넥터로 등록한 뒤, **웹앱과 claude.ai가 같은 노트 저장소를 공유**하는지 교차검증한다.

## 선행 조건
- Task v2-01~03 완료(계정 모델·AS·MCP 도구). 기존 배포 파이프라인(`docker-compose`, GitHub Actions, `infra/`).

## 구현 상세

### 04.1 공개 노출
- MCP·OAuth 엔드포인트가 **공개 인터넷에서 도달** 가능해야 함(Anthropic 클라우드가 접속). 사설망·VPN·방화벽 뒤는 불가.
- 기존 호스트(`vegapunk.jscraft.work`)에 같은 프로세스로 노출(`/mcp`, `/oauth/*`, `/.well-known/*`). 별도 서비스 아님.
- 리버스 프록시/WAF가 다음을 막지 않는지 확인: `/.well-known/oauth-protected-resource`, `/.well-known/oauth-authorization-server`, `/oauth/authorize`, `/oauth/token`, `/oauth/register`, `/mcp`.
- `OAUTH_REDIRECT_BASE` 등 환경변수 prod 값 점검. Claude 콜백(`https://claude.ai/api/mcp/auth_callback`) 허용 목록 반영.

### 04.2 커넥터 등록 (1회)
- claude.ai 웹 → Settings → Connectors → **Add custom connector** → MCP 서버 URL(`https://vegapunk.jscraft.work/mcp`) 등록.
- OAuth 동의 흐름(상류 provider 로그인) 완료 → 커넥터 연결됨.
- 등록 후 **폰 앱·Claude Code에 자동 동기화**.
- 제약: 무료 플랜 커스텀 커넥터 1개 제한. 커스텀 커넥터는 현재 **beta**(동작 변경 가능).

### 04.3 교차검증 (수동 시나리오)
- [ ] 웹앱에서 노트 저장 → claude.ai `search_notes`로 검색됨.
- [ ] claude.ai에서 `ingest_note`로 저장 → 웹앱 지식 화면에 뜸(같은 user_id).
- [ ] 폰(claude.ai 앱)에서 저장·검색 동작.
- [ ] Claude Code에서 커넥터 도구 호출(아이디어 검색 → 코드 작업 → 결정 저장 순환).
- [ ] **멀티 신원**: github로 커넥터 연결 → `link_account`로 kakao 연동 → 두 신원 모두 같은 노트.
- [ ] **스코프 격리**: (가능하면) 다른 user의 노트가 안 보이는지 확인.
- [ ] 빈 결과 시 Claude가 "노트 없음"이라 답하고 환각 안 함.

### 04.4 운영 점검
- OAuth/MCP 엔드포인트 rate limit 동작 확인.
- 토큰 만료·refresh 회전이 실사용에서 끊김 없는지.
- 로그에 민감정보(토큰 평문 등) 미노출.

## 완료 기준
- [ ] 04.1 공개 노출 점검 완료(디스커버리 문서가 외부에서 200).
- [ ] 04.2 커넥터 등록 성공(claude.ai에서 도구 목록 보임).
- [ ] 04.3 교차검증 시나리오 전부 통과.
- [ ] 04.4 운영 점검 통과.

**검증**: 주로 수동(실제 claude.ai 연결). 자동화 가능한 부분(디스커버리 200, 토큰 흐름)은 Task 02 테스트로 커버.

## 참고사항
- 디스커버리 호스트도 Anthropic egress에서 도달 가능해야 함(WAF 주의) — 04.1과 동일.
- 비용: openclaw 무료라 웹 입구 LLM 최적화는 지금 안 함. 단 LLM 호출은 이미 `LLMClient.complete(prompt, tier=)` 단일 추상화로 감싸져 있어 향후 API 전환 시 한 곳만 바꾸면 됨.
- 나중 과제: 민감정보 암호화, 복구 코드, rate limiting 정교화, primary_email 변경/신원 해제 UI.
