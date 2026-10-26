FROM node:22.14.0-bookworm-slim AS frontend
WORKDIR /app
RUN corepack enable && corepack prepare pnpm@11.25.0 --activate
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY tsconfig.json vite.config.ts index.html ./
COPY frontend frontend
COPY public public
RUN pnpm exec tsc --noEmit && pnpm exec vite build
FROM python:3.12.10-slim-bookworm
WORKDIR /app
COPY requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend backend
COPY data/fixtures data/fixtures
COPY data/reference data/reference
COPY --from=frontend /app/dist dist
RUN useradd -m app && mkdir -p data/local && chown -R app:app /app
USER app
CMD ["python","-m","uvicorn","backend.app.main:app","--host","0.0.0.0","--port","8000"]
