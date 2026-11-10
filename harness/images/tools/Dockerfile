# Host-side helper (never given to agents): packet capture and volume export.
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends tcpdump tar gzip \
    && rm -rf /var/lib/apt/lists/*
