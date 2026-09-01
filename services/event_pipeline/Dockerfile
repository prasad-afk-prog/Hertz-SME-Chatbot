# Track A — Event & Trigger Pipeline service image (M15 §3 containerised services).
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install deps first for better layer caching.
COPY requirements.txt ./requirements.txt
COPY services/requirements.txt ./services/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt -r services/requirements.txt

# App code (the generator carries the shared contract models the services import).
COPY generator ./generator
COPY services ./services

EXPOSE 8000
CMD ["uvicorn", "services.event_pipeline.main:app", "--host", "0.0.0.0", "--port", "8000"]
