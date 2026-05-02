# ClawBench HF Docker Space
# Layer the benchmark harness on top of the official OpenClaw image.

ARG BASE=ghcr.io/openclaw/openclaw:latest
FROM ${BASE}

USER root

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && \
    apt-get install -y python3-pip python-is-python3 && \
    rm -rf /var/lib/apt/lists/*

RUN ln -s /app /openclaw

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    NODE_PATH=/usr/local/lib/node_modules
RUN npm install -g playwright@1.59.1 && \
    playwright install --with-deps chromium && \
    CHROME_PATH="$(find /ms-playwright -path '*/chrome' -type f | sort | head -n 1)" && \
    test -x "$CHROME_PATH" && \
    ln -sf "$CHROME_PATH" /usr/bin/chromium

ENV HOME=/home/node PATH=/home/node/.local/bin:$PATH
WORKDIR /home/node/app

COPY --chown=node:node pyproject.toml README.md CLAWBENCH_V0_4_SPEC.md PARTNER_TRACE_SPEC.md ./
COPY --chown=node:node clawbench/ clawbench/
COPY --chown=node:node tasks-public/ tasks-public/
COPY --chown=node:node tasks-domain/ tasks-domain/
COPY --chown=node:node profiles/ profiles/
COPY --chown=node:node baselines/ baselines/
COPY --chown=node:node app.py .

RUN python3 -m pip install --break-system-packages --no-cache-dir .

RUN mkdir -p \
    /data/results \
    /data/queue \
    /home/node/.openclaw/agents/dev \
    /home/node/.openclaw/agents/main/agent && \
    chown -R node:node /data /home/node/.openclaw && \
    chmod -R 777 /data /home/node/.openclaw

USER node

ENV GATEWAY_PORT=18789
ENV OPENCLAW_HOME=/home/node
ENV OPENCLAW_STATE_DIR=/home/node/.openclaw

EXPOSE 7860
CMD ["python", "app.py"]
