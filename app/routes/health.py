from fastapi import APIRouter, Request

router = APIRouter()


async def _has_extension(conn, name: str) -> bool:
    cur = await conn.execute(
        "SELECT 1 FROM pg_extension WHERE extname = %s", (name,)
    )
    return await cur.fetchone() is not None


@router.get("/health")
async def health(request: Request) -> dict:
    pool = request.app.state.pool
    async with pool.connection() as conn:
        await conn.execute("SELECT 1")
        db_ok = True
        pgvector = await _has_extension(conn, "vector")
        pg_bigm = await _has_extension(conn, "pg_bigm")
    return {
        "status": "ok",
        "db": db_ok,
        "pgvector": pgvector,
        "pg_bigm": pg_bigm,
    }
