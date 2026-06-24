from app.db import MIGRATIONS_DIR, run_migrations

EXPECTED_TABLES = {
    "notes",
    "note_versions",
    "chunks",
    "edges",
    "tags",
    "note_tags",
    "conversations",
    "messages",
    "message_citations",
    "users",
    "user_memo",
}


async def test_migrations_idempotent(migrated_pool):
    # 이미 conftest에서 1회 적용됨 → 2회차는 아무것도 적용되지 않아야 함
    applied = await run_migrations(migrated_pool)
    assert applied == []

    async with migrated_pool.connection() as conn:
        cur = await conn.execute("SELECT count(*) FROM schema_migrations")
        (count,) = await cur.fetchone()
    # 적용 기록은 마이그레이션 파일 수와 일치(중복 없음)
    assert count == len(list(MIGRATIONS_DIR.glob("*.sql")))


async def test_all_tables_exist(migrated_pool):
    async with migrated_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        rows = await cur.fetchall()
    tables = {r[0] for r in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"missing tables: {missing}"
