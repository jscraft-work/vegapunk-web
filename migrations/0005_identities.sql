-- 멀티 신원 계정 모델: users(사람) ↔ identities(로그인 신원) 분리.
-- 한 사람이 여러 OAuth 신원(github/kakao/google)을 한 user로 수렴시킨다.

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

-- 병합(툼스톤)용 컬럼.
ALTER TABLE users ADD COLUMN status      TEXT NOT NULL DEFAULT 'active';  -- 'active'|'merged'
ALTER TABLE users ADD COLUMN merged_into BIGINT REFERENCES users(id);
ALTER TABLE users ADD COLUMN merged_at   TIMESTAMPTZ;

-- 링크 전엔 서로 다른 user가 같은 email을 가질 수 있으므로 유니크 해제.
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_email_key;
