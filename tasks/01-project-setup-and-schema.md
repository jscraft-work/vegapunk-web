# Task 01: 프로젝트 셋업 & DB 스키마

## 목표
FastAPI 앱이 기동되고, Postgres에 전체 스키마(기획서 9장)가 마이그레이션으로 적용되며, `GET /health`가 DB·확장 상태를 200으로 반환한다. 이후 모든 태스크가 올라설 토대(설정·DB 풀·마이그레이션 러너·테스트 하네스)를 만든다.

## 선행 조건
- 없음(그린필드). 단, **pgvector + pg_bigm이 설치된 Postgres**가 있어야 함. 운영 DB(macmini)는 이미 준비됨: 커스텀 이미지 `jscraft/postgres-pgvector-bigm:pg16`로 교체 완료, `vegapunk` DB 생성 + `vector`·`pg_bigm` 확장 활성화 완료. 로컬/CI 테스트는 같은 이미지를 사용(12장 docker-compose 참고).

## 구현 상세

### 1.1 패키지/프로젝트 초기화
- `uv`로 프로젝트 초기화, `pyproject.toml` 작성. 의존성:
  - 런타임: `fastapi`, `uvicorn[standard]`, `psycopg[binary,pool]`, `pgvector`, `sse-starlette`, `pydantic-settings`
  - 개발: `pytest`, `pytest-asyncio`, `httpx`
- Python 3.12 고정. `README.md`의 프로젝트 레이아웃을 그대로 생성(빈 패키지 `app/`, `app/routes/`).

### 1.2 설정 (`app/config.py`)
- `pydantic-settings`의 `BaseSettings`로 `Settings` 정의. 환경변수:
  - `DATABASE_URL`(postgres DSN), `REDIS_URL`(이후 태스크용, 지금은 선언만), `APP_ENV`(`dev`/`test`/`prod`)
- `.env.example` 작성. `get_settings()`는 `@lru_cache`.
- **DATABASE_URL**: 기존 `postgres` 슈퍼유저 재사용(전용 롤 안 만듦), DB는 `vegapunk`.
  - 컨테이너(`jscraft` 네트워크)에서: `postgresql://postgres:${DB_PASSWORD}@postgres:5432/vegapunk`
  - 호스트/로컬에서: `postgresql://postgres:${DB_PASSWORD}@localhost:5432/vegapunk`
  - REDIS_URL: `redis://redis:6379`(컨테이너) / `redis://localhost:6379`(호스트). 비번/호스트는 macmini `.env`의 `DB_PASSWORD` 사용.

### 1.3 DB 풀 + 마이그레이션 러너 (`app/db.py`)
- `psycopg_pool.AsyncConnectionPool`을 앱 lifespan에서 열고 닫는다. 풀 생성 시 `configure` 훅에서 `pgvector.psycopg.register_vector_async(conn)` 등록.
- `run_migrations(pool)`:
  - `schema_migrations(version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now())` 없으면 생성.
  - `migrations/` 의 `NNNN_*.sql`을 파일명 순으로 읽어, 아직 `schema_migrations`에 없는 버전만 **각각 트랜잭션으로** 실행 후 기록.
- 헬퍼: `async def fetch/fetchrow/execute(...)` 또는 풀에서 `conn`을 얻는 컨텍스트 — 이후 태스크가 공통으로 쓸 최소 인터페이스만.

### 1.4 마이그레이션 파일 (`migrations/0001_init.sql`)
- 기획서 9장의 **전체 스키마를 그대로** 작성: `CREATE EXTENSION vector` / `CREATE EXTENSION pg_bigm`, notes, note_versions, chunks(+ embedding vector(1024), hnsw 인덱스, `idx_chunks_bigm`=`gin (text gin_bigm_ops)`, note 인덱스), edges(+idx), tags, note_tags, conversations, messages(+idx), message_citations(note_id 기준), users.
- 모든 시각 컬럼 `TIMESTAMPTZ DEFAULT now()`.

### 1.5 앱 팩토리 + health (`app/main.py`)
- `create_app()`에서 lifespan으로 풀 오픈 → `run_migrations` 실행 → 라우터 등록(지금은 health만).
- `GET /health` → `{ "status": "ok", "db": true, "pgvector": true, "pg_bigm": true }`. 각 확장 존재는 `SELECT 1 FROM pg_extension WHERE extname=...`로 확인.

### 1.6 테스트 하네스 (`tests/conftest.py`)
- 테스트 전용 DB(또는 스키마)에 마이그레이션을 적용하고, 각 테스트 후 데이터 정리(TRUNCATE) 하는 fixture.
- `httpx.AsyncClient(transport=ASGITransport(app=...))` 기반 `client` fixture.

## 완료 기준

### 자동 검증 (테스트)
- [ ] `tests/test_health.py` — `GET /health`가 200이고 `db/pgvector/pg_bigm` 모두 true.
- [ ] `tests/test_migrations.py` — 마이그레이션 2회 실행해도 멱등(중복 적용 안 됨), 9장의 모든 테이블이 `information_schema.tables`에 존재.
- [ ] `tests/test_schema.py` — `chunks.embedding` 컬럼 타입이 `vector`, `idx_chunks_bigm`(gin)·`idx_chunks_embedding`(hnsw) 인덱스 존재.

### 수동 검증
- [ ] `uv run uvicorn app.main:app` 기동 후 `curl localhost:8000/health` → 200.

**검증 실행 명령어**: `uv run pytest tests/ -q`

## 참고사항
- pgvector 등록은 psycopg3에서 연결마다 필요 → 반드시 풀 `configure` 훅에서. 누락 시 `vector` 컬럼이 문자열로 들어와 04에서 깨짐.
- HNSW 인덱스는 행이 적을 때도 생성 가능. 데이터가 거의 없을 때 검색은 인덱스 미사용 seq scan일 수 있으나 정상.
- 다음 태스크(02~)는 이 풀/설정/마이그레이션 구조를 그대로 재사용한다.
