# Stdlib only, so no requirements file. Used for the Stage L3 local stack;
# Stage D installs agent.py directly on the host instead (see
# envoy/install/user_data.sh.tftpl) since there's no container runtime
# dependency to isolate there.
FROM python:3.12-slim@sha256:2b4f19dae3a777dfc3b76730bda1e82e1f66ab2a2686fa93ca78edbfb4f04ffe
COPY agent.py /agent.py
ENTRYPOINT ["python", "/agent.py"]
