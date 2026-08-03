FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for database drivers
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better layer caching
COPY pyproject.toml ./
COPY requirements.txt ./

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/
COPY semantic/ ./semantic/
COPY evals/ ./evals/
COPY harness/ ./harness/
COPY tests/ ./tests/

ENV PYTHONPATH=/app/src
ENV DATA_AGENT_AUTH=true

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000

CMD ["python3", "src/server.py"]
