# ---- Stage 1: build Tailwind/daisyUI CSS assets ----
FROM node:20-alpine AS assets

WORKDIR /build

COPY package.json package-lock.json* ./
RUN npm install

COPY tailwind.config.js ./
COPY app/templates ./app/templates
COPY app/static/src ./app/static/src

RUN npm run build:css

# ---- Stage 2: Python application image ----
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_APP=run.py

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /build/app/static/dist ./app/static/dist

RUN chmod +x entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
