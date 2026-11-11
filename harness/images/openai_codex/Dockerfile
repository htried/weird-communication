FROM sc-base:latest

# Full Codex package from the GitHub releases, not the bare binary. The package bundles
# codex-code-mode-host and codex-exec-server. Without them Codex reports "Code Mode is
# unavailable ... will fail closed" and can't run commands at all.
# Pin with --build-arg CODEX_VERSION=rust-vX.Y.Z. "latest" uses the newest release.
ARG CODEX_VERSION=latest
RUN set -eu; \
    case "$(uname -m)" in x86_64) a=x86_64;; aarch64) a=aarch64;; esac; \
    if [ "$CODEX_VERSION" = latest ]; then base=https://github.com/openai/codex/releases/latest/download; \
    else base="https://github.com/openai/codex/releases/download/${CODEX_VERSION}"; fi; \
    mkdir -p /opt/codex; \
    curl -fsSL "${base}/codex-package-${a}-unknown-linux-musl.tar.gz" | tar -xz -C /opt/codex; \
    chown -R root:root /opt/codex; chmod -R a+rX,go-w /opt/codex; \
    for b in codex codex-code-mode-host codex-exec-server; do ln -sf /opt/codex/bin/$b /usr/local/bin/$b; done; \
    /usr/local/bin/codex --version | tee /opt/sc/harness_version; chmod 0600 /opt/sc/harness_version
