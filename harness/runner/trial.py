"""Trial lifecycle on the Docker host (run this ON the host, e.g. boomhauer).

  python3 -m harness.runner.trial build                      # build all images
  python3 -m harness.runner.trial lockdown                   # probe gate: env1s + env2s
  python3 -m harness.runner.trial run --env env2s --harness stub
  python3 -m harness.runner.trial run --env env2s --harness claude_code --minutes 15

For real harnesses, API keys come from ~/.config/sidechannel/keys/<role>.env
(ANTHROPIC_API_KEY=..., CODEX_API_KEY=...), with a separate key per role.

Lifecycle: render → `compose up --no-start` → docker cp the root-only trial files into each
agent → start pcap and stats collectors → start → wait → collect volumes and logs →
`compose down -v` → delete the rendered secrets.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path

from .compose import bridge_name, project_name, render
from .condition import HARNESS_PROVIDER, ROLES, RolePlan, TrialPlan, resolve_condition, role_subnet
from .probe_expectations import evaluate

REPO = Path(__file__).resolve().parents[2]
HARNESS = REPO / "harness"
ARTIFACTS = REPO / "artifacts" / "trials"
KEYS_DIR = Path.home() / ".config" / "sidechannel" / "keys"
START_DELAY = 20          # seconds between `compose start` and the agents' shared start time
IMAGES = [  # (tag, context dir, depends on)
    ("sc-base", "images/base"),
    ("sc-egress", "images/egress"),
    ("sc-tools", "images/tools"),
    ("sc-agent-claude_code", "images/claude_code"),
    ("sc-agent-openai_codex", "images/openai_codex"),
    ("sc-agent-pi", "images/pi"),
]


def sh(cmd, **kw):
    kw.setdefault("check", True)
    return subprocess.run(cmd, **kw)


def out(cmd) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()


def build(args):
    for tag, ctx in IMAGES:
        if args.only and tag not in args.only:
            continue
        build_args = []
        if tag == "sc-agent-claude_code" and args.claude_version:
            build_args = ["--build-arg", f"CLAUDE_CODE_VERSION={args.claude_version}"]
        if tag == "sc-agent-openai_codex" and args.codex_version:
            build_args = ["--build-arg", f"CODEX_VERSION={args.codex_version}"]
        print(f"== building {tag}", flush=True)
        sh(["docker", "build", "-t", tag, *build_args, str(HARNESS / ctx)])
    for tag in ("sc-agent-claude_code", "sc-agent-openai_codex", "sc-agent-pi"):
        try:
            print(tag, "->", out(["docker", "run", "--rm", "--entrypoint", "cat", tag, "/opt/sc/harness_version"]))
        except subprocess.CalledProcessError:
            pass


def host_ip() -> str:
    for ip in out(["hostname", "-I"]).split():
        if not ip.startswith(("172.", "10.", "192.168.")) and ":" not in ip:
            return ip
    return "1.1.1.1"


def load_keys() -> dict[str, dict[str, str]]:
    keys = {}
    for role in ROLES:
        p = KEYS_DIR / f"{role}.env"
        if p.exists():
            keys[role] = dict(line.strip().split("=", 1) for line in p.read_text().splitlines()
                              if "=" in line and not line.lstrip().startswith("#"))
    return keys


def trial_tar(files: dict[str, bytes]) -> bytes:
    """Tar of root-owned 0400 files, extracted by `docker cp -` into /run/trial."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.uid, ti.gid, ti.mtime = len(data), 0o400, 0, 0, int(time.time())
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def export_volume(volume: str, dest: Path, as_tgz: bool):
    """Copy a docker volume's contents out through a throwaway root container."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if as_tgz:
        with open(dest, "wb") as f:
            sh(["docker", "run", "--rm", "-v", f"{volume}:/v:ro", "sc-tools",
                "tar", "-C", "/v", "-czf", "-", "."], stdout=f)
    else:
        data = subprocess.run(["docker", "run", "--rm", "-v", f"{volume}:/v:ro", "sc-tools",
                               "tar", "-C", "/v", "-cf", "-", "."], check=True, capture_output=True).stdout
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tf:
            tf.extractall(dest, filter="data")


def lan_ip(name: str) -> str | None:
    r = subprocess.run(["docker", "inspect", "-f",
                        "{{range $k, $v := .NetworkSettings.Networks}}{{$k}}={{$v.IPAddress}} {{end}}", name],
                       capture_output=True, text=True)
    for pair in r.stdout.split():
        net, _, ip = pair.partition("=")
        if net.endswith("_lan") and ip:
            return ip
    return None


PCAP_IP = re.compile(r"^\S+ IP (\d+\.\d+\.\d+\.\d+)(?:\.(\d+))? > (\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?: (\w+)")
PCAP_ARP = re.compile(r"ARP, Request who-has (\d+\.\d+\.\d+\.\d+).*? tell (\d+\.\d+\.\d+\.\d+)")


def summarize_pcap(tdir: Path, name: str = "lan.pcap") -> dict:
    """IP flows and ARP requests on the trial bridge, from tcpdump's text output."""
    text = subprocess.run(["docker", "run", "--rm", "-v", f"{tdir}:/t:ro", "sc-tools",
                           "tcpdump", "-nn", "-tt", "-r", f"/t/{name}"],
                          capture_output=True, text=True).stdout
    flows, arps = {}, {}
    for line in text.splitlines():
        m = PCAP_IP.match(line)
        if m:
            src, sport, dst, dport, proto = m.groups()
            proto = "tcp" if proto == "Flags" else proto.lower()
            key = f"{src} > {dst}:{dport or '-'} {proto}"
            f = flows.setdefault(key, {"src": src, "dst": dst, "dport": dport, "proto": proto,
                                       "packets": 0, "first": None})
            f["packets"] += 1
            f["first"] = f["first"] or float(line.split()[0])
            continue
        m = PCAP_ARP.search(line)
        if m and m.group(1) != m.group(2):          # skip gratuitous ARP (a host announcing itself)
            k = f"{m.group(2)} asks {m.group(1)}"
            arps[k] = arps.get(k, 0) + 1
    return {"flows": sorted(flows.values(), key=lambda f: f["first"] or 0), "arp_requests": arps}


def container_state(name: str) -> str:
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", name], capture_output=True, text=True)
    return r.stdout.strip() or "missing"


def cleanup_project(project: str):
    """Remove containers, networks and volumes left by an interrupted trial (found by the
    compose project label)."""
    lab = f"label=com.docker.compose.project={project}"
    for kind, ls in (("container", ["docker", "ps", "-aq", "--filter", lab]),
                     ("network", ["docker", "network", "ls", "-q", "--filter", lab]),
                     ("volume", ["docker", "volume", "ls", "-q", "--filter", lab])):
        ids = subprocess.run(ls, capture_output=True, text=True).stdout.split()
        if ids:
            rm = ["docker", "rm", "-f"] if kind == "container" else ["docker", kind, "rm", "-f"]
            subprocess.run(rm + ids, capture_output=True)
    subprocess.run(["docker", "rm", "-f", f"{project}-cap"], capture_output=True)


def run_trial(cond: dict, trial_id: str | None = None, quiet: bool = False) -> Path:
    trial_id = trial_id or f"t{time.strftime('%m%d%H%M%S')}{uuid.uuid4().hex[:4]}"
    tdir = ARTIFACTS / trial_id
    if (tdir / "DONE").exists():
        return tdir
    cleanup_project(project_name(trial_id))
    if tdir.exists():                       # half-finished from an interrupted run: start over
        shutil.rmtree(tdir)
    registry = json.loads((HARNESS / "config" / "model_registry.json").read_text())
    prompts = {p.stem: p.read_text().strip() for p in (HARNESS / "config" / "prompts").glob("*.txt")}
    ladder = json.loads((HARNESS / "config" / "ladder.json").read_text())
    plan = resolve_condition(trial_id, cond, registry, prompts, keys=load_keys(), host_public_ip=host_ip(),
                             ladder=ladder)

    run_dir = tdir / ".run"
    run_dir.mkdir(parents=True, exist_ok=False)
    os.chmod(run_dir, 0o700)
    compose = render(plan)
    compose_path = run_dir / "compose.json"
    compose_path.write_text(json.dumps(compose, indent=1))
    project = project_name(trial_id)
    dc = ["docker", "compose", "-f", str(compose_path), "-p", project]
    say = (lambda *a: None) if quiet else (lambda *a: print(f"[{trial_id}]", *a, flush=True))

    meta = {"trial_id": trial_id, "condition": cond, "env_spec": plan.env_spec, "subnet": plan.subnet,
            "model": plan.model, "host": socket.gethostname(), "project": project,
            "truth": plan.truth, "ladder_version": ladder.get("version") if "task" in cond else None,
            "roles": [{"role": r.role, "uid": r.uid, "value": r.value, "image": r.image, "cmd": r.cmd,
                       "env_keys": sorted(r.env), "key_names": sorted(r.keys), "files": sorted(r.files),
                       "followups": r.followups, "resume": r.resume, "goal": r.goal} for r in plan.roles]}
    collectors = []
    try:
        say("creating", project)
        sh(dc + ["up", "--no-start", "--quiet-pull"], capture_output=quiet)
        start_at = time.time() + START_DELAY
        meta["start_at"] = start_at
        for rp in plan.roles:
            file_map = {dest: f"file{i}" for i, dest in enumerate(sorted(rp.files))}
            cfg = {"role": rp.role, "uid": rp.uid, "cmd": rp.cmd, "env": rp.env,
                   "start_at": start_at, "minutes": cond.get("minutes", 15),
                   "files": file_map, "followups": rp.followups, "resume": rp.resume, "goal": rp.goal}
            keys = "".join(f"{k}={v}\n" for k, v in rp.keys.items())
            sh(["docker", "cp", "-", f"{project}-{rp.role}-1:/run/trial"],
               input=trial_tar({"config.json": json.dumps(cfg).encode(), "prompt": rp.prompt,
                                "keys.env": keys.encode(),
                                **{name: rp.files[dest] for dest, name in file_map.items()}}),
               capture_output=True)

        # Host-side collectors: the agents can't see or touch these.
        pcap = open(tdir / "lan.pcap", "wb")
        collectors.append(("pcap", subprocess.Popen(
            ["docker", "run", "--rm", "--name", f"{project}-cap", "--net=host",
             "--cap-add", "NET_RAW", "--cap-add", "NET_ADMIN", "sc-tools",
             "tcpdump", "-i", bridge_name(trial_id), "-U", "-n", "-w", "-"],
            stdout=pcap, stderr=subprocess.DEVNULL), pcap))
        stats_f = open(tdir / "stats.jsonl", "w")
        names = [f"{project}-{r.role}-1" for r in plan.roles] + [f"{project}-proxy-1"]
        stats_p = subprocess.Popen(["docker", "stats", "--format", "{{json .}}", *names],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        collectors.append(("stats", stats_p, stats_f))
        import threading

        def pump():
            for line in stats_p.stdout:
                line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
                if line.startswith("{"):
                    stats_f.write(json.dumps({"t": round(time.time(), 3), **json.loads(line)}) + "\n")
                    stats_f.flush()
        threading.Thread(target=pump, daemon=True).start()

        say("starting; agents begin in", START_DELAY, "s")
        sh(dc + ["start"], capture_output=True)
        meta["ips"] = {svc: lan_ip(f"{project}-{svc}-1") for svc in [r.role for r in plan.roles] + ["proxy"]}
        deadline = start_at + float(cond.get("minutes", 15)) * 60 + 60
        agents = [f"{project}-{r.role}-1" for r in plan.roles]
        while time.time() < deadline:
            states = [container_state(a) for a in agents]
            if all(s in ("exited", "dead", "missing") for s in states):
                break
            time.sleep(2)
        meta["ended"] = time.time()
        sh(dc + ["stop", "-t", "10"], capture_output=True)

        say("collecting")
        for rp in plan.roles:
            export_volume(f"{project}_logs_{rp.role}", tdir / "agents" / rp.role, as_tgz=False)
        if plan.env_spec["shared_work"]:
            export_volume(f"{project}_work", tdir / "work.tgz", as_tgz=True)
        else:
            for rp in plan.roles:
                export_volume(f"{project}_work_{rp.role}", tdir / "agents" / rp.role / "work.tgz", as_tgz=True)
        if plan.env_spec["shared_tmp"]:
            export_volume(f"{project}_tmp", tdir / "tmp.tgz", as_tgz=True)
        with open(tdir / "egress.log", "w") as f:
            sh(["docker", "logs", "-t", f"{project}-proxy-1"], stdout=f, stderr=subprocess.STDOUT)
        meta["prompts"] = {r.role: r.prompt.decode(errors="replace") for r in plan.roles}
        meta["images"] = {r.image: out(["docker", "image", "inspect", "-f", "{{.Id}}", r.image]) for r in plan.roles}
    finally:
        subprocess.run(["docker", "stop", "-t", "2", f"{project}-cap"], capture_output=True)
        for name, proc, fh in collectors:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            fh.close()
        subprocess.run(dc + ["down", "-v", "--remove-orphans", "-t", "5"], capture_output=True)
        if compose_path.exists():
            shutil.copy(compose_path, tdir / "compose.json")   # no secrets in it
        shutil.rmtree(run_dir, ignore_errors=True)               # prompts + keys
        (tdir / "trial.json").write_text(json.dumps(meta, indent=1))
    try:
        (tdir / "lan_flows.json").write_text(json.dumps(summarize_pcap(tdir), indent=1))
    except Exception as e:  # never lose a trial over the summary
        say("pcap summary failed:", e)
    if all((tdir / "agents" / r.role / "end.json").exists() for r in plan.roles):
        (tdir / "DONE").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    else:
        raise RuntimeError("agents did not all finish (no end.json); trial left incomplete for retry")
    say("artifacts:", tdir)
    return tdir


# --- split-host envs (env3): one role per machine ----------------------------------------------
# The controller (runner/env3.py) runs `plan-split` on one host, relays the other role's half
# over SSH, then runs `run-half` on both hosts with the same start time. Each host writes
# role-suffixed files into artifacts/trials/<id>/; the controller merges them.

def plan_split(args) -> int:
    """Resolve a split-host trial once and write one half-plan per role (no API keys; each
    host adds its own at run time). Prompts and task files are base64 in the half files."""
    import base64
    cond = json.loads(sys.stdin.read())
    registry = json.loads((HARNESS / "config" / "model_registry.json").read_text())
    prompts = {p.stem: p.read_text().strip() for p in (HARNESS / "config" / "prompts").glob("*.txt")}
    ladder = json.loads((HARNESS / "config" / "ladder.json").read_text())
    plan = resolve_condition(args.trial_id, cond, registry, prompts, keys=load_keys(), host_public_ip=host_ip(),
                             ladder=ladder)
    if not plan.env_spec.get("split_hosts"):
        raise SystemExit(f"{cond['env']} is not a split-host env")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)
    b64 = lambda b: base64.b64encode(b).decode()
    for rp in plan.roles:
        half = {"trial_id": plan.trial_id, "condition": plan.condition, "env_spec": plan.env_spec,
                "subnet": plan.subnet, "role_subnet": role_subnet(plan, rp.role), "model": plan.model,
                "truth": plan.truth, "ladder_version": ladder.get("version") if "task" in cond else None,
                "role": {"role": rp.role, "uid": rp.uid, "peer": rp.peer, "peer_uid": rp.peer_uid,
                         "value": rp.value, "prompt": b64(rp.prompt), "cmd": rp.cmd, "env": rp.env,
                         "key_names": sorted(rp.keys), "image": rp.image,
                         # probe canaries are not secrets: carry them; real API keys never leave a host
                         "inline_keys": rp.keys if cond["harness"] not in HARNESS_PROVIDER else {},
                         "files": {d: b64(v) for d, v in rp.files.items()}, "followups": rp.followups,
                         "resume": rp.resume, "goal": rp.goal}}
        f = out_dir / f"{rp.role}.json"
        f.touch(mode=0o600)
        f.write_text(json.dumps(half))
    print(json.dumps({"trial_id": plan.trial_id, "model": plan.model, "roles": [r.role for r in plan.roles]}))
    return 0


def host_devices() -> dict:
    """This machine's camera and desktop-session sockets, plus the host GIDs that grant access."""
    run = Path(f"/run/user/{os.getuid()}")
    dev = {"video": ["/dev/video0"], "wayland": str(run / "wayland-0"), "pulse": str(run / "pulse" / "native")}
    for p in [*dev["video"], dev["wayland"], dev["pulse"]]:
        if not os.path.exists(p):
            raise RuntimeError(f"{p} missing: is the desktop session logged in (auto-login)?")
    dev["groups"] = sorted({os.stat(dev["video"][0]).st_gid, os.stat(dev["wayland"]).st_gid})
    return dev


def desktop_audio(levels: dict | None = None) -> dict:
    """Read (and, given levels, first set) the desktop session's default speaker and mic levels.
    Agents can change these through the sound server, so device envs reset them every trial."""
    env = {**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"}
    pa = lambda *a: subprocess.run(["pactl", *a], env=env, capture_output=True, text=True).stdout.strip()
    if levels:
        pa("set-sink-mute", "@DEFAULT_SINK@", "0"); pa("set-source-mute", "@DEFAULT_SOURCE@", "0")
        pa("set-sink-volume", "@DEFAULT_SINK@", levels["sink_volume"])
        pa("set-source-volume", "@DEFAULT_SOURCE@", levels["source_volume"])
    pct = lambda t: (re.findall(r"(\d+)%", t) or ["?"])[0] + "%"
    return {"sink": pct(pa("get-sink-volume", "@DEFAULT_SINK@")), "sink_mute": pa("get-sink-mute", "@DEFAULT_SINK@"),
            "source": pct(pa("get-source-volume", "@DEFAULT_SOURCE@")), "source_mute": pa("get-source-mute", "@DEFAULT_SOURCE@")}


def run_half(args) -> int:
    import base64
    half = json.loads(Path(args.half).read_text())
    r, trial_id = half["role"], half["trial_id"]
    role = r["role"]
    tdir = ARTIFACTS / trial_id
    if (tdir / f"DONE-{role}").exists():
        return 0
    project = project_name(trial_id)
    cleanup_project(project)
    shutil.rmtree(tdir / "agents" / role, ignore_errors=True)
    tdir.mkdir(parents=True, exist_ok=True)
    cond = half["condition"]
    own = {**load_keys().get(role, {}), **r.get("inline_keys", {})}
    missing = [k for k in r["key_names"] if not own.get(k)]
    if missing:
        raise SystemExit(f"missing {missing} in {KEYS_DIR}/{role}.env")
    rp = RolePlan(role=role, uid=r["uid"], peer=r["peer"], peer_uid=r["peer_uid"], value=r["value"],
                  prompt=base64.b64decode(r["prompt"]), cmd=r["cmd"], env=r["env"],
                  keys={k: own[k] for k in r["key_names"]}, image=r["image"],
                  files={d: base64.b64decode(v) for d, v in r["files"].items()},
                  followups=r["followups"], resume=r["resume"], goal=r["goal"])
    plan = TrialPlan(trial_id, cond, half["env_spec"], half["subnet"], half["model"], roles=[rp], truth=half["truth"])
    dev = host_devices() if plan.env_spec["devices"] else None
    audio = None
    if dev:
        levels = json.loads((HARNESS / "config" / "lab.json").read_text())["audio"]
        audio = {"found_before": desktop_audio(), "set": desktop_audio(levels)}

    run_dir = tdir / f".run-{role}"
    run_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(run_dir, 0o700)
    compose_path = run_dir / "compose.json"
    compose_path.write_text(json.dumps(render(plan, [role], dev), indent=1))
    dc = ["docker", "compose", "-f", str(compose_path), "-p", project]
    agent, proxy = f"{project}-{role}-1", f"{project}-proxy-1"
    start_at = float(args.start_at)
    meta = {"role": role, "host": socket.gethostname(), "project": project, "start_at": start_at,
            "role_subnet": half["role_subnet"], "devices": dev, "value": rp.value, "image": rp.image,
            "cmd": rp.cmd, "env_keys": sorted(rp.env), "key_names": sorted(rp.keys), "files": sorted(rp.files),
            "followups": rp.followups, "resume": rp.resume, "goal": rp.goal, "uid": rp.uid,
            "prompt": rp.prompt.decode(errors="replace"), "late_start_s": None, "audio": audio}
    collectors = []
    try:
        print(f"[{trial_id}/{role}] creating {project}", flush=True)
        sh(dc + ["up", "--no-start", "--quiet-pull"], capture_output=True)
        file_map = {dest: f"file{i}" for i, dest in enumerate(sorted(rp.files))}
        cfg = {"role": role, "uid": rp.uid, "cmd": rp.cmd, "env": rp.env, "start_at": start_at,
               "minutes": cond.get("minutes", 15), "files": file_map, "followups": rp.followups,
               "resume": rp.resume, "goal": rp.goal, "groups": dev["groups"] if dev else []}
        keys = "".join(f"{k}={v}\n" for k, v in rp.keys.items())
        sh(["docker", "cp", "-", f"{agent}:/run/trial"],
           input=trial_tar({"config.json": json.dumps(cfg).encode(), "prompt": rp.prompt, "keys.env": keys.encode(),
                            **{name: rp.files[dest] for dest, name in file_map.items()}}), capture_output=True)
        pcap = open(tdir / f"lan-{role}.pcap", "wb")
        collectors.append(("pcap", subprocess.Popen(
            ["docker", "run", "--rm", "--name", f"{project}-cap", "--net=host", "--cap-add", "NET_RAW",
             "--cap-add", "NET_ADMIN", "sc-tools", "tcpdump", "-i", bridge_name(trial_id), "-U", "-n", "-w", "-"],
            stdout=pcap, stderr=subprocess.DEVNULL), pcap))
        stats_f = open(tdir / f"stats-{role}.jsonl", "w")
        stats_p = subprocess.Popen(["docker", "stats", "--format", "{{json .}}", agent, proxy],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        collectors.append(("stats", stats_p, stats_f))
        import threading

        def pump():
            for line in stats_p.stdout:
                line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", line).strip()
                if line.startswith("{"):
                    stats_f.write(json.dumps({"t": round(time.time(), 3), **json.loads(line)}) + "\n")
                    stats_f.flush()
        threading.Thread(target=pump, daemon=True).start()

        sh(dc + ["start"], capture_output=True)
        meta["late_start_s"] = round(time.time() - start_at, 2)     # > 0 means this host missed the start
        meta["ips"] = {role: lan_ip(agent), f"proxy-{role}": lan_ip(proxy)}
        print(f"[{trial_id}/{role}] started; agent begins in {start_at - time.time():.0f}s", flush=True)
        deadline = start_at + float(cond.get("minutes", 15)) * 60 + 60
        while time.time() < deadline and container_state(agent) not in ("exited", "dead", "missing"):
            time.sleep(2)
        meta["ended"] = time.time()
        sh(dc + ["stop", "-t", "10"], capture_output=True)
        if audio:
            audio["after"] = desktop_audio()
        export_volume(f"{project}_logs_{role}", tdir / "agents" / role, as_tgz=False)
        export_volume(f"{project}_work_{role}", tdir / "agents" / role / "work.tgz", as_tgz=True)
        with open(tdir / f"egress-{role}.log", "w") as f:
            sh(["docker", "logs", "-t", proxy], stdout=f, stderr=subprocess.STDOUT)
        meta["image_id"] = out(["docker", "image", "inspect", "-f", "{{.Id}}", rp.image])
    finally:
        subprocess.run(["docker", "stop", "-t", "2", f"{project}-cap"], capture_output=True)
        for name, proc, fh in collectors:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            fh.close()
        subprocess.run(dc + ["down", "-v", "--remove-orphans", "-t", "5"], capture_output=True)
        if compose_path.exists():
            shutil.copy(compose_path, tdir / f"compose-{role}.json")   # no secrets in it
        shutil.rmtree(run_dir, ignore_errors=True)
        (tdir / f"half-{role}.json").write_text(json.dumps(meta, indent=1))
    try:
        (tdir / f"lan_flows-{role}.json").write_text(json.dumps(summarize_pcap(tdir, f"lan-{role}.pcap"), indent=1))
    except Exception as e:
        print(f"[{trial_id}/{role}] pcap summary failed: {e}", flush=True)
    if not (tdir / "agents" / role / "end.json").exists():
        raise SystemExit(f"[{trial_id}/{role}] agent did not finish (no end.json)")
    (tdir / f"DONE-{role}").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    print(f"[{trial_id}/{role}] done", flush=True)
    return 0


def read_events(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def lockdown(args) -> int:
    failures = 0
    for env in args.envs:
        tdir = run_trial({"env": env, "harness": "probe", "minutes": 2})
        report = {}
        for role in ROLES:
            obs = read_events(tdir / "agents" / role / "events.jsonl")
            rows = evaluate(env, obs)
            report[role] = [{"check": c, "expected": e, "observed": o, "ok": ok} for c, e, o, ok in rows]
            bad = [r for r in rows if not r[3]]
            failures += len(bad)
            print(f"\n{env} / {role}: {len(rows) - len(bad)}/{len(rows)} checks pass")
            for c, e, o, ok in rows:
                print(f"  {'ok ' if ok else 'FAIL'} {c:<45} expected={e!s:<6} observed={o}")
        (tdir / "probe_report.json").write_text(json.dumps(report, indent=1))
    print("\nLOCKDOWN GATE:", "PASS" if failures == 0 else f"FAIL ({failures} mismatches)")
    return 1 if failures else 0


def sweep(args) -> int:
    """Run every condition in a sweep spec (JSON), `parallel` trials at a time. Trials whose
    directory already exists are skipped, so an interrupted sweep can just be re-run."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .condition import expand_sweep
    spec = json.loads(Path(args.spec).read_text())
    conds = expand_sweep(spec)
    todo = [(t, c) for t, c in conds if not (ARTIFACTS / t / "DONE").exists()]
    print(f"sweep {spec['name']}: {len(conds)} trials, {len(todo)} to run, parallel={args.parallel}", flush=True)
    if args.dry_run:
        for t, c in todo[:10]:
            print(" ", t, c)
        return 0
    def attempt(t, c):
        last = None
        for i in range(3):                  # retries get a different subnet (collision-safe)
            try:
                return run_trial({**c, **({"subnet_salt": f"retry{i}"} if i else {})}, t, True)
            except Exception as e:
                last = e
                print(f"  attempt {i + 1} failed for {t}: {type(e).__name__}: {str(e)[:200]}", flush=True)
                time.sleep(5)
        raise last

    ok = fail = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futs = {ex.submit(attempt, t, c): t for t, c in todo}
        for f in as_completed(futs):
            t = futs[f]
            try:
                f.result(); ok += 1
                status = "done"
            except Exception as e:
                fail += 1
                status = f"FAILED {type(e).__name__}: {str(e)[:200]}"
            print(f"[{ok + fail}/{len(todo)} {time.strftime('%H:%M:%S')} +{(time.time() - t0) / 60:.0f}m] {status} {t}", flush=True)
    print(f"sweep finished: {ok} ok, {fail} failed", flush=True)
    return 1 if fail else 0


def main():
    ap = argparse.ArgumentParser(prog="trial")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--only", nargs="*")
    b.add_argument("--claude-version"); b.add_argument("--codex-version")
    lk = sub.add_parser("lockdown"); lk.add_argument("--envs", nargs="+", default=["env1s", "env2s"])
    sw = sub.add_parser("sweep"); sw.add_argument("spec"); sw.add_argument("--parallel", type=int, default=4)
    sw.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run")
    r.add_argument("--env", required=True); r.add_argument("--harness", required=True)
    r.add_argument("--minutes", type=float, default=15); r.add_argument("--model-generation", default="current")
    r.add_argument("--prompt-id", default="placeholder"); r.add_argument("--seed", type=int)
    r.add_argument("--trial-id"); r.add_argument("--budget-usd", type=float, default=5.0)
    r.add_argument("--task"); r.add_argument("--rung", type=int); r.add_argument("--hatch")
    ps = sub.add_parser("plan-split", help="(split-host envs) resolve a trial; condition JSON on stdin")
    ps.add_argument("--trial-id", required=True); ps.add_argument("--out", required=True)
    rh = sub.add_parser("run-half", help="(split-host envs) run this host's role")
    rh.add_argument("--half", required=True); rh.add_argument("--start-at", required=True)
    a = ap.parse_args()
    if a.cmd == "plan-split":
        sys.exit(plan_split(a))
    if a.cmd == "run-half":
        sys.exit(run_half(a))
    if a.cmd == "build":
        build(a)
    elif a.cmd == "lockdown":
        sys.exit(lockdown(a))
    elif a.cmd == "sweep":
        sys.exit(sweep(a))
    else:
        cond = {"env": a.env, "harness": a.harness, "minutes": a.minutes,
                "model_generation": a.model_generation, "prompt_id": a.prompt_id,
                "budget_usd": a.budget_usd}
        if a.seed is not None:
            cond["seed"] = a.seed
        if a.task:
            cond.update({"task": a.task, "rung": a.rung or 1, "hatch": a.hatch or "none"})
            cond.pop("prompt_id")
        run_trial(cond, a.trial_id)


if __name__ == "__main__":
    main()
