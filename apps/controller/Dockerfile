FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin aegaeon \
    && mkdir -p /data /worker-state \
    && chown -R aegaeon:aegaeon /app /data /worker-state
COPY pyproject.toml README.md ./
COPY aegaeon ./aegaeon
COPY worker ./worker
COPY apps ./apps
RUN pip install --no-cache-dir .
USER aegaeon
EXPOSE 8000
CMD ["python", "-m", "apps.controller.main"]
