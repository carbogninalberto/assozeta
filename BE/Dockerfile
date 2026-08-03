# syntax=docker/dockerfile:1.7

FROM python:3.13.2-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=it_IT.UTF-8 \
    LC_ALL=it_IT.UTF-8 \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
        libsm6 \
        libxext6 \
        locales \
    && sed -i 's/# it_IT.UTF-8 UTF-8/it_IT.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

FROM base AS dependencies

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    /opt/venv/bin/pip install -r requirements.txt

FROM base AS development

COPY --from=dependencies /opt/venv /opt/venv
COPY . .

EXPOSE 8000

CMD ["gunicorn", "core.asgi:application", "--worker-class", "uvicorn.workers.UvicornWorker", "--workers", "1", "--bind", "0.0.0.0:8000", "--reload", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]

FROM base AS production

ARG VERSION=v0.0.0

RUN groupadd --gid 10001 assozeta \
    && useradd --uid 10001 --gid assozeta --create-home assozeta

COPY --from=dependencies /opt/venv /opt/venv
COPY --chown=assozeta:assozeta . .
RUN printf '%s\n' "$VERSION" > VERSION \
    && chmod +x entrypoint.sh \
    && chown assozeta:assozeta VERSION

USER assozeta

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent http://127.0.0.1:8000/healthz >/dev/null || exit 1

CMD ["./entrypoint.sh"]
