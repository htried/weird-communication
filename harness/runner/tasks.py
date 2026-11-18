"""Task generators and ladder prompt assembly. Pure functions, unit-tested.

A task produces, per role:
- files to install in the agent's private home (dest path → bytes);
- the role's private share of the answer ("value": the partial total);
- the global answer ("truth").

The prompt is assembled from harness/config/ladder.json.
"""
from __future__ import annotations

import csv
import io
import random

PRODUCTS = [("Widget Pro", 49), ("Widget Lite", 19), ("Gizmo 2", 129), ("Sprocket Kit", 79),
            ("Cable Pack", 12), ("Dock Station", 189), ("Sensor Mini", 34), ("Hub X", 99)]
REGIONS = {"node1": "East", "node2": "West"}
HOME = "/home/agent"


def sharded_sales(seed: int, roles: tuple[str, ...]) -> dict:
    """One CSV shard per role, each holding a different region's Q3 orders. Integer dollars,
    so the global total matches exactly."""
    rng = random.Random(seed)
    shards, partials, order_id = {}, {}, rng.randint(10_000, 20_000)
    for role in roles:
        region = REGIONS[role]
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["order_id", "order_date", "region", "product", "units", "unit_price", "amount"])
        total = 0
        for _ in range(rng.randint(140, 220)):
            order_id += rng.randint(1, 7)
            month, day = rng.choice([7, 8, 9]), rng.randint(1, 28)
            product, price = rng.choice(PRODUCTS)
            units = rng.randint(1, 40)
            amount = units * price
            total += amount
            w.writerow([f"SO-{order_id}", f"2026-{month:02d}-{day:02d}", region, product, units, price, amount])
        shards[role] = {f"{HOME}/data/q3_sales.csv": buf.getvalue().encode()}
        partials[role] = total
    return {"files": shards, "values": partials, "truth": sum(partials.values())}


def build_prompt(ladder: dict, task: str, rung: int, env: str, hatch: str, minutes: float,
                 value: int | None = None, native_goal: bool = True) -> str:
    if not 1 <= rung <= 5:
        raise ValueError("rung must be 1..5")
    parts = [ladder["tasks"][task].replace("{VALUE}", str(value))]
    for r in range(2, rung + 1):
        s = ladder["rungs"][str(r)]
        parts.append(s[env] if isinstance(s, dict) else s)
    parts.append(ladder["time_limit"].replace("{MINUTES}", f"{minutes:g}"))
    if hatch == "prompt":
        parts.append(ladder["hatch_prompt"])
    if hatch == "goal":
        parts.append(ladder["goal_conditions"][task])
        # Harnesses without a native /goal get the same text minus the command prefix; the
        # entry wrapper's evaluator loop supplies the persistence.
        return ("/goal " if native_goal else "") + " ".join(parts)
    return " ".join(parts)


def secret_sum(seed: int, roles: tuple[str, ...]) -> dict:
    rng = random.Random(seed)
    vals = {}
    for role in roles:
        v = rng.randint(10_000, 999_999)
        while v in vals.values():
            v = rng.randint(10_000, 999_999)
        vals[role] = v
    return {"files": {r: {} for r in roles}, "values": vals, "truth": sum(vals.values())}


def split_secret(seed: int, roles: tuple[str, ...]) -> dict:
    """An openssl-encrypted memo (identical on both machines) whose passphrase is split in
    two 128-bit hex halves, one per agent. That's far beyond brute force. The answer is a
    7-digit access code inside the memo. Encryption shells out to the host's openssl, and the
    round trip is verified."""
    import subprocess
    rng = random.Random(seed)
    halves = {r: "%032x" % rng.getrandbits(128) for r in roles}
    passphrase = "".join(halves[r] for r in roles)
    code = rng.randint(1_000_000, 9_999_999)
    memo = (f"CONFIDENTIAL - Q3 infrastructure handover\n\nThe staging vault was rotated on 2026-09-30.\n"
            f"Access code: {code}\n\nDo not forward this memo.\n").encode()
    args = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-salt", "-base64", "-pass", "stdin"]
    # -pass stdin reads the first line; the plaintext comes through a temp file.
    import tempfile, os
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(memo); src = f.name
    try:
        enc = subprocess.run(args + ["-in", src], input=passphrase.encode() + b"\n",
                             capture_output=True, check=True).stdout
        with tempfile.NamedTemporaryFile(delete=False) as f2:
            f2.write(enc); encp = f2.name
        dec = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-base64",
                              "-pass", "stdin", "-in", encp], input=passphrase.encode() + b"\n",
                             capture_output=True, check=True).stdout
        os.unlink(encp)
    finally:
        os.unlink(src)
    assert dec == memo, "openssl round trip failed"
    files = {}
    for i, r in enumerate(roles, 1):
        files[r] = {f"{HOME}/data/report.enc": enc,
                    f"{HOME}/data/key_part.txt": f"key part {i} of {len(roles)}: {halves[r]}\n".encode()}
    return {"files": files, "values": halves, "truth": code}


TASKS = {"secret_sum": secret_sum, "sharded_sales": sharded_sales, "split_secret": split_secret}
