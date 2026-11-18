"""Controller for split-host envs (env3: two laptops that share only the room).

Runs on the controller machine (the Mac on the lab LAN), never on a lab host. Stdlib only.
Each lab host runs one role through `trial plan-split` / `trial run-half`; this module
relays the second half between hosts, starts both at the same clock time, and merges the
two half-trials into one artifacts/trials/<id>/ directory with the usual layout, so
score.py, merge_trace.py and the judge work unchanged.

  python3 -m harness.runner.env3 sync                  # push the repo to both lab hosts
  python3 -m harness.runner.env3 build                 # build images on both (pinned CLI versions)
  python3 -m harness.runner.env3 lockdown              # env3 probe gate
  python3 -m harness.runner.env3 run --harness openai_codex --model-generation current \
      --task secret_sum --rung 4 --hatch prompt
  python3 -m harness.runner.env3 sweep sweeps/env3_pilot.json

Trials run one at a time: there is only one room.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .condition import ROLES, expand_sweep
from .probe_expectations import evaluate

REPO = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO / "artifacts" / "trials"
LAB = json.loads((REPO / "harness" / "config" / "lab.json").read_text())
HOSTS = LAB["hosts"]                     # role -> ssh alias
RDIR = LAB["repo"]
PINNED = {"claude": "2.1.322", "codex": "rust-v0.167.2"}   # same CLI versions as the server sweeps


def ssh(host: str, cmd: str, input: bytes | None = None, check: bool = True, timeout: float | None = None):
    return subprocess.run(["ssh", host, cmd], input=input, capture_output=True, check=check, timeout=timeout)


def say(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def clock_offsets() -> dict[str, float]:
    """Remote clock minus local clock (s), from the midpoint of a round trip."""
    offs = {}
    for role, h in HOSTS.items():
        t0 = time.time()
        r = ssh(h, "date +%s.%N")
        t1 = time.time()
        offs[h] = round(float(r.stdout) - (t0 + t1) / 2, 3)
    return offs


def merge(tdir: Path, cond: dict, extra: dict) -> bool:
    """Combine the half-trial files into trial.json, egress.log and lan_flows.json."""
    halves = {r: json.loads((tdir / f"half-{r}.json").read_text()) for r in ROLES if (tdir / f"half-{r}.json").exists()}
    plan = json.loads((tdir / "plan.json").read_text())
    ips = {}
    for h in halves.values():
        ips.update(h.get("ips") or {})
    meta = {"trial_id": tdir.name, "condition": cond, "env_spec": plan["env_spec"], "subnet": plan["subnet"],
            "model": plan["model"], "truth": plan["truth"], "ladder_version": plan["ladder_version"],
            "host": {r: h.get("host") for r, h in halves.items()}, "project": next(iter(halves.values()), {}).get("project"),
            "start_at": min((h["start_at"] for h in halves.values()), default=None),
            "ended": max((h.get("ended") or 0 for h in halves.values()), default=None),
            "ips": ips, "prompts": {r: h["prompt"] for r, h in halves.items()},
            "roles": [{"role": r, "uid": h["uid"], "value": h["value"], "image": h["image"], "cmd": h["cmd"],
                       "env_keys": h["env_keys"], "key_names": h["key_names"], "files": h["files"],
                       "followups": h["followups"], "resume": h["resume"], "goal": h["goal"],
                       "host": h["host"], "late_start_s": h["late_start_s"], "devices": h["devices"]}
                      for r, h in halves.items()],
            "images": {h["image"]: h.get("image_id") for h in halves.values()}, **extra}
    (tdir / "trial.json").write_text(json.dumps(meta, indent=1))
    with open(tdir / "egress.log", "w") as f:
        for r in ROLES:
            p = tdir / f"egress-{r}.log"
            if p.exists():
                f.write(p.read_text(errors="replace"))
    flows = {"flows": [], "arp_requests": {}}
    for r in ROLES:
        p = tdir / f"lan_flows-{r}.json"
        if p.exists():
            d = json.loads(p.read_text())
            flows["flows"] += d.get("flows", [])
            flows["arp_requests"].update(d.get("arp_requests", {}))
    (tdir / "lan_flows.json").write_text(json.dumps(flows, indent=1))
    done = all((tdir / f"DONE-{r}").exists() for r in ROLES)
    if done:
        (tdir / "DONE").write_text(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return done


def run(cond: dict, trial_id: str | None = None) -> Path:
    trial_id = trial_id or f"e3t{time.strftime('%m%d%H%M%S')}"
    tdir = ARTIFACTS / trial_id
    if (tdir / "DONE").exists():
        return tdir
    cond = {**cond, "env": "env3", "lan_targets": LAB["lan_targets"]}
    pdir = f"/tmp/scplan-{trial_id}"
    h1, h2 = HOSTS["node1"], HOSTS["node2"]
    for h in (h1, h2):     # stale half-trial files from an interrupted attempt
        ssh(h, f"rm -rf {pdir} {RDIR}/artifacts/trials/{trial_id}", check=False)
    try:
        r = ssh(h1, f"cd {RDIR} && python3 -m harness.runner.trial plan-split --trial-id {trial_id} --out {pdir}",
                input=json.dumps(cond).encode())
        say(trial_id, "planned", r.stdout.decode().strip())
        # Relay node2's half host-to-host through this process's memory (never written here).
        half2 = ssh(h1, f"cat {pdir}/node2.json").stdout
        ssh(h2, f"umask 077 && mkdir -p {pdir} && cat > {pdir}/node2.json", input=half2)
        ssh(h1, f"rm -f {pdir}/node2.json")
        plan_meta = json.loads(half2)
        plan_meta.pop("role")
        del half2
        offsets = clock_offsets()
        start_at = time.time() + LAB["start_delay_s"]
        say(trial_id, f"starting both halves at +{LAB['start_delay_s']}s; clock offsets {offsets}")
        minutes = float(cond.get("minutes", 15))

        def half(role):
            h = HOSTS[role]
            return ssh(h, f"cd {RDIR} && python3 -m harness.runner.trial run-half --half {pdir}/{role}.json "
                          f"--start-at {start_at}", check=False, timeout=minutes * 60 + 900)
        with ThreadPoolExecutor(2) as ex:
            res = dict(zip(ROLES, ex.map(half, ROLES)))
        tdir.mkdir(parents=True, exist_ok=True)
        for role, rr in res.items():
            (tdir / f"controller-{role}.log").write_bytes(rr.stdout + rr.stderr)
            subprocess.run(["rsync", "-a", f"{HOSTS[role]}:{RDIR}/artifacts/trials/{trial_id}/", f"{tdir}/"],
                           capture_output=True)     # may be absent if that half failed early
        (tdir / "plan.json").write_text(json.dumps(plan_meta, indent=1))
        ok = merge(tdir, cond, {"clock_offsets": offsets, "controller_start_at": start_at})
        if not ok:
            raise RuntimeError(f"{trial_id}: half failed: " + "; ".join(
                f"{r}: rc={rr.returncode} {rr.stderr.decode(errors='replace')[-300:]}" for r, rr in res.items()))
        say(trial_id, "done ->", tdir)
        return tdir
    finally:
        for h in (h1, h2):
            ssh(h, f"rm -rf {pdir}", check=False)


def lockdown(_args) -> int:
    tdir = run({"harness": "probe", "minutes": 2}, f"e3probe{time.strftime('%m%d%H%M%S')}")
    failures, report = 0, {}
    for role in ROLES:
        obs = [json.loads(l) for l in (tdir / "agents" / role / "events.jsonl").read_text().splitlines()
               if l.startswith("{")]
        rows = evaluate("env3", obs)
        report[role] = [{"check": c, "expected": e, "observed": o, "ok": ok} for c, e, o, ok in rows]
        bad = [x for x in rows if not x[3]]
        failures += len(bad)
        print(f"\nenv3 / {role} ({HOSTS[role]}): {len(rows) - len(bad)}/{len(rows)} checks pass")
        for c, e, o, ok in rows:
            print(f"  {'ok ' if ok else 'FAIL'} {c:<45} expected={e!s:<6} observed={o}")
    (tdir / "probe_report.json").write_text(json.dumps(report, indent=1))
    print("\nENV3 LOCKDOWN GATE:", "PASS" if failures == 0 else f"FAIL ({failures} mismatches)")
    return 1 if failures else 0


def sweep(args) -> int:
    """Run a sweep spec sequentially. Trials are ordered rep by rep (every condition's r1, then
    every r2, ...), so a time-boxed session (--until, local HH:MM) stays balanced across
    conditions; no new trial starts after the deadline. --reps overrides the spec's reps."""
    spec = json.loads(Path(args.spec).read_text())
    if args.reps:
        spec["reps"] = args.reps
    rep = lambda t: int(t.rsplit("-r", 1)[1])
    conds = sorted([(t, c) for t, c in expand_sweep(spec) if not (ARTIFACTS / t / "DONE").exists()],
                   key=lambda tc: rep(tc[0]))
    until = None
    if args.until:
        hh, mm = map(int, args.until.split(":"))
        lt = time.localtime()
        until = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    say(f"sweep {spec['name']}: {len(conds)} to run (sequential: one room)"
        + (f"; no new trials after {args.until}" if until else ""))
    fail = 0
    for i, (t, c) in enumerate(conds, 1):
        if until and time.time() >= until:
            say(f"deadline {args.until} reached; stopping before {t}")
            conds = conds[:i - 1]
            break
        for attempt in range(2):
            try:
                run(c, t)
                break
            except Exception as e:
                say(f"  attempt {attempt + 1} failed for {t}: {type(e).__name__}: {str(e)[:300]}")
                time.sleep(10)
        else:
            fail += 1
        say(f"[{i}/{len(conds)}] {t}")
    say(f"sweep finished: {len(conds) - fail} ok, {fail} failed")
    return 1 if fail else 0


def sync(_args) -> int:
    for h in HOSTS.values():
        subprocess.run(["rsync", "-az", "--exclude", "artifacts/", "--exclude", ".git/", "--exclude", "__pycache__/",
                        "--exclude", "legacy/", f"{REPO}/", f"{h}:{RDIR}/"], check=True)
        say("synced", h)
    return 0


def build(args) -> int:
    cmd = (f"cd {RDIR} && python3 -m harness.runner.trial build --claude-version {PINNED['claude']} "
           f"--codex-version {PINNED['codex']}" + (f" --only {' '.join(args.only)}" if args.only else ""))
    with ThreadPoolExecutor(2) as ex:
        res = list(ex.map(lambda h: (h, ssh(h, cmd + " 2>&1 | tail -8", check=False)), HOSTS.values()))
    for h, r in res:
        print(f"== {h}\n{r.stdout.decode(errors='replace')}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="env3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync"); sub.add_parser("lockdown")
    b = sub.add_parser("build"); b.add_argument("--only", nargs="*")
    sw = sub.add_parser("sweep"); sw.add_argument("spec")
    sw.add_argument("--until", help="local HH:MM; no new trial starts after it")
    sw.add_argument("--reps", type=int, help="override the spec's reps (with --until: an upper bound)")
    r = sub.add_parser("run")
    r.add_argument("--harness", required=True); r.add_argument("--model-generation", default="current")
    r.add_argument("--minutes", type=float, default=15); r.add_argument("--budget-usd", type=float, default=5.0)
    r.add_argument("--task", required=True); r.add_argument("--rung", type=int, required=True)
    r.add_argument("--hatch", default="prompt"); r.add_argument("--trial-id")
    a = ap.parse_args()
    if a.cmd == "run":
        run({"harness": a.harness, "model_generation": a.model_generation, "minutes": a.minutes,
             "budget_usd": a.budget_usd, "task": a.task, "rung": a.rung, "hatch": a.hatch}, a.trial_id)
        return
    sys.exit({"sync": sync, "lockdown": lockdown, "sweep": sweep, "build": build}[a.cmd](a))


if __name__ == "__main__":
    main()
