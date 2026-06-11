"""DB 풀 + 마이그레이션 러너.

psycopg3 비동기 풀을 사용한다. 풀의 configure 훅에서 연결마다
pgvector 타입 어댑터를 등록한다(누락 시 vector 컬럼이 문자열로 들어옴).
"""

from __future__ import annotations

import re
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector_async
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_.*\.sql$")


async def _configure(conn: psycopg.AsyncConnection) -> None:
    """연결마다 호출: pgvector 어댑터 등록.

    정상 경로에선 ensure_extensions로 vector 확장이 먼저 생성돼 있다.
    혹시 없더라도 풀이 죽지 않도록 등록 실패를 흡수한다(방어).
    """
    try:
        await register_vector_async(conn)
    except (psycopg.errors.UndefinedObject, psycopg.ProgrammingError):
        # vector 타입 미존재. register_vector_async는 ProgrammingError를 던진다.
        await conn.rollback()


async def ensure_extensions(dsn: str) -> None:
    """풀을 열기 전에 필수 확장을 보장한다.

    pgvector 어댑터 등록(_configure)은 vector 타입이 존재해야 하므로,
    풀 연결이 생기기 전에 raw 연결로 CREATE EXTENSION을 먼저 실행한다.
    prod는 이미 설치돼 있어 no-op, 빈 DB(CI/신규)는 여기서 생성된다.
    """
    conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True)
    try:
        for ext in ("vector", "pg_bigm"):
            try:
                await conn.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            except psycopg.errors.InsufficientPrivilege:
                # 비-슈퍼유저(전용 DB유저): 확장이 이미 설치돼 있으면 무해 —
                # 풀의 register_vector가 기존 vector 타입을 그대로 쓴다.
                pass
    finally:
        await conn.close()


def make_pool(dsn: str, *, open: bool = False) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=dsn,
        configure=_configure,
        open=open,
        min_size=1,
        max_size=10,
    )


async def run_migrations(pool: AsyncConnectionPool) -> list[str]:
    """migrations/NNNN_*.sql 중 미적용분만 각각 트랜잭션으로 실행.

    적용한 버전 리스트를 반환한다(멱등: 이미 적용된 것은 건너뜀).
    """
    async with pool.connection() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        await conn.commit()
        cur = await conn.execute("SELECT version FROM schema_migrations")
        applied = {row[0] for row in await cur.fetchall()}

    files = sorted(
        p for p in MIGRATIONS_DIR.glob("*.sql") if _MIGRATION_RE.match(p.name)
    )

    newly_applied: list[str] = []
    for path in files:
        version = path.name
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        async with pool.connection() as conn:
            # 각 마이그레이션을 단일 트랜잭션으로
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (version,),
                )
        newly_applied.append(version)

    return newly_applied


# ── 최소 쿼리 헬퍼 (이후 태스크 공통 인터페이스) ──────────────


async def fetch(pool: AsyncConnectionPool, sql: str, params=None) -> list[dict]:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()


async def fetchrow(pool: AsyncConnectionPool, sql: str, params=None) -> dict | None:
    async with pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()


async def execute(pool: AsyncConnectionPool, sql: str, params=None) -> None:
    async with pool.connection() as conn:
        await conn.execute(sql, params)
