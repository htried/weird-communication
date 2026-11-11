"""Render a TrialPlan into a docker compose file (JSON, which compose accepts as YAML).

Names are deliberately neutral (project ws<id>, services node1/node2/proxy, network lan),
so reverse DNS and `docker`-style hostnames don't announce an experiment.
"""
from __future__ import annotations

from .condition import TrialPlan

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


def render(plan: TrialPlan) -> dict:
    spec = plan.env_spec
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

    for rp in plan.roles:
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
                "ipam": {"config": [{"subnet": plan.subnet}]},
            },
            "uplink": {"driver": "bridge"},
        },
        "volumes": volumes,
    }
