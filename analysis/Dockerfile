# Judge runtime (host-side; never given to agents). Run via scripts/judge.sh.
FROM python:3.12-slim
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
