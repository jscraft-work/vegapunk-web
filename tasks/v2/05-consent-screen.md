# Task v2-05: 연결 동의 화면 (authorize consent)

## 목표
MCP 커넥터 연결 시, `/oauth/authorize`가 **코드를 발급하기 직전에 항상 동의 화면**을 띄운다. "지금 이 커넥터를 **어느 vegapunk 계정**에 연결하는지"를 사용자에게 명확히 보여주고 승인을 받는다 → 묵시적 바인딩 제거 + CSRF 방어심화.

## 배경 (왜 전 경로에 항상 표시하나)
- 현재 `authorize`(oauth.py:176-180)는 **세션이 있으면 확인 없이 즉시 코드 발급**. 사용자가 어느 계정에 붙는지 모른 채 연결됨.
- **케이스 1(상류 로그인)과 3(상류 세션으로 조용히 통과)은 서버에서 구분 불가**(github OAuth는 OIDC 아님 → 신선도 신호 없음). 그래서 "3만 동의, 1은 생략"은 불가능 → **코드 발급 지점에 항상** 동의를 건다(1·2·3·4 전부 표시, 무해).

## 선행 조건
- v2-02(OAuth AS) 완료. `oauth_store.save_authreq`/`get_authreq`, `_issue_and_redirect` 재사용.

## 구현 상세

### 05.1 authorize: 즉시 발급 → 동의 화면
`oauth.py`의 세션 분기(발급 직전)를 교체:
```python
# 3) 세션 있음 → 바로 발급하지 말고 동의 화면으로.
sess = ...
if sess is not None:
    authreq_id = await oauth_store.save_authreq(store, {**pending, "user_id": sess["user_id"]})
    return _consent_page(request, authreq_id, sess["user_id"])   # HTML
# 4) 세션 없음 → 상류 OAuth (기존). 상류 콜백 재개도 발급 전에 동의 화면 경유.
```
- 상류 재개 경로(`app/routes/auth.py`의 authreq 재개)도 code 발급 **직전**에 동의 화면을 거치도록 동일 처리.

### 05.2 동의 화면 (작은 HTML)
```
Claude를 vegapunk 계정에 연결합니다.
  계정: {email 또는 닉네임}
  노트: {N}개
  [ 연결 승인 ]   [ 다른 계정으로 ]
```
- 대상 user 정보 조회(email/닉, notes count)해서 표시.

### 05.3 승인/전환 핸들러
```
POST /oauth/consent { authreq_id, action=approve }
  → get_authreq → _issue_and_redirect(store, user_id, pending)   # code 발급 → claude redirect
POST /oauth/consent { authreq_id, action=switch }
  → 세션 로그아웃 + 상류 재로그인(authreq 유지)로 리다이렉트
```
- CSRF: authreq_id는 서버 저장(추측 불가), 승인 POST는 해당 세션/authreq 검증.

## 완료 기준

### 자동 검증
- [ ] `tests/test_consent.py::test_session_path_shows_consent` — 세션 있어도 authorize가 즉시 발급 안 하고 동의 화면(HTML) 반환.
- [ ] `tests/test_consent.py::test_approve_issues_code` — 승인 POST → code 발급 + redirect_uri로 302.
- [ ] `tests/test_consent.py::test_upstream_path_also_consents` — 상류 로그인 재개 경로도 발급 전 동의 화면 경유.
- [ ] `tests/test_consent.py::test_switch_account` — "다른 계정으로" → 로그아웃 후 상류 재로그인 흐름.
- [ ] `tests/test_oauth_as.py` 기존 통과 — authcode/pkce 흐름이 동의 단계 추가로 안 깨짐(테스트는 승인 자동화).

**검증**: `uv run pytest tests/test_consent.py tests/test_oauth_as.py -q`

## 참고사항
- 재배포 필요(공개 인증 흐름 변경).
- 이 태스크는 [[account-model]] 위의 인증 UX/보안 마감. Admin(v2-08)과는 별개.
- 로그인 화면을 실제로 보려면 vegapunk·상류 둘 다 로그아웃이어야 하는 건 그대로(동의 화면은 그와 무관하게 항상 뜸).
