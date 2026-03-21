FROM python:3.11-slim

WORKDIR /app

# System deps for PostGIS client, GDAL bindings
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY infra/api/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY castline/ /app/castline/

EXPOSE 8000

CMD ["uvicorn", "castline.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
