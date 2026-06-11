# vegapunk 앱 이미지. uv로 의존성 설치, fastembed 모델 프리페치(콜드스타트 단축).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE=/app/.fastembed_cache \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN pip install --no-cache-dir uv

# 레이어 캐시: lock 먼저 복사 → 설치 → 소스 복사.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 소스.
COPY app ./app
COPY migrations ./migrations
COPY static ./static

# fastembed 모델을 이미지에 미리 받아둔다(uv.lock 고정 버전으로 — 풀링 일관성).
RUN uv run python -c "from app import embedding; embedding._get_model()"

# 비루트 유저.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
