ARG DOCKER_REGISTRY=""
ARG APT_MIRROR=""
ARG PIP_INDEX_URL="https://pypi.org/simple"
ARG PIP_TRUSTED_HOST=""

FROM ${DOCKER_REGISTRY}python:3.11-slim

# Re-declare build args after FROM to make them available in build stage
ARG APT_MIRROR=""
ARG PIP_INDEX_URL="https://pypi.org/simple"
ARG PIP_TRUSTED_HOST=""

WORKDIR /app

# Configure APT mirror if provided (for enterprise environments)
# Replaces the full base URL including /debian path
RUN if [ -n "$APT_MIRROR" ]; then \
        echo "Configuring APT mirror: $APT_MIRROR" && \
        sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
        sed -i "s|http://deb.debian.org/debian|$APT_MIRROR|g" /etc/apt/sources.list; \
    fi

# Install system dependencies (including PostgreSQL client for pg_dump)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Configure pip for custom PyPI mirror (for enterprise environments)
RUN if [ -n "$PIP_TRUSTED_HOST" ]; then \
        pip install --no-cache-dir --index-url="$PIP_INDEX_URL" --trusted-host="$PIP_TRUSTED_HOST" -r requirements.txt; \
    else \
        pip install --no-cache-dir --index-url="$PIP_INDEX_URL" -r requirements.txt; \
    fi

# Copy application code
COPY app ./app

# Create non-root user for security
# Using UID 1000 for compatibility with common host user IDs
RUN groupadd -r -g 1000 appgroup && \
    useradd -r -u 1000 -g appgroup appuser

# Create data directory with proper ownership
RUN mkdir -p /app/data && \
    chown -R appuser:appgroup /app/data

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Health check - uses lightweight endpoint for quick checks
HEALTHCHECK --interval=30s --timeout=3s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health/quick', timeout=2)" || exit 1

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
