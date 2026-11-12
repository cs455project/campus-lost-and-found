# Backend image: FastAPI API (and, with another command, the worker).
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# System libraries the ID-card OCR (OpenCV / ONNX Runtime) needs on Linux.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 libxrender1 libxcb1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p uploads .cache/models .cache/emails

EXPOSE 8000

# On every start: apply new migrations, add starting data (safe to repeat),
# then serve the API. --proxy-headers: it sits behind Caddy (HTTPS).
# --workers 2: one Python process per CPU core of the t3.small (one process
# can only use one core). See testing/perf/README.md.
CMD ["sh", "-c", "alembic upgrade head && python -m app.seed && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers --forwarded-allow-ips='*'"]
