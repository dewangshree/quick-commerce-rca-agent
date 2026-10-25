# Backend API image (FastAPI + LangGraph + MCP). Ollama is NOT bundled; point OLLAMA_BASE_URL at it.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    API_HOST=0.0.0.0 API_PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY qc_rca/ qc_rca/
COPY migrations/ migrations/
COPY alembic.ini main.py ./
COPY knowledge_base/ knowledge_base/
COPY data/ data/
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh && useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
