# vegapunk 태스크 목록

## 개요
개인 지식베이스 기반 AI 대화 비서("제2의 뇌"). 내가 저장한 노트를 근거(RAG)로 답하고, 대화를 노트로 증류(distill)해 지식을 불려나간다. 단일 사용자, 추가 유료 의존성 0(Postgres·Redis는 기존 운영 인프라 재사용).

## 기술 스택 (전 태스크 공통 — 변경 금지)
- **언어/런타임**: Python 3.12
- **패키지 관리**: `uv` (`uv venv`, `uv pip install`, `pyproject.toml`)
- **웹**: FastAPI + uvicorn, SSE는 `sse-starlette`
- **DB**: **Postgres + pgvector(의미검색) + pg_bigm(글자검색, 2-gram CJK)**, 드라이버 `psycopg` (psycopg3) + `psycopg_pool`, 벡터 어댑터 `pgvector.psycopg`. 이미지: 커스텀 `jscraft/postgres-pgvector-bigm:pg16`(베이스 `postgres:16`, pgvector+pg_bigm 컴파일 내장. Dockerfile: `infra/postgres-bigm/`). **공식 postgres:16엔 두 확장 모두 미포함**이므로 이 이미지를 쓴다.
- **임베딩**: `fastembed` — `intfloat/multilingual-e5-large` (1024차원, 로컬, 무비용)
- **LLM**: openclaw (호스트 래퍼 재사용, 무상태 prompt→answer). 외부 의존이라 테스트에서는 **stub/mock**로 대체.
- **세션**: `redis` (redis-py), 서명 쿠키 → Redis 세션 저장
- **인증**: `authlib` OAuth (카카오 + GitHub)
- **테스트**: `pytest`, `pytest-asyncio`, `httpx`(ASGITransport로 인앱 호출). DB 테스트는 실제 Postgres(테스트 전용 DB/스키마)에 대해 수행.
- **프론트엔드**: 빌드 없는 바닐라 JS SPA, FastAPI가 정적 서빙
- **배포**: Docker + docker-compose(app+postgres+redis) + GitHub Actions

### 공통 규약 (모든 태스크가 지킨다)
- **e5 prefix 필수**: 청크 임베딩 = `"passage: "` 접두, 검색 쿼리 = `"query: "` 접두. 누락 시 검색 품질 붕괴.
- **openclaw는 무상태**: session_id 미사용. 매 턴 앱이 prompt 문자열을 직접 조립.
- **citations는 note_id 기준** 저장(chunk_id는 재인덱싱마다 바뀜).
- **시각**: 모든 시각 컬럼은 `TIMESTAMPTZ`, 앱에서는 UTC aware datetime으로 다룬다.
- **재인덱싱은 단일 트랜잭션**: 청크 삭제→INSERT→임베딩 UPDATE→edges 재생성을 한 트랜잭션으로.
- **마이그레이션**: `migrations/NNNN_*.sql` 순번 파일 + `schema_migrations` 테이블로 미적용분만 실행.
- **레이어 경계**: `db` → `embedding` → `indexing` → `search` → `llm` → `memory` → `chat`/`distill`/`notes` API → `auth` → `frontend` → `deploy`.

### 프로젝트 레이아웃 (01에서 확정, 이후 태스크가 따른다)
```
vegapunk-web/
  pyproject.toml
  app/
    main.py            # FastAPI 앱 팩토리 + 라우터 등록
    config.py          # 환경변수(Settings)
    db.py              # psycopg_pool, 마이그레이션 러너
    embedding.py       # 02
    indexing.py        # 03
    search.py          # 04
    llm.py             # 05 (openclaw 클라이언트)
    memory.py          # 06
    routes/
      chat.py          # 07
      distill.py       # 08
      notes.py         # 09
      auth.py          # 10
  migrations/NNNN_*.sql
  static/              # 11 (바닐라 JS SPA)
  tests/
  Dockerfile, docker-compose.yml, .github/workflows/ci.yml  # 12
```

## 태스크 목록
| # | 태스크 | 설명 | 상태 |
|---|--------|------|------|
| 01 | [프로젝트 셋업 & DB 스키마](01-project-setup-and-schema.md) | FastAPI 골격, psycopg 풀, 마이그레이션 러너, 전체 스키마, /health | ⬜ |
| 02 | [임베딩 서비스](02-embedding-service.md) | fastembed 래퍼, e5 prefix, 배치 임베딩 | ⬜ |
| 03 | [인덱싱 파이프라인](03-indexing-pipeline.md) | 청크 분할, 부분 재인덱싱(트랜잭션), 위키링크→edges 해석 | ⬜ |
| 04 | [하이브리드 검색 엔진](04-hybrid-search.md) | pgvector + pg_bigm → RRF → 그래프확장 → 추리기 | ⬜ |
| 05 | [openclaw LLM 클라이언트](05-llm-client.md) | 무상태 prompt→answer, 스트리밍, 모델 등급 | ⬜ |
| 06 | [대화 기억/압축](06-conversation-memory.md) | 증분 요약, 배칭, 불변식, 다시쓰기 입력 조립 | ⬜ |
| 07 | [채팅 파이프라인 & SSE](07-chat-pipeline.md) | 매 턴 파이프라인, /api/chat SSE, 대화 CRUD | ⬜ |
| 08 | [distill (지식 저장)](08-distill.md) | 후보 생성, 병합대상 매칭, 병합 미리보기, ingest | ⬜ |
| 09 | [노트/지식 API](09-notes-api.md) | pages/tags/search/page/태그/버전이력/되돌리기 | ⬜ |
| 10 | [인증 (카카오+GitHub)](10-auth.md) | OAuth, Redis 세션, /auth/me, 라우트 보호 | ⬜ |
| 11 | [프론트엔드 SPA](11-frontend-spa.md) | 2-스페이스 SPA, SSE 소비, distill/버전 모달 | ⬜ |
| 12 | [배포 (Docker + CI)](12-deployment.md) | Dockerfile, docker-compose, GitHub Actions | ⬜ |

## 실행 방법
각 태스크 파일을 순서대로 에이전트에게 전달하세요:
```
/ralph tasks/01-project-setup-and-schema.md 읽고 구현해
```
각 태스크 완료 후 그 시점에서 빌드/테스트가 통과하며, 다음 태스크는 이전 결과물 위에서 독립적으로 시작합니다.
