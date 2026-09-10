FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

RUN groupadd --system radar \
    && useradd --system --gid radar --create-home radar

COPY pyproject.toml README.md alembic.ini ./
COPY packages ./packages
COPY services ./services

RUN pip install --timeout 120 --retries 10 .

COPY infra/migrations ./infra/migrations
COPY config ./config
COPY data ./data
COPY scripts ./scripts

RUN mkdir -p /app/data/exports/daily \
    && chown -R radar:radar /app

USER radar

EXPOSE 8000

CMD ["uvicorn", "home_radar_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
