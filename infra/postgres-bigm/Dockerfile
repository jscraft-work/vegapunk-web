# 커스텀 Postgres: pgvector + pg_bigm 컴파일 내장.
# ⚠️ 베이스는 반드시 postgres:16(trixie, glibc 2.41). bookworm 기반으로 바꾸면
#    기존 데이터와 collation 버전 불일치가 나므로 금지.
FROM postgres:16

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git ca-certificates postgresql-server-dev-16 \
 && rm -rf /var/lib/apt/lists/*

# pgvector — 앱 의존성과 동일 계열 버전(0.8.x)로 맞춤.
RUN git clone --depth 1 --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector \
 && cd /tmp/pgvector && make && make install \
 && rm -rf /tmp/pgvector

# pg_bigm — 2-gram CJK 글자검색.
RUN git clone --depth 1 https://github.com/pgbigm/pg_bigm.git /tmp/pg_bigm \
 && cd /tmp/pg_bigm && make USE_PGXS=1 && make USE_PGXS=1 install \
 && rm -rf /tmp/pg_bigm

# 빌드 도구 정리(런타임 이미지 슬림화).
RUN apt-get purge -y build-essential git postgresql-server-dev-16 \
 && apt-get autoremove -y

# 확장 자체는 앱 마이그레이션(CREATE EXTENSION vector / pg_bigm)이 활성화한다.
