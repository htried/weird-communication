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
import re
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


class Log:
    """Append-only, line-timestamped root-owned log shared across turns."""
    def __init__(self, name):
        self.out = open(f"{LOGS}/{name}", "ab")
        self.ts = open(f"{LOGS}/{name}.t", "a")
        self.lock = threading.Lock()

    def write(self, line: bytes):
        with self.lock:
            self.out.write(line if line.endswith(b"\n") else line + b"\n"); self.out.flush()
            self.ts.write(f"{time.time():.3f}\n"); self.ts.flush()


def relay(src, log_, keep=None):
    for line in iter(src.readline, b""):
        log_.write(line)
        if keep is not None:
            keep.append(line)


# Scripted-reply trigger: the agent's last message asks for input or says it couldn't finish.
# A plain, confident answer (even a wrong one) gets no reply. Replying would signal that the
# answer was wrong.
ASKS = re.compile(
    r"\?|\b(please (provide|send|share|give|tell)|provide (the|part|it|y|me)|let me know|send me|once you|"
    r"if you (can|could|send|share|give|provide)|could you|can you|"
    r"can['’]?t|cannot|unable|couldn['’]?t|could not|don['’]?t have|do not have|doesn['’]?t have|missing|"
    r"not (available|possible|able|accessible)|isn['’]?t (available|possible|accessible|here)|no access|"
    r"won['’]?t guess|need (the|y|more|your|you|access)|without (the|y|access))\b", re.I)


def session_and_final(lines):
    sid, final = None, ""
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        sid = e.get("session_id") or e.get("thread_id") or sid
        if e.get("type") == "result":
            final = e.get("result") or final
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "agent_message":
            final = it.get("text") or final
    return sid, final


def install_files(uid, files):
    """Write the agent's private task files as the agent itself (no CHOWN capability needed)."""
    for dest, src in files.items():
        data = open(f"{TRIAL}/{src}", "rb").read()
        subprocess.run(["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "--no-new-privs",
                        "--", "sh", "-c", 'umask 022; mkdir -p "$(dirname "$1")" && cat > "$1"', "sh", dest],
                       input=data, check=True)


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

    install_files(uid, cfg.get("files", {}))

    priv = ["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups",
            "--inh-caps=-all", "--no-new-privs", "--"]
    events, errors = Log("events.jsonl"), Log("stderr.log")
    followups, resume = list(cfg.get("followups", [])), cfg.get("resume")
    started = time.time()
    deadline = started + float(cfg["minutes"]) * 60
    # The agent gets a normal umask, so files it writes in shared /work and /tmp are readable
    # by the peer, as on any ordinary system. Our logs stay protected by /logs being 0700 root.
    os.umask(0o022)

    status, turn, stdin_bytes, argv, returncode = "finished", 0, prompt, cfg["cmd"], None
    while True:
        log(f"turn {turn} start cmd={argv[0]}")
        lines = []
        child = subprocess.Popen(priv + argv, cwd="/work", env=env, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        threads = [threading.Thread(target=relay, args=(child.stdout, events, lines), daemon=True),
                   threading.Thread(target=relay, args=(child.stderr, errors), daemon=True)]
        for t in threads:
            t.start()
        try:
            child.stdin.write(stdin_bytes)
            child.stdin.close()
        except BrokenPipeError:
            pass
        try:
            child.wait(timeout=max(deadline - time.time(), 1))
        except subprocess.TimeoutExpired:
            status = "timeout"
            log("deadline reached; SIGTERM process group")
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=GRACE_SECONDS)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for t in threads:
            t.join(timeout=5)
        returncode = child.returncode
        if status == "timeout" or not followups or not resume:
            break
        sid, final = session_and_final(lines)
        if not sid:
            log("no session id; cannot send scripted reply"); break
        if final and not ASKS.search(final):
            log("final answer does not ask for input; no scripted reply"); break
        reply = followups.pop(0)
        turn += 1
        events.write(json.dumps({"type": "sc.user_reply", "turn": turn, "text": reply,
                                 "trigger": "empty_final" if not final else "asks_for_input"}).encode())
        stdin_bytes = reply.encode()
        argv = [a.replace("{session}", sid) for a in resume]
    ended = time.time()
    swept = sweep(uid)

    end = {"role": cfg["role"], "status": status, "exit_code": returncode, "turns": turn + 1,
           "scripted_replies_sent": turn, "started": started, "ended": ended,
           "seconds": round(ended - started, 3), "leftover_processes_killed": swept}
    with open(f"{LOGS}/end.json", "w") as f:
        json.dump(end, f)
    log(f"end {end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
