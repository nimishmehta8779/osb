# Build context is the repo root, because the broker imports services.common:
#   docker build -f services/broker/Dockerfile -t osb/broker:dev .
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe

WORKDIR /app
COPY services/broker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY services/common /app/services/common
COPY services/broker/app /app/services/broker/app

ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["uvicorn", "services.broker.app.entry:app", "--host", "0.0.0.0", "--port", "8000"]
