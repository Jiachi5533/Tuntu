# syntax=docker/dockerfile:1
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TUNTU_DATA_DIR=/data \
    TUNTU_HOST=0.0.0.0 \
    TUNTU_PORT=8000

WORKDIR /app

RUN groupadd --gid 10001 tuntu \
    && useradd --uid 10001 --gid 10001 --home-dir /app --no-create-home tuntu \
    && install -d -o tuntu -g tuntu -m 0700 /data

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read()"]

ENTRYPOINT ["tuntu"]
CMD ["serve"]
