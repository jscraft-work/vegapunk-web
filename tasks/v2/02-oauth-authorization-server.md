# Task v2-02: OAuth 2.1 인가서버 (AS)

## 목표
vegapunk를 **OAuth 2.1 인가서버**로 노출해, claude.ai 커스텀 커넥터가 OAuth로 연결하고 vegapunk가 발급한 **자기 토큰**을 들고 `/mcp`를 호출하게 한다. 기존 kakao/github/google OAuth는 **상류 인증**(사람 식별)으로 재사용하고, AS는 그 결과를 Claude가 쓰는 토큰으로 번역한다.

## 왜 필요한가 (한 줄)
커넥터는 OAuth 서버 하나만 가리키고, "여러 신원=동일인"을 아는 건 vegapunk뿐 → Claude에게 줄 access token은 vegapunk가 찍어야 하고, 그래야 토큰→`user_id` 매핑이 가능하다. (정적 토큰을 커넥터가 지원하면 생략 가능하나, 미지원 전제.)

## 선행 조건
- Task v2-01 완료(`user_id` 식별 확정). 기존 Redis(`app.state.session_store`) 사용 가능.

## 구현 상세

### 02.1 라이브러리 — authlib 프로바이더 (직접 구현 금지)
- OAuth 2.1 + PKCE + 토큰 발급은 보안상 손으로 짜지 않는다. **`authlib`의 AuthorizationServer**(`authlib.oauth2`, `authlib.integrations.starlette_client`는 기존 client용)로 구현.
- 외부 AS(Hydra/Keycloak)는 별도 프로세스·운영부담이라 이 규모엔 과함 → **in-process** authlib AS를 FastAPI에 마운트.

### 02.2 저장소 (Redis 재사용)
- **authorization code**: 1회용, 짧은 만료(~60s). `code:<v>` → {client_id, user_id, redirect_uri, code_challenge, scope}.
- **access token**: `mcptok:<v>` → {user_id, client_id, scope}, TTL(예: 1h).
- **refresh token**: `mcprt:<v>` → {user_id, client_id}, 긴 TTL(예: 30d), 회전(rotate).
- **registered clients(DCR)**: `mcpclient:<id>` → {redirect_uris, ...}. (또는 작은 DB 테이블 `oauth_clients`.)
- `/mcp` 요청 시 Bearer 토큰 → Redis 조회 → `user_id` 복원(오프라인, 빠름).

### 02.3 엔드포인트 (MCP/OAuth 명세 요건)
```
GET  /.well-known/oauth-protected-resource      # resource → AS 위치 안내
GET  /.well-known/oauth-authorization-server    # RFC 8414 메타데이터 (또는 OIDC discovery)
POST /oauth/register                            # DCR (Dynamic Client Registration)
GET  /oauth/authorize                           # ← 여기서 상류 OAuth(kakao/github/google)로 보냄
GET  /auth/callback/{provider}                  # (기존) 상류 콜백 → user_id 확정 → authorize 재개 → code 발급
POST /oauth/token                               # code/refresh → access token (form-urlencoded)
```
요건:
- **401 응답에 `WWW-Authenticate`** 헤더로 인증서버 위치 안내(보호 리소스 표준).
- **PKCE S256 필수**, 메타데이터에 `code_challenge_methods_supported: ["S256"]`.
- `/oauth/token`은 `application/x-www-form-urlencoded` 수용.
- **콜백 허용 URL**: `https://claude.ai/api/mcp/auth_callback` (+ 향후 `https://claude.com/...`). Claude Code는 **loopback**(`http://localhost`/`127.0.0.1`, 포트 가변) 허용.
- **클라이언트 등록**: 단독 사용은 DCR 또는 CIMD. (DCR은 연결마다 클라이언트 생성 → 트래픽 늘면 CIMD 권장.)

### 02.4 authorize 흐름 (상류 인증 끼워넣기)
```
Claude → GET /oauth/authorize(client_id, redirect_uri, code_challenge, state, scope)
  서버: 로그인 세션 없으면 → 상류 OAuth(provider 선택 화면 또는 기본) 로 리다이렉트
  상류 콜백 → resolve_user(profile) → user_id (Task 01)
  서버: authorize 재개 → authorization code 발급 → redirect_uri(claude 콜백)로 리다이렉트(code, state)
Claude → POST /oauth/token(code, code_verifier) → access token(+refresh)
```
- "provider 선택 화면"이 vegapunk `/oauth/authorize`의 일부 = vegapunk가 AS여야 하는 이유.
- state·PKCE 검증 철저. code는 1회용·즉시 폐기.

### 02.5 day-1 위생 (공개 노출 최소 방어 — v1 포함)
- `/oauth/token`·`/oauth/register`·`/oauth/authorize`에 **rate limit**(IP/클라이언트 기준).
- DCR 남용 방지: 등록 클라이언트 수/속도 제한, 또는 CIMD 우선.
- 디스커버리 호스트가 **Anthropic egress에서 도달** 가능해야 함(WAF/allowlist 주의).

## 완료 기준

### 자동 검증 (테스트, 상류 provider 모킹)
- [ ] `tests/test_oauth_as.py::test_discovery_documents` — 두 `.well-known` 문서가 필수 필드(issuer, authorization_endpoint, token_endpoint, code_challenge_methods_supported=["S256"]) 포함.
- [ ] `tests/test_oauth_as.py::test_dcr_register` — `/oauth/register`로 클라이언트 등록 → client_id 발급.
- [ ] `tests/test_oauth_as.py::test_authcode_pkce_flow` — authorize(상류 모킹)→code→token(code_verifier) 전 과정 → access token 발급, user_id 매핑.
- [ ] `tests/test_oauth_as.py::test_pkce_mismatch_rejected` — 잘못된 code_verifier → 토큰 거부.
- [ ] `tests/test_oauth_as.py::test_token_resolves_user` — 발급 토큰으로 보호 리소스 접근 시 올바른 user_id.
- [ ] `tests/test_oauth_as.py::test_unauth_returns_www_authenticate` — 토큰 없이 `/mcp` → 401 + WWW-Authenticate.
- [ ] `tests/test_oauth_as.py::test_refresh_rotation` — refresh로 새 access token + refresh 회전.

**검증 실행 명령어**: `uv run pytest tests/test_oauth_as.py -q`

## 참고사항
- 기존 `/auth/login/*`·`/auth/callback/*`(웹앱 세션용)은 유지. AS의 authorize는 그 상류 흐름을 **재사용**하되, 끝에서 세션 대신(또는 세션과 함께) **authorization code**를 발급하는 분기를 추가.
- 토큰은 불투명(opaque)+Redis 조회 방식 권장(JWT보다 폐기·회전이 쉬움). 기존 세션 토큰 방식과 일관.
- 이 태스크가 v2에서 가장 큰 작업·보안 핵심. 라이브러리에 최대한 위임할 것.
