FROM python:3.12-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python deps first (cache layer — rarely changes)
COPY pyproject.toml ./
RUN pip install --no-cache-dir . 2>/dev/null || true
COPY requirements.txt* ./
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

# Framework core (shared across all agents)
COPY core/ ./core/

# Client configs (all agents — each container picks its own via CLIENT_ID)
COPY clients/ ./clients/

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()" || exit 1

CMD ["python", "-m", "core.api.webhooks"]
