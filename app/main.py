from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import ensure_extensions, make_pool, run_migrations
from app.deps import require_user
from app.routes import account, auth, chat, distill, health, memo, notes
from app.session import COOKIE_NAME, MemoryStore, get_session
from app import settings as app_settings

_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _asset_version() -> str:
    """정적 자산 캐시버스팅용 버전(파일 mtime). 배포 시 mtime 바뀌면 ?v 갱신."""
    try:
        return str(int(max(
            (_STATIC_DIR / f).stat().st_mtime
            for f in ("app.js", "style.css", "index.html")
        )))
    except OSError:
        return "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await ensure_extensions(settings.DATABASE_URL)
    pool = make_pool(settings.DATABASE_URL)
    await pool.open()
    await pool.wait()
    app.state.pool = pool
    await run_migrations(pool)

    # 세션 저장소: Redis 우선, 미가용 시 인메모리 폴백.
    try:
        from redis.asyncio import from_url

        r = from_url(settings.REDIS_URL)
        await r.ping()
        app.state.session_store = r
    except Exception:  # noqa: BLE001 — Redis 없으면 dev 폴백
        app.state.session_store = MemoryStore()

    await app_settings.load(app.state.session_store)  # 런타임 설정을 Redis에서 복원

    try:
        yield
    finally:
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="vegapunk", lifespan=lifespan)

    # 공개: health, auth.
    app.include_router(health.router)
    app.include_router(auth.router)
    # 보호: /api/* (chat·distill·notes).
    protected = [Depends(require_user)]
    app.include_router(chat.router, dependencies=protected)
    app.include_router(distill.router, dependencies=protected)
    app.include_router(notes.router, dependencies=protected)
    app.include_router(memo.router, dependencies=protected)
    # account: 라우트 함수에 require_user를 직접 Depends → router-level 미적용.
    app.include_router(account.router)

    @app.get("/login")
    async def login_page():
        return FileResponse(_STATIC_DIR / "login.html")

    @app.get("/")
    async def index(request: Request):
        # 미인증은 /login으로. 인증되면 SPA 셸.
        token = request.cookies.get(COOKIE_NAME)
        store = getattr(request.app.state, "session_store", None)
        if not token or store is None or await get_session(store, token) is None:
            return RedirectResponse(url="/login", status_code=302)
        # index.html은 no-cache, 참조 자산엔 ?v=버전 붙여 캐시버스팅.
        html = (_STATIC_DIR / "index.html").read_text(encoding="utf-8")
        v = _asset_version()
        html = html.replace("/static/style.css", f"/static/style.css?v={v}").replace(
            "/static/app.js", f"/static/app.js?v={v}"
        )
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
    return app


app = create_app()
