# vegapunk

개인 지식베이스 기반 AI 대화 비서 ("제2의 뇌"). 내가 저장한 노트를 근거(RAG)로 답하고, 대화를 노트로 증류(distill)해 지식을 불려나간다.

## 스택
- Python 3.12 / `uv`
- FastAPI + uvicorn, SSE는 `sse-starlette`
- Postgres + pgvector + pg_bigm, 드라이버 `psycopg` (psycopg3) + `psycopg_pool`
- 임베딩: `fastembed` (`intfloat/multilingual-e5-large`, 1024차원)

## 개발
```bash
uv sync --extra dev
uv run uvicorn app.main:app --reload
curl localhost:8000/health
```

## 테스트
테스트는 `DATABASE_URL` 환경변수로 대상 DB를 받는다. (테스트 전용 DB 사용 권장.)
```bash
DATABASE_URL=postgresql://postgres@localhost:55432/vegapunk_test uv run pytest tests/ -q
```
