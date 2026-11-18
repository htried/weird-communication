FROM sc-base:latest

# Node 24 from the official tarball (not Debian's Node 18), then the pi coding agent from npm.
# Node 24 honours HTTPS_PROXY for fetch when NODE_USE_ENV_PROXY=1 (set in harness_env), which
# matters because the lockdown proxy is the only way out.
ARG PI_VERSION=0.73.1
RUN set -eu; \
    case "$(uname -m)" in x86_64) a=x64;; aarch64) a=arm64;; esac; \
    f=$(curl -fsSL https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt | grep -oE "node-v24[^ ]*-linux-${a}\.tar\.gz" | head -1); \
    curl -fsSL "https://nodejs.org/dist/latest-v24.x/${f}" | tar -xz -C /usr/local --strip-components=1; \
    node --version; \
    npm install -g --no-fund --no-audit "@mariozechner/pi-coding-agent@${PI_VERSION}"; \
    pi --version 2>&1 | tee /opt/sc/harness_version || echo "pi ${PI_VERSION}" > /opt/sc/harness_version; \
    chmod 0600 /opt/sc/harness_version
