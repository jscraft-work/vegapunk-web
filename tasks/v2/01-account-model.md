# Task v2-01: 계정 모델 (멀티 신원)

## 목표
한 사람이 **여러 로그인 신원**(회사 GitHub, 개인 카카오, 구글 등)을 한 계정으로 쓸 수 있게 `users`↔`identities`를 분리한다. 어디서 로그인하든(웹앱/claude.ai 커넥터) 같은 `user_id`로 수렴해 **노트가 한 저장소로 모이게** 한다. 멀티유저 전제이므로 v1부터 제대로 만든다.

## 선행 조건
- 현재 단일 유저 1명(기존 `users` 1행, notes/conversations 소유). PK는 **기존 `bigint` 유지**(notes/conversations/user_memo가 `users.id` bigint FK).

## 구현 상세

### 01.1 마이그레이션 `migrations/0005_identities.sql`
```sql
CREATE TABLE identities (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id    BIGINT NOT NULL REFERENCES users ON DELETE CASCADE,
  provider   TEXT NOT NULL,          -- 'kakao' | 'github' | 'google'
  sub        TEXT NOT NULL,          -- 제공자 내 고유 ID (식별 키)
  email      TEXT,                   -- 그 신원이 준 이메일(표시용)
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, sub)
);
CREATE INDEX idx_identities_user ON identities(user_id);

ALTER TABLE users ADD COLUMN status      TEXT NOT NULL DEFAULT 'active';  -- 'active'|'merged'
ALTER TABLE users ADD COLUMN merged_into BIGINT REFERENCES users(id);
ALTER TABLE users ADD COLUMN merged_at   TIMESTAMPTZ;
-- 링크 전엔 서로 다른 user가 같은 email을 가질 수 있으므로 유니크 해제.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;
```
- 기존 1유저는 `identities`를 만들지 않는다(그의 `sub`를 모름). 아래 find-or-create의 **일회용 브리지**가 다음 로그인 때 흡수한다.

### 01.2 find-or-create (`app/routes/auth.py` — `_upsert_user` 교체)
`_fetch_profile`은 이미 `{provider, sub, email, name}`을 반환하므로, 신원 기반 식별로 바꾼다.
```python
async def resolve_user(pool, profile) -> int:
    # 1) 아는 신원?
    row = await fetchrow(pool,
        "SELECT user_id FROM identities WHERE provider=%s AND sub=%s",
        (profile["provider"], profile["sub"]))
    if row:
        return row["user_id"]
    # 2) 일회용 브리지: identity 0개인 레거시 user와 email 일치 시 흡수(탈취 위험 없음).
    user_id = None
    if profile.get("email"):
        legacy = await fetchrow(pool,
            "SELECT u.id FROM users u WHERE u.email=%s AND u.status='active' "
            "AND NOT EXISTS (SELECT 1 FROM identities i WHERE i.user_id=u.id)",
            (profile["email"],))
        if legacy:
            user_id = legacy["id"]
    # 3) 신규 가입(user+identity 생성)
    if user_id is None:
        u = await fetchrow(pool,
            "INSERT INTO users (email, name) VALUES (%s,%s) RETURNING id",
            (profile.get("email") or f'{profile["provider"]}:{profile["sub"]}', profile.get("name")))
        user_id = u["id"]
    await execute(pool,
        "INSERT INTO identities (user_id, provider, sub, email) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (provider, sub) DO NOTHING",
        (user_id, profile["provider"], profile["sub"], profile.get("email")))
    return user_id
```
- **금지**: "provider 달라도 email 같으면 자동 합침"은 하지 않는다. 브리지는 *identity 0개 레거시*에만 한정.
- `_login_and_redirect`는 `resolve_user`가 준 `user_id`로 세션 생성(기존 흐름 유지).

### 01.3 link_account (이미 로그인된 상태에서 시작 → 브라우저 OAuth로 소유 증명)
연동은 **두 신원 모두 로그인 가능함을 증명**해야 안전 → 반드시 브라우저 OAuth를 한 번 탄다.
```
POST /api/account/link/start  (require_user)
   → 일회용 토큰 발급(현재 user_id 담음, 짧은 만료, Redis) → { url: "/auth/login/{provider}?link=<token>" }
GET  /auth/callback/{provider}?...&link=<token>
   → 기존 OAuth 흐름으로 (provider, sub) 확보
   → link 토큰에서 current_user_id 복원
   → 충돌 검사 후 identities INSERT
```
충돌 처리:
```python
existing = await fetchrow(pool,
    "SELECT user_id FROM identities WHERE provider=%s AND sub=%s", (provider, sub))
if existing and existing["user_id"] != current_user_id:
    return {"status": "conflict", "other_user": existing["user_id"]}  # 자동병합 금지 → 병합 플로우 안내
await execute(pool, "INSERT INTO identities (user_id, provider, sub, email) "
    "VALUES (%s,%s,%s,%s) ON CONFLICT (provider, sub) DO NOTHING",
    (current_user_id, provider, sub, email))
```
- 콜백은 `link` 파라미터 유무로 "신규 로그인"과 "연동"을 분기(기존 콜백 핸들러에 가지치기).
- 웹앱 설정 화면 버튼 + (Task 03에서) MCP `link_account` 도구가 둘 다 `/api/account/link/start`를 호출.

### 01.4 병합 (실수로 계정 2개 — 삭제 금지, 툼스톤)
```python
async def merge_users(pool, src_id, dst_id):  # src를 dst로 흡수
    async with pool.connection() as conn:
        async with conn.transaction():
            for tbl in ("notes", "conversations", "identities", "user_memo"):
                await conn.execute(f"UPDATE {tbl} SET user_id=%s WHERE user_id=%s", (dst_id, src_id))
            await conn.execute("UPDATE users SET status='merged', merged_into=%s, merged_at=now() "
                               "WHERE id=%s", (dst_id, src_id))
```
- `user_memo`는 PK가 `user_id`라 dst에 이미 행이 있으면 충돌 → 정책: dst 우선(src 행 스킵/삭제) 또는 본문 합치기. 단순히 dst 우선으로.

### 01.5 user_id 스코프 점검
- notes/conversations/memo/검색 모든 쿼리가 `user_id`로 필터되는지 재확인(기존 0002에서 도입됨, 누락 점검만).
- `status='merged'` user로는 신규 세션이 안 생기게(merge된 신원은 dst로 이전되므로 자연히 dst로 로그인됨).

## 완료 기준

### 자동 검증 (테스트, OAuth 프로바이더 모킹)
- [ ] `tests/test_account.py::test_new_identity_creates_user` — 처음 보는 (provider,sub) → user+identity 생성.
- [ ] `tests/test_account.py::test_known_identity_returns_same_user` — 같은 (provider,sub) 재로그인 → 같은 user_id.
- [ ] `tests/test_account.py::test_legacy_bridge` — identity 0개 레거시 user와 email 일치 시 그 user에 흡수(중복 user 미생성).
- [ ] `tests/test_account.py::test_no_email_autolink` — identity가 있는 user와 email만 같으면 **자동 합치지 않음**(새 user).
- [ ] `tests/test_account.py::test_link_account_attaches_identity` — 로그인 상태 + link 콜백 → 현재 user에 새 identity 추가.
- [ ] `tests/test_account.py::test_link_conflict` — 다른 user에 묶인 신원 link 시 conflict 반환(중복 INSERT 안 됨).
- [ ] `tests/test_account.py::test_merge_moves_data_and_tombstones` — merge 후 notes/identities가 dst로 이전, src는 status='merged'.

**검증 실행 명령어**: `uv run pytest tests/test_account.py -q`

## 참고사항
- PK는 bigint 유지(문서의 uuid는 무시 — 기존 FK 보존, 이득 없음).
- 이 태스크는 **MCP 없이도 웹앱만으로 테스트 가능**(층 2, 데이터 모델). MCP 도구는 Task 03에서 이 위에 올린다.
- `link_account` 브라우저 흐름은 기존 kakao/github OAuth(상류) 재사용 — AS(Task 02)와 무관.
