"""Minimal obs-websocket v5 client (stdlib only) to start/stop the room recording per env3 trial.

The OBS password is read from the recorder Mac's own OBS config over SSH at connect time
(ssh alias in harness/config/lab.json "obs.ssh"), kept in memory only, never stored or printed.

  python3 -m harness.runner.obs status|start|stop
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import uuid
from pathlib import Path

LAB = json.loads((Path(__file__).resolve().parents[2] / "harness" / "config" / "lab.json").read_text())
CONFIG = "~/Library/Application Support/obs-studio/plugin_config/obs-websocket/config.json"


def _password() -> str:
    r = subprocess.run(["ssh", LAB["obs"]["ssh"], f"cat \"$HOME/{CONFIG[2:]}\""], capture_output=True, text=True, check=True)
    return json.loads(r.stdout).get("server_password", "")


class OBS:
    def __init__(self, host: str | None = None, port: int | None = None, timeout: float = 10):
        cfg = LAB["obs"]
        self.sock = socket.create_connection((host or cfg["host"], port or cfg["port"]), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET / HTTP/1.1\r\nHost: {cfg['host']}:{cfg['port']}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                           "Sec-WebSocket-Protocol: obswebsocket.json\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode(errors="replace"))
        hello = self._recv()["d"]
        ident = {"rpcVersion": 1}
        if hello.get("authentication"):
            a, pw = hello["authentication"], _password()
            secret = base64.b64encode(hashlib.sha256((pw + a["salt"]).encode()).digest()).decode()
            ident["authentication"] = base64.b64encode(hashlib.sha256((secret + a["challenge"]).encode()).digest()).decode()
            del pw, secret
        self._send({"op": 1, "d": ident})
        if self._recv().get("op") != 2:
            raise ConnectionError("OBS identify failed (wrong password?)")

    def _send(self, obj: dict):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n)
                                if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def _recv(self) -> dict:
        while True:
            b1, b2 = self._recv_exact(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            payload = self._recv_exact(n)
            if b1 & 0x0F == 0x1:
                return json.loads(payload)
            if b1 & 0x0F == 0x8:
                raise ConnectionError("closed by OBS")

    def request(self, rtype: str, data: dict | None = None) -> dict:
        rid = uuid.uuid4().hex
        self._send({"op": 6, "d": {"requestType": rtype, "requestId": rid, "requestData": data or {}}})
        while True:
            m = self._recv()
            if m.get("op") == 7 and m["d"].get("requestId") == rid:
                st = m["d"]["requestStatus"]
                if not st.get("result"):
                    raise RuntimeError(f"{rtype}: {st.get('code')} {st.get('comment', '')}")
                return m["d"].get("responseData") or {}

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def call(rtype: str, data: dict | None = None) -> dict:
    o = OBS()
    try:
        return o.request(rtype, data)
    finally:
        o.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    print(json.dumps({"status": lambda: call("GetRecordStatus"), "start": lambda: call("StartRecord"),
                      "stop": lambda: call("StopRecord")}[cmd](), indent=1))
