# Google Cloud Run — listen on PORT (set by the platform at runtime).
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV COREX_SALES_SERVICE_ENV=production

RUN adduser --disabled-password --gecos "" appuser

COPY requirements.txt requirements-service.txt ./
RUN pip install --no-cache-dir -r requirements-service.txt

COPY src ./src
COPY public ./public
COPY db ./db
COPY db.py finder_places.py lead_qualifier.py run_lead_pipeline.py run_leads_discovery.py ./
COPY cities.csv cities_canada.csv ./
COPY tests/fixtures ./tests/fixtures

RUN mkdir -p /app/runs && chown -R appuser:appuser /app/runs

USER appuser

EXPOSE 8080

CMD ["sh", "-c", "uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT}"]
