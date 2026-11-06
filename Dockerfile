FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml ./
COPY assistant ./assistant
RUN pip install . && rm -rf /app/assistant

# Config utilisateur montée ici (config.yaml), voir docker-compose.yml
VOLUME ["/config"]
EXPOSE 8080

# 99:100 = nobody:users, l'utilisateur habituel des appdata Unraid
USER 99:100
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

CMD ["python", "-m", "assistant"]
