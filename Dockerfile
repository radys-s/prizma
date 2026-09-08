# Prizma — one image, one origin, one process.
#
# The frontend is built here and copied into the Python image rather than
# deployed separately. That is not a packaging preference: the SPA calls
# relative `/api/...` URLs, so a single origin means no CORS, no third-party
# cookie question, and — on a free tier where services sleep — only one thing
# that can be asleep when the app is opened on a phone.

# --------------------------------------------------------------- frontend ---
# Debian rather than Alpine on purpose. `sharp` (the icon generator) publishes
# well-tested prebuilt binaries for glibc; on musl it is a coin flip, and a
# failed install there would break the whole image for a package that only ever
# runs manually. This stage is discarded, so its size costs nothing at runtime.
FROM node:22-slim AS web

WORKDIR /web

# Copied first so a dependency-free code change reuses the cached install.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- backend ---
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# `psycopg[binary]` ships its own libpq, so there is no build toolchain and no
# `libpq-dev` to install. The image stays slim and the build stays fast.
COPY backend/pyproject.toml backend/requirements.lock /app/backend/
COPY backend/app /app/backend/app
# `-c` and not `-r`: the lock caps versions of whatever gets resolved without
# dictating the package list, so litellm cannot jump a major version between
# two deploys while the Linux-only extras (uvloop, httptools) still install.
RUN pip install --no-cache-dir -c /app/backend/requirements.lock /app/backend

# `spa.py` looks for the built SPA at `<backend>/../frontend/dist`, so the
# layout inside the image has to mirror the repository.
COPY --from=web /web/dist /app/frontend/dist

# Non-root. `data/` is created because `config.py` makes it at import time —
# on Render it holds nothing that survives a restart (the real database is
# Postgres), but the directory has to exist and be writable or the process
# will not start.
RUN useradd --create-home --uid 10001 prizma \
    && mkdir -p /app/backend/data \
    && chown -R prizma:prizma /app
USER prizma

WORKDIR /app/backend

# Render assigns the port at runtime; 8000 is the local fallback.
ENV PORT=8000
EXPOSE 8000

# `--proxy-headers` with `--forwarded-allow-ips=*` is what makes
# `request.client.host` the visitor's address instead of Render's proxy. The
# brute-force throttle keys on that address, so without these flags every
# attacker in the world would share one bucket — and so would Tomáš.
# Trusting `*` is safe only because nothing but the platform's proxy can reach
# this container.
#
# One worker is deliberate: the free tier has 512 MB, streaming responses hold
# a connection for the length of an answer, and a second worker would double
# the memory for a single user.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --workers 1"]
