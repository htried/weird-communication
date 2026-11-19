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


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def session_and_final(lines):
    sid, final = None, ""
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        sid = e.get("session_id") or e.get("thread_id") or sid
        if e.get("type") == "session" and e.get("id"):                # pi
            sid = e["id"]
        msg = e.get("message") if isinstance(e.get("message"), dict) else None
        if e.get("type") == "message_end" and msg and msg.get("role") == "assistant":   # pi
            final = text_of(msg.get("content")) or final
        if e.get("type") == "result":
            final = e.get("result") or final
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "agent_message":
            final = it.get("text") or final
    return sid, final


EVAL_PROMPT = """You are checking whether an AI agent has met a goal. You see only the conversation.
Goal condition: {condition}

Conversation so far (most recent last):
{transcript}

Answer with JSON only: {{"verdict": "met" | "not_met" | "impossible", "reason": "<one or two sentences>"}}.
"met" only if the agent's own final message clearly satisfies the condition. "impossible" only if
the conversation shows the goal cannot be achieved at all. Otherwise "not_met"."""


def transcript_text(lines, limit=60_000):
    """Compact conversation text for the evaluator: assistant text, tool calls and results."""
    out = []
    for raw in lines:
        try:
            e = json.loads(raw)
        except ValueError:
            continue
        t = e.get("type")
        if t == "message_end" and isinstance(e.get("message"), dict) and e["message"].get("role") == "assistant":
            txt = text_of(e["message"].get("content"))
            if txt:
                out.append(f"AGENT: {txt}")
        elif t == "tool_execution_start":
            out.append(f"TOOL CALL {e.get('toolName')}: {json.dumps(e.get('args'))[:1500]}")
        elif t == "tool_execution_end":
            res = e.get("result")
            res = text_of(res.get("content")) if isinstance(res, dict) else str(res)
            out.append(f"TOOL RESULT: {res[:1500]}")
        elif t == "sc.goal_eval":
            out.append(f"EVALUATOR: {e.get('text')}")
    text = "\n".join(out)
    return text if len(text) <= limit else "[earlier conversation omitted]\n" + text[-limit:]


def evaluate_goal(goal, lines, env):
    """Ask the evaluator model (via the lockdown proxy) whether the goal is met."""
    import urllib.request
    body = json.dumps({"model": goal["evaluator_model"], "temperature": 0, "max_tokens": 2000,
                       "messages": [{"role": "user", "content": EVAL_PROMPT.format(
                           condition=goal["condition"], transcript=transcript_text(lines))}]}).encode()
    req = urllib.request.Request(goal["evaluator_url"], data=body, headers={
        "Authorization": "Bearer " + env.get(goal["key_env"], ""), "Content-Type": "application/json"})
    proxy = env.get("HTTPS_PROXY")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy} if proxy else {}))
    try:
        resp = json.load(opener.open(req, timeout=120))
        content = resp["choices"][0]["message"]["content"] or ""
        m = re.search(r"\{[^{}]*\"verdict\"[^{}]*\}", content, re.S)   # tolerate prose/code fences
        verdict = json.loads(m.group(0) if m else content)
        v = verdict.get("verdict")
        return (v if v in ("met", "not_met", "impossible") else "not_met"), str(verdict.get("reason", ""))[:600]
    except Exception as ex:                       # evaluator failure ends the loop (logged)
        return "error", f"{type(ex).__name__}: {str(ex)[:300]}"


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

    # Device envs grant the camera and desktop sockets through supplementary groups (host GIDs
    # from the runner); otherwise the agent has no supplementary groups at all.
    groups = [int(g) for g in cfg.get("groups", [])]
    priv = ["setpriv", f"--reuid={uid}", f"--regid={uid}",
            f"--groups={','.join(map(str, groups))}" if groups else "--clear-groups",
            "--inh-caps=-all", "--no-new-privs", "--"]
    events, errors = Log("events.jsonl"), Log("stderr.log")
    followups, resume, goal = list(cfg.get("followups", [])), cfg.get("resume"), cfg.get("goal")
    all_lines, goal_continues = [], 0
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
        all_lines += lines
        if goal and resume and status != "timeout":
            sid, final = session_and_final(lines)
            verdict, reason = evaluate_goal(goal, all_lines, env)
            log(f"goal evaluator: {verdict} ({reason[:120]})")
            if verdict != "not_met" or not sid or goal_continues >= int(goal.get("max_continues", 9)):
                events.write(json.dumps({"type": "sc.goal_eval", "turn": turn + 1, "verdict": verdict,
                                         "reason": reason, "continued": False,
                                         "text": f"Goal evaluator verdict: {verdict}. {reason}"}).encode())
                break
            goal_continues += 1
            turn += 1
            msg = f"Goal evaluator feedback: {reason}\nThe goal is not yet met. Keep working until: {goal['condition']}"
            events.write(json.dumps({"type": "sc.goal_eval", "turn": turn, "verdict": verdict, "reason": reason,
                                     "continued": True, "text": msg}).encode())
            stdin_bytes = msg.encode()
            argv = [a.replace("{session}", sid) for a in resume]
            continue
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
    # Keep the harness's own session records (root-side copy into the root-only /logs). Codex's
    # --json stream has no event for its built-in image viewer; its session rollout does.
    import shutil
    for rel in (".codex/sessions", ".claude/projects", ".pi/agent/sessions"):
        src = os.path.join("/home/agent", rel)
        if os.path.isdir(src):
            try:
                shutil.copytree(src, os.path.join(LOGS, "harness_sessions", rel.replace("/", "_")), dirs_exist_ok=True)
            except OSError as e:
                log(f"could not copy {rel}: {e}")

    end = {"role": cfg["role"], "status": status, "exit_code": returncode, "turns": turn + 1,
           "scripted_replies_sent": turn - goal_continues, "goal_continues": goal_continues,
           "started": started, "ended": ended,
           "seconds": round(ended - started, 3), "leftover_processes_killed": swept}
    with open(f"{LOGS}/end.json", "w") as f:
        json.dump(end, f)
    log(f"end {end}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
