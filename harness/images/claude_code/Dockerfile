FROM sc-base:latest

# Pin with --build-arg CLAUDE_CODE_VERSION=x.y.z. "latest" resolves at build time and is
# recorded in the image label.
ARG CLAUDE_CODE_VERSION=latest
RUN set -eu; \
    v="$CLAUDE_CODE_VERSION"; \
    if [ "$v" = latest ]; then v="$(curl -fsSL https://downloads.claude.ai/claude-code-releases/latest)"; fi; \
    case "$(uname -m)" in x86_64) p=linux-x64;; aarch64) p=linux-arm64;; esac; \
    curl -fsSL "https://downloads.claude.ai/claude-code-releases/${v}/${p}/claude" -o /usr/local/bin/claude; \
    chmod 0755 /usr/local/bin/claude; \
    echo "$v" > /opt/sc/harness_version; \
    /usr/local/bin/claude --version
