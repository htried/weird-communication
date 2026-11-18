"""Render a TrialPlan into a docker compose file (JSON, which compose accepts as YAML).

Names are deliberately neutral (project ws<id>, services node1/node2/proxy, network lan),
so reverse DNS and `docker`-style hostnames don't announce an experiment.
"""
from __future__ import annotations

from .condition import TrialPlan, role_subnet

AGENT_CAPS = ["SETUID", "SETGID", "KILL"]   # entry wrapper only: drop to UID, sweep leftovers
LIMITS = {"cpus": 4.0, "mem_limit": "8g", "pids_limit": 1024}


def _tag(trial_id: str) -> str:
    # Hash-based, so parallel trials never share a compose project or bridge name.
    # Truncated trial-id prefixes or suffixes collided across a sweep.
    import hashlib
    return hashlib.sha1(trial_id.encode()).hexdigest()[:11]


def project_name(trial_id: str) -> str:
    return "ws" + _tag(trial_id)


def bridge_name(trial_id: str) -> str:
    return "sc" + _tag(trial_id)          # 13 chars (Linux limit is 15)


def render(plan: TrialPlan, roles: list[str] | None = None, host: dict | None = None) -> dict:
    """Compose file for this host. `roles` limits it to some roles (split-host envs run one role
    per machine). `host` describes the machine's devices for device envs:
    {"video": ["/dev/video0"], "wayland": "<socket>", "pulse": "<socket>"}."""
    spec = plan.env_spec
    roles = roles or [r.role for r in plan.roles]
    if spec["devices"] and not host:
        raise ValueError("device env needs a host device description")
    services: dict = {
        "proxy": {
            "image": "sc-egress",
            "networks": {"lan": {}, "uplink": {}},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "restart": "no",
        }
    }
    volumes: dict = {}

    if spec["shared_pid_ipc"]:
        # Neutral namespace owner, so neither agent's exit tears down the other's processes.
        services["init"] = {
            "image": "sc-base", "entrypoint": ["/usr/bin/tini", "--", "sleep", "infinity"],
            "network_mode": "none", "ipc": "shareable",
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        }
    if spec["shared_work"]:
        volumes["work"] = {}
    if spec["shared_tmp"]:
        volumes["tmp"] = {}

    for rp in [r for r in plan.roles if r.role in roles]:
        svc = {
            "image": rp.image,
            "hostname": rp.role,
            "networks": {"lan": {}},
            "cap_drop": ["ALL"],
            "cap_add": AGENT_CAPS,
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": [f"/home/agent:uid={rp.uid},gid={rp.uid},mode=0700"],
            "volumes": [f"logs_{rp.role}:/logs"],
            "restart": "no",
            **LIMITS,
        }
        if spec["shared_work"]:
            svc["volumes"].append("work:/work")
        else:
            volumes[f"work_{rp.role}"] = {}
            svc["volumes"].append(f"work_{rp.role}:/work")
        if spec["shared_tmp"]:
            svc["volumes"].append("tmp:/tmp")
        else:
            svc["tmpfs"].append("/tmp:mode=1777")
        if spec["shared_pid_ipc"]:
            svc["pid"] = "service:init"
            svc["ipc"] = "service:init"
            svc["depends_on"] = ["init"]
        if spec["devices"]:
            # Camera device, plus the desktop session's display and sound sockets in a private
            # runtime dir. Access comes from supplementary groups set by the entry wrapper
            # (config "groups"), not from capabilities.
            svc["devices"] = [f"{d}:{d}" for d in host["video"]]
            svc["tmpfs"].append(f"/run/xdg:uid={rp.uid},gid={rp.uid},mode=0700")
            svc["volumes"] += [f"{host['wayland']}:/run/xdg/wayland-0", f"{host['pulse']}:/run/xdg/pulse/native"]
        services[rp.role] = svc
        volumes[f"logs_{rp.role}"] = {}

    return {
        "name": project_name(plan.trial_id),
        "services": services,
        "networks": {
            "lan": {
                "internal": True,
                "driver": "bridge",
                "driver_opts": {
                    "com.docker.network.bridge.name": bridge_name(plan.trial_id),
                    # No IP on the host side of the bridge: agents can't reach host services.
                    "com.docker.network.bridge.inhibit_ipv4": "true",
                },
                "ipam": {"config": [{"subnet": role_subnet(plan, roles[0]) if len(roles) == 1 else plan.subnet}]},
            },
            "uplink": {"driver": "bridge"},
        },
        "volumes": volumes,
    }
