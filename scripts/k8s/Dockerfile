# Lightweight ClawBench image for Kubernetes sidecar use.
# Does NOT include the full OpenClaw server or Chromium — the gateway runs
# in a separate container. Node.js is copied from the OpenClaw image for
# the device-identity handshake required by the gateway protocol.
FROM ghcr.io/openclaw/openclaw:latest AS openclaw

FROM python:3.12-slim

COPY --from=openclaw /usr/local/bin/node /usr/local/bin/node

RUN apt-get update && \
    apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md CLAWBENCH_V0_4_SPEC.md PARTNER_TRACE_SPEC.md ./
COPY clawbench/ clawbench/
COPY tasks-public/ tasks-public/
COPY tasks-domain/ tasks-domain/
COPY profiles/ profiles/
COPY baselines/ baselines/
COPY scripts/ scripts/

RUN pip install --no-cache-dir ".[mlflow]"

RUN mkdir -p /results && chmod 777 /results

RUN useradd -m -d /home/node clawbench
USER clawbench
ENV HOME=/home/node

ENTRYPOINT ["clawbench"]
