# The API as a container. Built by docker-compose.yml and by CI; other services
# call it at http://api:8000.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so editing the code does not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
# The golden dataset and eval runner ship in the image, so CI scores exactly
# the code and dependencies that would be deployed:
#   docker compose run --rm api python -m evals.run_eval --gate
COPY evals ./evals

# Run as an ordinary user; /app/data is where a SQLite database would go.
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
