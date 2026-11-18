"""Mask API keys and credentials in anything that leaves the host (judge prompts) or is kept
as an artifact. Agents can and do run `env`/`printenv`, so their own keys end up in transcripts.

Two layers: exact values of every key in the agent key files (SC_CONFIG_DIR/keys/*.env and
judge.env), plus generic credential patterns.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

CFG = Path(os.environ.get("SC_CONFIG_DIR", Path.home() / ".config" / "sidechannel"))
PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),                 # Anthropic
    re.compile(r"sk-(?:proj-|svcacct-|or-v1-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / OpenRouter
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),              # AWS access key id
    re.compile(r"(?i)((?:aws_secret_access_key|aws_session_token|aws_bearer_token_bedrock)\s*[=:]\s*)\S+"),
    re.compile(r"\bABSK[A-Za-z0-9+/=]{20,}"),                  # Bedrock API key (bearer)
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{24,}"),
]


def _known_values() -> list[str]:
    vals = []
    for f in list((CFG / "keys").glob("*.env")) + [CFG / "judge.env"]:
        try:
            for line in f.read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    v = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if len(v) >= 12:
                        vals.append(v)
        except OSError:
            pass
    return sorted(set(vals), key=len, reverse=True)


_VALUES = None


def redact(text: str) -> str:
    global _VALUES
    if _VALUES is None:
        _VALUES = _known_values()
    for v in _VALUES:
        if v in text:
            text = text.replace(v, "[REDACTED-KEY]")
    for rx in PATTERNS:
        text = rx.sub(lambda m: (m.group(1) if m.lastindex else "") + "[REDACTED-KEY]", text)
    return text
