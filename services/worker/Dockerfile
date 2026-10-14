# Build context is the repo root, because the worker imports services.common:
#   docker build -f services/worker/Dockerfile -t osb/worker:dev .
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe

WORKDIR /app
COPY services/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/common /app/services/common
COPY services/worker/app /app/services/worker/app

ENV PYTHONPATH=/app
CMD ["python", "-m", "services.worker.app.entry"]
