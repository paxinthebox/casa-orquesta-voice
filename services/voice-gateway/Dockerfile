FROM python:3.11-slim
WORKDIR /app
COPY services/voice-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/voice-gateway/ .
COPY services/_shared/ /app/_shared/
ENV PYTHONPATH="/app:/app/_shared"
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]