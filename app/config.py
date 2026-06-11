from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """애플리케이션 환경설정. 환경변수 또는 .env에서 로드."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres DSN. 컨테이너(jscraft 네트워크): postgres:5432, 호스트: localhost:5432
    DATABASE_URL: str = (
        "postgresql://postgres@localhost:5432/vegapunk"
    )
    # 이후 태스크(세션/인증)용. 지금은 선언만.
    REDIS_URL: str = "redis://localhost:6379"
    # dev | test | prod
    APP_ENV: str = "dev"

    # ── openclaw LLM (Task 05) ──────────────────────────────
    # 호스트 래퍼 엔드포인트/인증. session_id는 절대 전송하지 않음(무상태).
    OPENCLAW_BASE_URL: str = "http://localhost:8080"
    OPENCLAW_API_KEY: str = ""
    # tier → 실제 openclaw 모델 매핑.
    OPENCLAW_MODEL_LOW: str = "low"
    OPENCLAW_MODEL_DEFAULT: str = "default"

    # ── 인증/세션 (Task 10) ─────────────────────────────────
    SECRET_KEY: str = "dev-secret-change-me"  # SessionMiddleware(authlib state)용
    SESSION_TTL: int = 60 * 60 * 24 * 14  # 세션 쿠키/Redis TTL(초)
    OAUTH_REDIRECT_BASE: str = "http://localhost:8000"
    GITHUB_CLIENT_ID: str = ""
    GITHUB_CLIENT_SECRET: str = ""
    KAKAO_REST_API_KEY: str = ""
    KAKAO_CLIENT_SECRET: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
