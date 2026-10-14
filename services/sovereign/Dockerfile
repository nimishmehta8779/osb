# Sovereign control plane (pinned). Base image pinned by digest (amd64).
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe

RUN pip install --no-cache-dir "sovereign==0.32.12"

WORKDIR /app
COPY config/sovereign.yaml /app/config/sovereign.yaml
COPY templates /app/templates

ENV SOVEREIGN_CONFIG=file:///app/config/sovereign.yaml
EXPOSE 8080
CMD ["sovereign"]
