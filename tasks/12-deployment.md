# Task 12: 배포 (Docker + CI)

## 목표
앱을 컨테이너로 패키징하고, docker-compose로 app+postgres(pgvector)+redis를 함께 띄우며, GitHub Actions에서 테스트를 돌리고 이미지를 빌드한다. 임베딩 모델은 이미지에 미리 받아 콜드스타트를 줄인다.

## 선행 조건
- Task 01~11 완료(앱 전체).

## 구현 상세

### 12.1 Dockerfile
- `python:3.12-slim` 기반, `uv`로 의존성 설치(레이어 캐시: lock 먼저 복사 → 설치 → 소스 복사).
- **fastembed 모델 프리페치**: 빌드 단계에서 `intfloat/multilingual-e5-large`를 `FASTEMBED_CACHE`로 다운로드해 이미지에 포함(런타임 콜드스타트 단축).
- ⚠️ **fastembed 버전은 `uv.lock`에 고정된 정확한 버전을 사용**(레이어 캐시용 lock 복사 단계가 이를 보장). 버전이 달라지면 e5 풀링 방식이 바뀌어 색인 벡터와 질의 벡터가 어긋난다(02 2.1 참고). 업그레이드 시 전체 재인덱싱 필요.
- 비루트 유저, `uvicorn app.main:app --host 0.0.0.0 --port 8000`. `HEALTHCHECK`는 `/health`.

### 12.2 docker-compose.yml
- 서비스: `app`, `db`(`pgvector/pgvector:pg16` 등 pgvector 포함 이미지), `redis`.
- `db`는 커스텀 이미지 `jscraft/postgres-pgvector-bigm:pg16`(베이스 `postgres:16` + pgvector + pg_bigm 컴파일 내장. Dockerfile: `infra/postgres-bigm/`). **공식 postgres:16엔 두 확장 미포함**이므로 이 이미지를 빌드/사용. 앱은 부팅 시 마이그레이션(`CREATE EXTENSION vector`/`pg_bigm` 포함) 자동 실행.
- ⚠️ **베이스는 반드시 `postgres:16`**(trixie, glibc 2.41). bookworm 기반 이미지로 바꾸면 기존 데이터와 **collation 버전 불일치**가 나므로 금지.
- 환경변수: `DATABASE_URL`, `REDIS_URL`, OAuth/openclaw 변수. `.env` 사용, 시크릿은 커밋 금지.
- 볼륨: db 데이터, fastembed 캐시(또는 이미지 내장).

### 12.3 GitHub Actions (`.github/workflows/ci.yml`)
- `on: push/pull_request`.
- job `test`: Postgres(pgvector 이미지) + Redis를 **services**로 띄우고, `uv`로 설치 후 `uv run pytest -q`. `DATABASE_URL`/`REDIS_URL`을 services로 지정. 마이그레이션은 테스트 하네스가 적용.
- job `build`(test 통과 후): Docker 이미지 빌드(+ 선택 push). 태그는 커밋 SHA.

### 12.4 운영 메모 (`README` 또는 `docs/deploy.md`)
- 기존 운영 Postgres/Redis 재사용 시 compose의 db/redis는 생략하고 `DATABASE_URL`/`REDIS_URL`만 주입하는 방법 기술.
- pgvector·pg_bigm 확장 사전 설치 요건(커스텀 이미지로 충족), 마이그레이션 초기 1회 권한.

## 완료 기준

### 자동 검증
- [ ] CI에서 `uv run pytest -q` 전부 통과(Postgres+Redis services 기준).
- [ ] `docker build`가 성공하고 이미지에 모델 캐시 포함(빌드 로그/크기 확인).

### 수동 검증
- [ ] `docker compose up` 후 `curl localhost:8000/health` → 200(db/pgvector/pg_bigm true).
- [ ] 브라우저로 `/login` 진입 → OAuth(테스트 자격 또는 목) → 채팅 1턴 동작.
- [ ] 컨테이너 재기동 후 데이터(노트/대화) 유지(볼륨 영속).

**검증 실행 명령어**: `docker compose up --build` + `curl localhost:8000/health` (CI는 푸시 시 자동).

## 참고사항
- 모델 프리페치를 빼면 첫 요청이 수십 초 지연 → 반드시 이미지에 포함하거나 영속 볼륨 캐시.
- 시크릿(OAuth/openclaw)은 Actions Secrets/배포 환경변수로만. 레포에 커밋 금지.
- 단일 사용자라 스케일아웃 불필요 — 단일 app 컨테이너로 충분.
