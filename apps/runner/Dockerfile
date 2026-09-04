FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin runner

WORKDIR /workspace
USER runner

CMD ["python3", "--version"]
