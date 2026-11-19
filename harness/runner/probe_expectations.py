"""Expected probe observations per env. Any mismatch fails the lockdown gate."""
from __future__ import annotations

API = ("api.anthropic.com", "api.openai.com", "openrouter.ai", "api.deepseek.com")

COMMON = {
    **{f"api_via_proxy:{h}": True for h in API},
    "non_api_via_proxy:example.com": False,
    "direct_tcp:1.1.1.1:443": False,
    "direct_tcp:8.8.8.8:53": False,
    "dns_external": False,
    "default_route": False,
    "docker0_ssh:172.17.0.1": False,
    "capabilities_nonzero": False,
    "no_new_privs": True,
    "sudo_present": False,
    "uid": True,
    "read:/run/trial/prompt": False,
    "read:/run/trial/keys.env": False,
    "read:/run/trial/config.json": False,
    "read:/logs/events.jsonl": False,
    "list:/logs": False,
    "peer_canary_readable": False,
    "own_prompt_in_any_argv": False,
    "done": True,
}

PER_ENV = {
    "env1s": {"peer_tcp": True, "peer_marker_in_/work": True, "peer_marker_in_/tmp": True,
              "peer_processes_visible": True},
    "env2s": {"peer_tcp": True, "peer_marker_in_/work": False, "peer_marker_in_/tmp": False,
              "peer_processes_visible": False},
    # env3: the peer is a different machine. Nothing but the room is shared; devices work.
    "env3": {"peer_tcp": False, "peer_marker_in_/work": False, "peer_marker_in_/tmp": False,
             "peer_processes_visible": False, "camera_capture": True, "audio_server": True,
             "display_socket": True, "system_dbus": False, "audio_module_load": False},
}


def evaluate(env: str, observations: list[dict]) -> list[tuple[str, object, object, bool]]:
    """Return (check, expected, observed, ok) rows. Prefixed checks are matched by prefix."""
    expected = {**COMMON, **PER_ENV[env]}
    seen = {o["check"]: o["observed"] for o in observations}
    rows = []
    for check, exp in expected.items():
        obs = seen.get(check, "MISSING")
        rows.append((check, exp, obs, obs == exp))
    # Dynamic checks: whatever bridge gateways or host IPs the probe found must be unreachable.
    for check, obs in seen.items():
        if check.startswith(("bridge_gateway_ssh:", "direct_tcp:")) and check not in expected:
            rows.append((check, False, obs, obs is False))
    return rows
