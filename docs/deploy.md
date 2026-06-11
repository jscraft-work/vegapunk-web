# 배포 가이드

## 빠른 시작 (compose 전체)

```bash
cp .env.example .env   # DB_PASSWORD, SECRET_KEY, OAuth, openclaw 값 채우기
docker compose up --build
curl localhost:8000/health   # {"status":"ok","db":true,"pgvector":true,"pg_bigm":true}
```

`db` 서비스는 커스텀 이미지 `jscraft/postgres-pgvector-bigm:pg16`을 빌드한다
(공식 `postgres:16`엔 pgvector·pg_bigm이 없음). 앱은 부팅 시 마이그레이션으로
`CREATE EXTENSION vector / pg_bigm`을 자동 실행한다.

> ⚠️ DB 베이스 이미지는 반드시 **`postgres:16`(trixie, glibc 2.41)**. bookworm
> 기반으로 바꾸면 기존 데이터와 collation 버전 불일치가 발생한다.

## 기존 운영 Postgres/Redis 재사용

이미 pgvector·pg_bigm이 설치된 Postgres와 Redis가 있으면 compose의 `db`/`redis`를
생략하고 URL만 주입한다:

```bash
docker compose up --build app   # db/redis 서비스 안 띄움
# app 환경변수에 외부 주소 주입
#   DATABASE_URL=postgresql://USER:PASS@host:5432/vegapunk
#   REDIS_URL=redis://host:6379
```

확장 사전 설치 요건: 대상 DB에 pgvector·pg_bigm이 있어야 하며, 최초 1회
마이그레이션이 `CREATE EXTENSION`을 실행할 수 있는 권한이 필요하다.

## 모델 프리페치

이미지 빌드 시 `intfloat/multilingual-e5-large`를 `FASTEMBED_CACHE`로 받아 포함한다
(빼면 첫 요청이 수십 초 지연). fastembed 버전은 `uv.lock`에 고정 — 버전이 바뀌면
e5 풀링이 달라져 색인/질의 벡터가 어긋나므로 업그레이드 시 전체 재인덱싱이 필요하다.

## 시크릿

OAuth(GitHub/Kakao)·openclaw·SECRET_KEY는 레포에 커밋 금지. GitHub Actions
Secrets 또는 배포 환경변수로만 주입한다.

## CI

`.github/workflows/ci.yml`:
- `test`: pgvector+pg_bigm 이미지 빌드 → Postgres/Redis 기동 → `uv run pytest -q`.
- `build`: 테스트 통과 후 앱 이미지 빌드(태그=커밋 SHA).
