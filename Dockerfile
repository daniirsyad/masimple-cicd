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
# curl: only needed transiently, to fetch the kubectl binary below.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# kubectl: KubernetesProvider (app/services/deployment/kubernetes_provider.py)
# shells out to `kubectl` for every action against a registered Kubernetes
# DeploymentServer — test-connection, apply/delete, pods/secrets/configmaps
# management, rollout restarts — same "shell out to the real CLI, don't
# reimplement its protocol" precedent as the docker CLI below. Also used by
# the "kaniko" build engine (KanikoBuildEngine, app/services/build/engine.py)
# to launch/watch/tear down a Kubernetes Job that runs the *official*
# kaniko-executor image as its own pod — not a copy of that binary in this
# image (an earlier version of KanikoBuildEngine ran kaniko-executor as a
# bare subprocess sharing this app's own root filesystem, which corrupted a
# live app container mid-build; running it as its own pod avoids that
# entirely, so there's nothing kaniko-related to install here anymore).
# Not present on python:3.12-slim by default; installed from the official
# release URL and pinned to a specific version, same as the docker CLI below.
ARG KUBECTL_VERSION=v1.30.4
RUN curl -fsSL -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/$(dpkg --print-architecture)/kubectl" \
    && chmod +x /usr/local/bin/kubectl

# docker CLI + buildx plugin: the "docker" build engine (see
# app/services/build/engine.py) shells out to `docker buildx build` against
# the mounted host socket, for BuildKit support (RUN --mount, heredocs, ...)
# — apt's docker.io package provides a docker CLI but does not bundle the
# buildx plugin, so it's copied from the official docker CLI image instead.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker:27-cli /usr/local/libexec/docker/cli-plugins/docker-buildx /usr/local/libexec/docker/cli-plugins/docker-buildx

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /build/app/static/dist ./app/static/dist

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
