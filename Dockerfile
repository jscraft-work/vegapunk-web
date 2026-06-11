# vegapunk 앱 이미지. uv로 의존성 설치, fastembed 모델 프리페치(콜드스타트 단축).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE=/app/.fastembed_cache \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN pip install --no-cache-dir uv

# 레이어 캐시: lock 먼저 복사 → 의존성만 설치(프로젝트 자체는 소스 복사 후).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# fastembed 모델(~2GB) 프리페치 — 소스와 무관(앱 import 대신 fastembed 직접 호출).
# COPY app 앞에 둬서 앱 코드가 바뀌어도 이 레이어 캐시가 유지된다(uv.lock에만 의존).
# 모델명/캐시경로는 app/embedding.py 와 일치해야 함.
RUN uv run python -c "from fastembed import TextEmbedding; TextEmbedding('intfloat/multilingual-e5-large', cache_dir='/app/.fastembed_cache')"

# 소스.
COPY app ./app
COPY migrations ./migrations
COPY static ./static

# 소스 들어온 뒤 프로젝트(vegapunk) 설치.
RUN uv sync --frozen --no-dev

# 비루트 유저.
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
