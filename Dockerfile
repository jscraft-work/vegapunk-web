# vegapunk 앱 이미지. 모델은 이미지에 굽지 않고 마운트 볼륨(/app/models)에 둬서
# 빌드/푸시를 가볍게(빠르게) 유지한다.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE=/app/models \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
RUN pip install --no-cache-dir uv

# 레이어 캐시: lock 먼저 복사 → 의존성만 설치(프로젝트 자체는 소스 복사 후).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 소스.
COPY app ./app
COPY migrations ./migrations
COPY static ./static

# 소스 들어온 뒤 프로젝트(vegapunk) 설치.
RUN uv sync --frozen --no-dev

# 모델(~2GB)은 마운트된 vegapunk-models 볼륨(/app/models)에 첫 기동 1회 다운로드 →
# 이후 배포마다 재사용. (이미지에 굽지 않으므로 빌드/푸시가 빠름.)
# 볼륨이 root 소유라 컨테이너도 root로 실행(쓰기 가능). 단일 사용자 개인앱.
RUN mkdir -p /app/models

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
