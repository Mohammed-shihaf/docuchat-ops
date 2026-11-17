# Multi-stage build: dependencies are installed in a builder, the runtime image stays small.
FROM python:3.13-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 EMBED_CACHE_DIR=/app/models HF_HUB_OFFLINE=0
WORKDIR /app
COPY --from=builder /install /usr/local
# Bake the embedding model into the image: startup never depends on downloading from the internet.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')"
ENV HF_HUB_OFFLINE=1
COPY app/ app/
# Run as a non-root user (a common Kubernetes security requirement).
RUN useradd --uid 10001 --create-home appuser && chown -R appuser /app
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz')"
# --no-access-log: request URLs can contain file names, which are customer data.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
