FROM public.ecr.aws/docker/library/python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ ./app/
COPY schemas/ ./schemas/
COPY playbooks/ ./playbooks/
COPY scripts* ./scripts/
COPY pyproject.toml ./
RUN mkdir -p /data && chmod -R a+rX /app/schemas && chown app:app /data
ENV APP_MODE=demo DB_PATH=/data/forkfix.db HOST=0.0.0.0 PORT=8000
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status==200 else 1)"
CMD ["sh", "-c", "python -m app.preflight && exec python -m app"]
