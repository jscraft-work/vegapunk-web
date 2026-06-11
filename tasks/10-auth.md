# Task 10: 인증 (카카오 + GitHub OAuth)

## 목표
기획서 14.2의 인증을 구현한다: 카카오·GitHub OAuth 로그인, Redis 기반 세션, `/auth/me`, 로그아웃, 그리고 `/api/*` 라우트 보호. 단일 사용자 전제이나 사용자 레코드는 `users`에 저장한다.

## 선행 조건
- Task 01(users 스키마, REDIS_URL 설정 자리) 완료. 나머지 API(07~09)는 보호 대상이므로 그 이후 권장.

## 구현 상세

### 10.1 세션 저장소 (`app/session.py`)
- `redis` 비동기 클라이언트(앱 lifespan에서 생성). 세션 = 서버 생성 토큰(`secrets.token_urlsafe`) → Redis에 `{user_id, ...}` 저장(TTL). 브라우저엔 **HttpOnly·SameSite=Lax 쿠키**로 토큰만 전달(서명 또는 불투명 토큰).
- `create_session(user_id)`, `get_session(token)`, `destroy_session(token)`.

### 10.2 OAuth (`app/routes/auth.py`, authlib)
```
GET /login                 로그인 랜딩(카카오/GitHub 버튼) — 정적 또는 간단 템플릿
GET /auth/login/github     → GitHub authorize 리다이렉트
GET /auth/callback/github  GitHub 콜백 → 프로필(email,name) → users upsert → 세션 생성 → / 리다이렉트
GET /auth/login/kakao      → 카카오 authorize 리다이렉트
GET /auth/callback/kakao   카카오 콜백 → 프로필 → users upsert → 세션 생성 → / 리다이렉트
GET /auth/me               → { user: {id,email,name} | null }
GET /auth/logout           세션 파기 → / 리다이렉트
```
- authlib `OAuth` 레지스트리에 두 프로바이더 등록. client id/secret/redirect는 환경변수(`GITHUB_CLIENT_ID/SECRET`, `KAKAO_REST_API_KEY/SECRET`, `OAUTH_REDIRECT_BASE`).
- 카카오: 이메일 동의 항목 미동의 시 email NULL 가능 → `users.email`은 유니크 필수이므로 폴백 키(`kakao:{id}` 형태 placeholder email) 처리하고 name은 닉네임 사용.
- CSRF: authlib state 파라미터 사용.

### 10.3 라우트 보호 (`app/deps.py`)
- `Depends(require_user)` — 쿠키 토큰→세션 조회, 없으면 401. `/api/*`(chat·distill·notes 등)에 적용. `/health`·`/login`·`/auth/*`·정적파일은 공개.
- `current_user` 의존성으로 핸들러에서 user 접근(단일 사용자라 소유권 필터는 생략, 향후 user_id 도입 자리만 표시).

## 완료 기준

### 자동 검증 (테스트, OAuth 프로바이더는 모킹)
- [ ] `tests/test_auth.py::test_callback_creates_user_and_session` — 모킹한 GitHub/카카오 프로필로 콜백 → users upsert + 세션 쿠키 발급.
- [ ] `tests/test_auth.py::test_me` — 세션 있으면 user 반환, 없으면 `{user:null}`.
- [ ] `tests/test_auth.py::test_logout` — 로그아웃 후 세션 무효(Redis에서 제거), `/auth/me` null.
- [ ] `tests/test_auth.py::test_protected_routes` — 미인증 `/api/chat` 등 401, 인증 후 통과.
- [ ] `tests/test_auth.py::test_kakao_no_email_fallback` — 카카오 email 미제공 시 placeholder로 upsert 성공.

**검증 실행 명령어**: `uv run pytest tests/test_auth.py -q`

## 참고사항
- bj-auth는 제외(카카오+GitHub만).
- Redis는 기존 운영 인프라 재사용(추가 비용 0).
- 보호 적용 후 07~09 테스트가 401로 깨질 수 있음 → 테스트는 인증 우회 fixture(테스트용 세션 주입) 제공.
- 콜백은 OAuth 공급자가 호출하는 브라우저 리다이렉트(FE fetch 아님, 14.3).
