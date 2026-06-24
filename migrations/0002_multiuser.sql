-- 멀티유저 전환: 노트/대화에 소유권(user_id) 도입.
-- 기존 데이터는 'ocpek@kakao.com'(없으면 가장 오래된 user)에게 백필.

ALTER TABLE notes         ADD COLUMN user_id BIGINT REFERENCES users ON DELETE CASCADE;
ALTER TABLE conversations ADD COLUMN user_id BIGINT REFERENCES users ON DELETE CASCADE;

-- 백필: 소유자 1명을 골라 기존 행에 채운다.
-- (데이터가 있는데 user가 0명이면 아래 SET NOT NULL에서 실패 → 최소 1회 로그인 필요.)
DO $$
DECLARE owner_id BIGINT;
BEGIN
  SELECT id INTO owner_id FROM users WHERE email = 'ocpek@kakao.com' ORDER BY id LIMIT 1;
  IF owner_id IS NULL THEN
    SELECT id INTO owner_id FROM users ORDER BY id LIMIT 1;
  END IF;
  UPDATE notes         SET user_id = owner_id WHERE user_id IS NULL;
  UPDATE conversations SET user_id = owner_id WHERE user_id IS NULL;
END $$;

ALTER TABLE notes         ALTER COLUMN user_id SET NOT NULL;
ALTER TABLE conversations ALTER COLUMN user_id SET NOT NULL;

-- 노트 제목 유니크: 전역 → 사용자별. (인라인 UNIQUE의 기본 제약명은 notes_title_key)
ALTER TABLE notes DROP CONSTRAINT IF EXISTS notes_title_key;
ALTER TABLE notes ADD CONSTRAINT notes_user_title_key UNIQUE (user_id, title);

CREATE INDEX idx_notes_user         ON notes(user_id, updated_at DESC);
CREATE INDEX idx_conversations_user ON conversations(user_id, updated_at DESC);
