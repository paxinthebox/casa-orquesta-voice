FROM python:3.11-slim
WORKDIR /app

# Copy requirements first for layer caching
COPY services/orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy orchestrator source
COPY services/orchestrator/ .

# Copy shared modules used by the orchestrator (listing_filters, location_filters, etc.)
COPY services/_shared/ /app/_shared/

ENV PYTHONPATH="/app:/app/_shared"

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
