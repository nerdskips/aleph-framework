FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install framework from PyPI
RUN pip install --no-cache-dir zuper-agent

# Copy agent files (config, prompts, tools, data)
COPY config.yaml ./agent/config.yaml
COPY prompts/     ./agent/prompts/
COPY tools/       ./agent/tools/
COPY data/        ./agent/data/
COPY .env         ./agent/.env

# Tell the framework where the agent files live
ENV AGENT_DIR=/app/agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()" || exit 1

CMD ["python", "-m", "core.api.webhooks"]
