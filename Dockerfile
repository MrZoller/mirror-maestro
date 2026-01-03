FROM python:3.11-slim

WORKDIR /app

# Install system dependencies (including PostgreSQL client for pg_dump)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

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
