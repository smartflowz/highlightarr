FROM python:3.12-slim

# Shown on the container registries; "source" also links the ghcr.io package to the repository.
LABEL org.opencontainers.image.title="highlightarr" \
      org.opencontainers.image.description="Automatically cuts highlights from recordings" \
      org.opencontainers.image.source="https://github.com/smartflowz/highlightarr" \
      org.opencontainers.image.licenses="GPL-3.0-only"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Intel VA-API drivers (and `vainfo` for troubleshooting) enable hardware encoding. They only exist
# for amd64, so the arm64 image is built without them.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg tzdata \
    && if [ "$(dpkg --print-architecture)" = "amd64" ]; then \
         apt-get install -y --no-install-recommends intel-media-va-driver i965-va-driver vainfo; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY LICENSE ./LICENSE

# Shown on the About page; set by the CI build (--build-arg GIT_COMMIT=<sha>).
ARG GIT_COMMIT=""
ENV GIT_COMMIT=${GIT_COMMIT}

# The port inside the container. Publish it on any host port in docker-compose.yml.
ENV PORT=9797
EXPOSE 9797
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['PORT'], timeout=3)"

# One process on purpose: the web UI reads the worker's live state in memory.
# (`exec` so uvicorn, not the shell, receives the stop signal.)
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
