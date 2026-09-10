FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y libgeos-c1v5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY packages ./packages
COPY services ./services
COPY infra/migrations ./infra/migrations
COPY config ./config
COPY data ./data
COPY tests ./tests
COPY scripts ./scripts

RUN pip install ".[dev]"

CMD ["pytest"]
