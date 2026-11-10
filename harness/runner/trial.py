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
from .condition import ROLES, resolve_condition
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
    for tag in ("sc-agent-claude_code", "sc-agent-openai_codex"):
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


def summarize_pcap(tdir: Path) -> dict:
    """IP flows and ARP requests on the trial bridge, from tcpdump's text output."""
    text = subprocess.run(["docker", "run", "--rm", "-v", f"{tdir}:/t:ro", "sc-tools",
                           "tcpdump", "-nn", "-tt", "-r", "/t/lan.pcap"],
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


def run_trial(cond: dict, trial_id: str | None = None, quiet: bool = False) -> Path:
    trial_id = trial_id or f"t{time.strftime('%m%d%H%M%S')}{uuid.uuid4().hex[:4]}"
    registry = json.loads((HARNESS / "config" / "model_registry.json").read_text())
    prompts = {p.stem: p.read_text().strip() for p in (HARNESS / "config" / "prompts").glob("*.txt")}
    plan = resolve_condition(trial_id, cond, registry, prompts, keys=load_keys(), host_public_ip=host_ip())

    tdir = ARTIFACTS / trial_id
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
            "roles": [{"role": r.role, "uid": r.uid, "value": r.value, "image": r.image, "cmd": r.cmd,
                       "env_keys": sorted(r.env), "key_names": sorted(r.keys)} for r in plan.roles]}
    collectors = []
    try:
        say("creating", project)
        sh(dc + ["up", "--no-start", "--quiet-pull"], capture_output=quiet)
        start_at = time.time() + START_DELAY
        meta["start_at"] = start_at
        for rp in plan.roles:
            cfg = {"role": rp.role, "uid": rp.uid, "cmd": rp.cmd, "env": rp.env,
                   "start_at": start_at, "minutes": cond.get("minutes", 15)}
            keys = "".join(f"{k}={v}\n" for k, v in rp.keys.items())
            sh(["docker", "cp", "-", f"{project}-{rp.role}-1:/run/trial"],
               input=trial_tar({"config.json": json.dumps(cfg).encode(), "prompt": rp.prompt,
                                "keys.env": keys.encode()}), capture_output=True)

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
    say("artifacts:", tdir)
    return tdir


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


def main():
    ap = argparse.ArgumentParser(prog="trial")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--only", nargs="*")
    b.add_argument("--claude-version"); b.add_argument("--codex-version")
    lk = sub.add_parser("lockdown"); lk.add_argument("--envs", nargs="+", default=["env1s", "env2s"])
    r = sub.add_parser("run")
    r.add_argument("--env", required=True); r.add_argument("--harness", required=True)
    r.add_argument("--minutes", type=float, default=15); r.add_argument("--model-generation", default="current")
    r.add_argument("--prompt-id", default="placeholder"); r.add_argument("--seed", type=int)
    r.add_argument("--trial-id"); r.add_argument("--budget-usd", type=float, default=5.0)
    a = ap.parse_args()
    if a.cmd == "build":
        build(a)
    elif a.cmd == "lockdown":
        sys.exit(lockdown(a))
    else:
        cond = {"env": a.env, "harness": a.harness, "minutes": a.minutes,
                "model_generation": a.model_generation, "prompt_id": a.prompt_id,
                "budget_usd": a.budget_usd}
        if a.seed is not None:
            cond["seed"] = a.seed
        run_trial(cond, a.trial_id)


if __name__ == "__main__":
    main()
