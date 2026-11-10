#!/usr/bin/env python3
"""Container entry wrapper. Runs as root and is harness-agnostic.

Reads the root-only trial files the runner copied into /run/trial, waits for the shared
start time, then runs the configured command as the agent UID:
- with no capabilities and no-new-privs;
- with the prompt on stdin, never in argv or the environment.

The agent's stdout/stderr are relayed into root-only /logs, so an agent can't read or
truncate its own transcript. After the run, every process left behind by the agent UID is
killed.

/run/trial/config.json:
  {"role", "uid", "cmd": [...], "env": {...}, "start_at": epoch, "minutes"}
/run/trial/prompt     the exact bytes fed to the command's stdin
/run/trial/keys.env   KEY=VALUE lines, merged into the command's environment
"""
import json
import os
import signal
import subprocess
import sys
import threading
import time

TRIAL = "/run/trial"
LOGS = "/logs"
GRACE_SECONDS = 5


def log(msg):
    with open(f"{LOGS}/entry.log", "a") as f:
        f.write(f"{time.time():.3f} {msg}\n")


def read_keys(path):
    env = {}
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def relay(src, name):
    """Copy a child pipe into a root-owned log, with a per-line receive timestamp alongside."""
    with open(f"{LOGS}/{name}", "wb") as out, open(f"{LOGS}/{name}.t", "w") as ts:
        for line in iter(src.readline, b""):
            out.write(line); out.flush()
            ts.write(f"{time.time():.3f}\n"); ts.flush()


def pids_of_uid(uid):
    pids = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/status") as f:
                    for line in f:
                        if line.startswith("Uid:") and int(line.split()[1]) == uid:
                            pids.append(int(d))
                            break
            except OSError:
                pass
    return pids


def sweep(uid):
    """Kill everything the agent UID left running (nohup'd recorders, servers, ...)."""
    left = pids_of_uid(uid)
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return len(left)


def main():
    os.umask(0o077)
    cfg = json.load(open(f"{TRIAL}/config.json"))
    prompt = open(f"{TRIAL}/prompt", "rb").read()
    uid = int(cfg["uid"])

    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/home/agent", "LANG": "C.UTF-8",
           "TERM": "dumb", "SHELL": "/bin/bash", "USER": cfg["role"], "LOGNAME": cfg["role"]}
    env.update(cfg.get("env", {}))
    env.update(read_keys(f"{TRIAL}/keys.env"))

    wait = cfg["start_at"] - time.time()
    log(f"role={cfg['role']} waiting {wait:.1f}s for start")
    if wait > 0:
        time.sleep(wait)

    cmd = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups",
           "--inh-caps=-all", "--no-new-privs", "--"] + cfg["cmd"]
    started = time.time()
    log(f"start cmd={cfg['cmd'][0]}")
    # The agent gets a normal umask, so files it writes in shared /work and /tmp are readable
    # by the peer, as on any ordinary system. Our logs stay protected by /logs being 0700 root.
    os.umask(0o022)
    child = subprocess.Popen(cmd, cwd="/work", env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True)
    threads = [threading.Thread(target=relay, args=(child.stdout, "events.jsonl"), daemon=True),
               threading.Thread(target=relay, args=(child.stderr, "stderr.log"), daemon=True)]
    for t in threads:
        t.start()
    try:
        child.stdin.write(prompt)
        child.stdin.close()
    except BrokenPipeError:
        pass

    status = "finished"
    try:
        child.wait(timeout=float(cfg["minutes"]) * 60)
    except subprocess.TimeoutExpired:
        status = "timeout"
        log("deadline reached; SIGTERM process group")
        try:
            os.killpg(child.pid, signal.SIGTERM)
            child.wait(timeout=GRACE_SECONDS)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
    ended = time.time()
    swept = sweep(uid)
    for t in threads:
        t.join(timeout=5)

    end = {"role": cfg["role"], "status": status, "exit_code": child.returncode,
           "started": started, "ended": ended, "seconds": round(ended - started, 3),
           "leftover_processes_killed": swept}
    with open(f"{LOGS}/end.json", "w") as f:
        json.dump(end, f)
    log(f"end {end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
