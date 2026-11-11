# Base image for every agent container. It provides the "media" toolkit from design §4.
# There is deliberately no sudo here. The entry wrapper runs as root, then drops to the
# agent UID.
FROM debian:bookworm-slim

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      tini ca-certificates curl openssl python3 python3-numpy \
      ffmpeg alsa-utils pulseaudio-utils espeak-ng v4l-utils \
      iproute2 iputils-ping netcat-openbsd procps psmisc util-linux less file \
    && rm -rf /var/lib/apt/lists/* \
    && (command -v sudo && exit 1 || true)

# Agent UIDs are high, so they never alias real users on the host. Each container runs one
# role. Both users exist in every image so the shared PID namespace in env1 renders names.
RUN groupadd -g 20001 node1 && useradd -u 20001 -g 20001 -G audio,video -d /home/agent -M -s /bin/bash node1 \
 && groupadd -g 20002 node2 && useradd -u 20002 -g 20002 -G audio,video -d /home/agent -M -s /bin/bash node2

# /run/trial is filled by the runner (docker cp) before start, and /logs is a per-agent
# volume. Both are root-only. /work is world-writable, so env1's shared volume works for
# both UIDs.
RUN mkdir -p /run/trial /logs /work /home/agent /opt/sc \
 && chmod 0700 /run/trial /logs && chmod 1777 /work

COPY entry.py probe.py /opt/sc/
# The agent can't list /opt/sc or read the wrapper or version file; the smoke trials showed
# agents reading entry.py's comments, which describe the setup. probe.py must stay readable,
# because lockdown probes run it as the agent UID.
RUN chmod 0711 /opt/sc && chmod 0700 /opt/sc/entry.py && chmod 0755 /opt/sc/probe.py

WORKDIR /work
ENTRYPOINT ["/usr/bin/tini", "-s", "-g", "--", "/usr/bin/python3", "/opt/sc/entry.py"]
