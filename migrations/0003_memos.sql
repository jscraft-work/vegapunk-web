-- 메모 서버 저장: 대화별 메모(1:1) + 사용자별 글로벌 메모.

-- 대화별 메모: conversations에 1:1 컬럼(대화 삭제 시 함께 삭제).
ALTER TABLE conversations ADD COLUMN memo TEXT NOT NULL DEFAULT '';

-- 사용자별 글로벌 메모. 행은 첫 저장 시 생성(없으면 GET이 빈 문자열).
CREATE TABLE user_memo (
  user_id    BIGINT PRIMARY KEY REFERENCES users ON DELETE CASCADE,
  body       TEXT NOT NULL DEFAULT '',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
