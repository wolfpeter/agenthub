FROM node:24-slim AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_COMPILE_BYTECODE=1 UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY agenthub ./agenthub
COPY migrations ./migrations
COPY alembic.ini ./
COPY --from=ui /src/agenthub/static ./agenthub/static
RUN uv sync --frozen --no-dev
ENV PATH=/opt/venv/bin:$PATH HOME=/tmp
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn agenthub.api.app:app --host 0.0.0.0 --port 8000 --proxy-headers"]
