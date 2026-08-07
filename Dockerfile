# ---- Stage 1: build Tailwind/daisyUI CSS assets ----
FROM node:20-alpine AS assets

WORKDIR /build

COPY package.json package-lock.json* ./
RUN npm install

COPY tailwind.config.js ./
COPY app/templates ./app/templates
COPY app/static/src ./app/static/src
COPY app/static/js ./app/static/js

RUN npm run build:css

# ---- Stage 2: Python application image ----
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=run.py

# git: needed by GitPython to clone source repos for the image builder.
# ca-certificates: required by kaniko-executor (a static Go binary with no
#   bundled cert store of its own) to verify registry TLS when the "kaniko"
#   build engine pushes images — not otherwise guaranteed present on -slim.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# docker CLI + buildx plugin: the "docker" build engine (see
# app/services/build/engine.py) shells out to `docker buildx build` against
# the mounted host socket, for BuildKit support (RUN --mount, heredocs, ...)
# — apt's docker.io package provides a docker CLI but does not bundle the
# buildx plugin, so it's copied from the official docker CLI image instead.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx

# kaniko-executor: daemonless build+push engine, an alternative to the
# docker CLI above for environments where mounting /var/run/docker.sock
# isn't possible/desired (see "kaniko" in SystemConfig.build_engine). Copied
# as a single static binary from kaniko's own (scratch-based) image rather
# than installed via a package manager — that's the whole image's contents.
COPY --from=gcr.io/kaniko-project/executor:v1.23.2 /kaniko/executor /kaniko/executor

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /build/app/static/dist ./app/static/dist

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
