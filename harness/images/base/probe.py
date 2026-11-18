#!/usr/bin/env python3
"""Lockdown probe. It runs as the agent UID through the same entry path a real harness uses.

It reads its spec (JSON) from stdin, the way a real CLI reads its prompt. The spec embeds a
per-role prompt canary, and the environment carries a per-role key canary. It emits one JSON
observation per line. The runner, not the probe, decides pass or fail per env (see
harness/runner/probe_expectations.py).
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request

spec = json.loads(sys.stdin.read())
ROLE, PEER = spec["role"], spec["peer"]
PORT = 47000
T0 = time.time()


def emit(check, observed, **detail):
    print(json.dumps({"check": check, "observed": observed, "t": round(time.time() - T0, 2), **detail}), flush=True)


def tcp(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, type(e).__name__ + ": " + str(e)[:120]


def https_via_proxy(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": spec["proxy"]}))
    try:
        r = opener.open(url, timeout=10)
        return True, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:      # an HTTP error from the *origin* still means we got through
        return True, f"HTTP {e.code}"
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)[:160]


# Listener first, so the peer can reach us (env1/env2 expect peer TCP to work).
def listen():
    s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT)); s.listen(8); s.settimeout(30)
    try:
        while time.time() - T0 < 25:
            try:
                c, _ = s.accept(); c.close()
            except socket.timeout:
                break
    finally:
        s.close()
threading.Thread(target=listen, daemon=True).start()

for name in ("work", "tmp"):
    try:
        open(f"/{name}/marker-{ROLE}", "w").write(ROLE)
    except OSError as e:
        emit(f"write_{name}_marker", False, err=str(e))

# --- egress ---------------------------------------------------------------
for host in spec["api_hosts"]:
    ok, d = https_via_proxy(f"https://{host}/")
    emit(f"api_via_proxy:{host}", ok, detail=d)
ok, d = https_via_proxy("https://example.com/")
emit("non_api_via_proxy:example.com", ok, detail=d)
for host, port in [("1.1.1.1", 443), ("8.8.8.8", 53), (spec.get("host_public_ip", "1.1.1.1"), 22)]:
    ok, d = tcp(host, port)
    emit(f"direct_tcp:{host}:{port}", ok, detail=d)
try:
    socket.getaddrinfo("example.com", 443)
    emit("dns_external", True)
except OSError as e:
    emit("dns_external", False, detail=str(e))
route = subprocess.run(["ip", "route"], capture_output=True, text=True).stdout
emit("default_route", "default" in route, detail=route.strip())
gws = set(re.findall(r"^(\d+\.\d+\.\d+)\.\d+/\d+ ", route, re.M))
for prefix in sorted(gws):
    ok, d = tcp(f"{prefix}.1", 22)
    emit(f"bridge_gateway_ssh:{prefix}.1", ok, detail=d)
ok, d = tcp("172.17.0.1", 22)
emit("docker0_ssh:172.17.0.1", ok, detail=d)
for target in spec.get("lan_targets", []):          # home/lab LAN: router, controller, other laptop
    h, _, port = target.rpartition(":")
    ok, d = tcp(h, int(port))
    emit(f"direct_tcp:{h}:{port}", ok, detail=d)

# --- devices (env1/2/3): what the agent can use, and what it must not reach --------------
if spec.get("devices"):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "v4l2", "-i", "/dev/video0",
                        "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True, timeout=20)
    emit("camera_capture", r.returncode == 0, detail=r.stderr[-200:])
    r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=10)
    emit("audio_server", r.returncode == 0, detail=(r.stdout + r.stderr)[-200:])
    try:
        s = socket.socket(socket.AF_UNIX)
        s.connect(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/run/xdg"), os.environ.get("WAYLAND_DISPLAY", "wayland-0")))
        s.close(); emit("display_socket", True)
    except OSError as e:
        emit("display_socket", False, detail=str(e))
    emit("system_dbus", os.path.exists("/run/dbus/system_bus_socket"))

# --- privilege ------------------------------------------------------------
status = open("/proc/self/status").read()
caps = {k: v for k, v in re.findall(r"^(Cap\w+|NoNewPrivs):\s+(\S+)", status, re.M)}
emit("capabilities_nonzero", any(int(caps[k], 16) for k in ("CapInh", "CapPrm", "CapEff", "CapAmb")), detail=caps)
emit("no_new_privs", caps.get("NoNewPrivs") == "1")
emit("sudo_present", any(os.path.exists(p) for p in ("/usr/bin/sudo", "/bin/sudo", "/usr/sbin/sudo")))
emit("uid", os.getuid() == spec["uid"], detail=os.getuid())

# --- own trial secrets and logs must be unreadable ------------------------
for path in ("/run/trial/prompt", "/run/trial/keys.env", "/run/trial/config.json", "/logs/events.jsonl"):
    try:
        open(path).read(1)
        emit(f"read:{path}", True)
    except OSError as e:
        emit(f"read:{path}", False, detail=type(e).__name__)
try:
    os.listdir("/logs"); emit("list:/logs", True)
except OSError:
    emit("list:/logs", False)

# --- what's shared with the peer ------------------------------------------
time.sleep(4)
try:
    peer_ip = socket.gethostbyname(PEER)
    ok, d = tcp(PEER, PORT)
except OSError as e:
    peer_ip, ok, d = None, False, str(e)
emit("peer_tcp", ok, peer=PEER, peer_ip=peer_ip, detail=d)
time.sleep(2)
for name in ("work", "tmp"):
    emit(f"peer_marker_in_/{name}", os.path.exists(f"/{name}/marker-{PEER}"))
peer_uid = spec["peer_uid"]
peer_procs = []
for d in os.listdir("/proc"):
    if d.isdigit():
        try:
            if os.stat(f"/proc/{d}").st_uid == peer_uid:
                peer_procs.append(d)
        except OSError:
            pass
emit("peer_processes_visible", bool(peer_procs), count=len(peer_procs))

# --- canary leaks: the peer's prompt or key must not be readable anywhere ----
peer_canaries = [f"PROMPTCANARY-{PEER}", f"KEYCANARY-{PEER}"]
own_prompt_canary = f"PROMPTCANARY-{ROLE}"
hits, own_argv_hits = [], []
for d in os.listdir("/proc"):
    if not d.isdigit():
        continue
    for leaf in ("cmdline", "environ"):
        try:
            blob = open(f"/proc/{d}/{leaf}", "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        hits += [f"/proc/{d}/{leaf}:{c}" for c in peer_canaries if c in blob]
        if leaf == "cmdline" and own_prompt_canary in blob:
            own_argv_hits.append(d)
for root in ("/work", "/tmp", "/home", "/run", "/opt", "/var/tmp", "/dev/shm"):
    for dirpath, _, files in os.walk(root):
        for fn in files:
            p = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(p) < 5_000_000:
                    blob = open(p, "rb").read().decode("utf-8", "replace")
                    hits += [f"{p}:{c}" for c in peer_canaries if c in blob]
            except OSError:
                pass
emit("peer_canary_readable", bool(hits), hits=hits[:20])
emit("own_prompt_in_any_argv", bool(own_argv_hits), pids=own_argv_hits)
# Stay alive briefly, so a peer scanning /proc a moment later still sees this process
# (env1s peer_processes_visible was a timing race when one probe exited first).
time.sleep(4)
emit("done", True, elapsed=round(time.time() - T0, 1))
