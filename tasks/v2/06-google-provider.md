# Task v2-06: Google OAuth provider 추가

## 목표
로그인/연동 provider에 **Google(OIDC)** 을 추가한다. github/kakao와 동일 패턴. Google은 `openid email profile`로 **검증된 이메일을 바로** 주므로(카카오처럼 비즈앱·권한신청 불필요) 두 번째 provider로 적합.

## 선행 조건
- v2-01(멀티신원, `resolve_user`) 완료. `_PROVIDERS`(auth.py)·`_creds`·`_fetch_profile` 패턴 존재. account.py `_PROVIDERS`·mcp `link_account`는 이미 'google' 허용.

## 구현 상세

### 06.1 설정 (`app/config.py`)
```python
GOOGLE_CLIENT_ID: str = ""
GOOGLE_CLIENT_SECRET: str = ""
```

### 06.2 provider 등록 (`app/routes/auth.py`)
```python
_PROVIDERS["google"] = {
    "authorize": "https://accounts.google.com/o/oauth2/v2/auth",
    "token": "https://oauth2.googleapis.com/token",
    "scope": "openid email profile",
}
# _creds: google → (GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET)
```
- authorize 파라미터에 구글 필수값 반영 필요 시(`access_type` 등) 추가. state·PKCE는 기존 흐름.

### 06.3 프로필 조회 (`_fetch_profile`의 google 분기)
```python
# code → token → userinfo
info = (await http.get(
    "https://openidconnect.googleapis.com/v1/userinfo",
    headers={"Authorization": f"Bearer {access}"})).json()
return {"provider": "google", "sub": info["sub"], "email": info.get("email"), "name": info.get("name")}
```
- google의 `sub`는 안정적 고유 ID → identity 키로 그대로.

### 06.4 로그인 화면 (선택)
- `static/login.html`에 "Google로 로그인" 버튼 추가(원하면). 미설정(credential 빈값)이면 `/auth/login/google`이 "미설정 404"로 안전 처리(기존 로직).

## 외부 준비 (사용자)
Google Cloud Console(console.cloud.google.com):
1. 프로젝트 생성/선택 → **OAuth 동의 화면**(External, 스코프 email/profile/openid — 비민감이라 검수 불필요, 테스트 사용자에 본인 추가)
2. **사용자 인증 정보 → OAuth 클라이언트 ID → 웹 애플리케이션**
3. **승인된 리디렉션 URI**: `https://vegapunk.jscraft.work/auth/callback/google`
4. 클라이언트 ID/보안 비밀 → GitHub Secrets `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`

## 완료 기준

### 자동 검증 (google 프로필 모킹)
- [ ] `tests/test_google.py::test_new_google_identity` — 처음 보는 google (sub) → user+identity 생성, email 채워짐.
- [ ] `tests/test_google.py::test_known_google_identity` — 같은 sub 재로그인 → 같은 user.
- [ ] `tests/test_google.py::test_link_google` — 로그인 상태 + link 콜백(google) → 현재 user에 google identity 추가.
- [ ] `tests/test_auth.py` 기존 통과 — provider 추가가 github/kakao를 안 깸.

**검증**: `uv run pytest tests/test_google.py tests/test_auth.py -q`

## 참고사항
- 재배포 필요 + Google 클라이언트/시크릿 등록 후에 실동작.
- Google OAuth 동의 화면이 "테스트" 모드면 테스트 사용자만 로그인 가능 — 개인용은 본인 이메일만 추가하면 충분(게시/검수 불필요).
