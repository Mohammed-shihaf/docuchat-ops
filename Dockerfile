# Multi-stage build: dependencies are installed in a builder, the runtime image stays small.
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HOME=/app/.cache
WORKDIR /app
COPY --from=builder /install /usr/local
COPY app/ app/
COPY ingest.py .
# Run as a non-root user (a common Kubernetes security requirement).
RUN useradd --uid 10001 --create-home appuser && mkdir -p /app/.cache && chown -R appuser /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
