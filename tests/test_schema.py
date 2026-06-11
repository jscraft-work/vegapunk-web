async def test_embedding_column_is_vector(migrated_pool):
    async with migrated_pool.connection() as conn:
        cur = await conn.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'chunks' AND column_name = 'embedding'"
        )
        row = await cur.fetchone()
    assert row is not None
    assert row[0] == "vector"


async def test_chunk_indexes_exist(migrated_pool):
    async with migrated_pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT i.relname AS index_name, am.amname AS method
            FROM pg_class t
            JOIN pg_index ix ON ix.indrelid = t.oid
            JOIN pg_class i ON i.oid = ix.indexrelid
            JOIN pg_am am ON am.oid = i.relam
            WHERE t.relname = 'chunks'
            """
        )
        rows = await cur.fetchall()
    indexes = {r[0]: r[1] for r in rows}

    assert "idx_chunks_bigm" in indexes
    assert indexes["idx_chunks_bigm"] == "gin"

    assert "idx_chunks_embedding" in indexes
    assert indexes["idx_chunks_embedding"] == "hnsw"
