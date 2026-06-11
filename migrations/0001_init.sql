-- 기획서 9장: 전체 스키마

-- ── 확장 (1회) ─────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector: 의미검색(벡터)
CREATE EXTENSION IF NOT EXISTS pg_bigm;   -- 글자검색(2-gram, CJK)

-- ── 노트 ───────────────────────────────
CREATE TABLE notes (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  title      TEXT UNIQUE NOT NULL,
  body       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE note_versions (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  note_id    BIGINT NOT NULL REFERENCES notes ON DELETE CASCADE,
  body       TEXT NOT NULL,
  source     TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── 검색 (청크 + 의미 + 글자) ───────────
CREATE TABLE chunks (
  id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  note_id   BIGINT NOT NULL REFERENCES notes ON DELETE CASCADE,
  ord       INTEGER NOT NULL,
  text      TEXT NOT NULL,
  embedding vector(1024)
);
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_chunks_bigm ON chunks USING gin (text gin_bigm_ops);
CREATE INDEX idx_chunks_note ON chunks(note_id);

-- ── 그래프 (연결) ──────────────────────
CREATE TABLE edges (
  src_note  BIGINT NOT NULL REFERENCES notes ON DELETE CASCADE,
  dst_title TEXT NOT NULL,
  dst_note  BIGINT REFERENCES notes ON DELETE CASCADE,
  kind      TEXT NOT NULL DEFAULT 'wikilink',
  PRIMARY KEY (src_note, dst_title, kind)
);
CREATE INDEX idx_edges_dst ON edges(dst_note);

-- ── 태그 ───────────────────────────────
CREATE TABLE tags (
  id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name TEXT UNIQUE NOT NULL
);
CREATE TABLE note_tags (
  note_id BIGINT NOT NULL REFERENCES notes ON DELETE CASCADE,
  tag_id  BIGINT NOT NULL REFERENCES tags  ON DELETE CASCADE,
  PRIMARY KEY (note_id, tag_id)
);

-- ── 대화 ───────────────────────────────
CREATE TABLE conversations (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  title               TEXT,
  summary             TEXT,
  summary_upto_msg_id BIGINT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  conv_id     BIGINT NOT NULL REFERENCES conversations ON DELETE CASCADE,
  role        TEXT NOT NULL,
  content     TEXT NOT NULL,
  sent_prompt TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conv ON messages(conv_id, id);

CREATE TABLE message_citations (
  message_id BIGINT NOT NULL REFERENCES messages ON DELETE CASCADE,
  note_id    BIGINT REFERENCES notes ON DELETE SET NULL,
  score      REAL,
  PRIMARY KEY (message_id, note_id)
);

-- ── 사용자 (인증, 최소) ────────────────
CREATE TABLE users (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  email      TEXT UNIQUE NOT NULL,
  name       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
