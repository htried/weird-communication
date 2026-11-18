#!/usr/bin/env python3
"""Readable dump of a trial: values, each agent's tool calls, results and final answer.

    python3 analysis/show.py artifacts/trials/<trial_id> [--full]

Understands Claude Code stream-json and Codex `exec --json` events.
"""
import json
import sys
from pathlib import Path

W = 10_000 if "--full" in sys.argv else 400


def clip(s, n=W):
    s = str(s).replace("\n", " ⏎ ")
    return s if len(s) <= n else s[:n] + " …"


def show_agent(path: Path):
    for line in path.read_text(errors="replace").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            print("  RAW     ", clip(line)); continue
        t = e.get("type", "")
        if t == "system" and e.get("subtype") == "init":
            print(f"  INIT     model={e.get('model')} v={e.get('claude_code_version')} tools={len(e.get('tools', []))}")
        elif t == "assistant":
            for c in e["message"]["content"]:
                if c["type"] == "text":
                    print("  SAY     ", clip(c["text"]))
                elif c["type"] == "tool_use":
                    print(f"  CALL     {c['name']} {clip(json.dumps(c['input']))}")
        elif t == "system" and e.get("subtype") in ("notification", "informational"):
            print("  NOTE    ", clip(e.get("text") or e.get("content")))
        elif t == "user":
            for c in e["message"].get("content", []):
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").startswith("Stop hook feedback"):
                    print("  GOALEVAL", clip(c["text"]))
                elif isinstance(c, dict) and c.get("type") == "tool_result":
                    print("  RESULT  ", clip(c.get("content")))
        elif t == "sc.goal_eval":
            print(f"  GOALEVAL {clip(e.get('text'))}")
        elif t == "tool_execution_start":
            a = e.get("args") or {}
            print(f"  CALL     {e.get('toolName')} {clip(a.get('command') if e.get('toolName') == 'bash' else json.dumps(a))}")
        elif t == "tool_execution_end":
            r = e.get("result")
            r = " ".join(b.get("text", "") for b in r.get("content", [])) if isinstance(r, dict) else r
            print(f"  RESULT   {'[error] ' if e.get('isError') else ''}{clip(r)}")
        elif t == "message_end" and isinstance(e.get("message"), dict) and e["message"].get("role") == "assistant":
            c = e["message"].get("content")
            txt = c if isinstance(c, str) else " ".join(b.get("text", "") for b in (c or []) if isinstance(b, dict) and b.get("type") == "text")
            if txt.strip():
                print("  SAY     ", clip(txt))
        elif t == "sc.user_reply":
            print(f"  USER     (scripted reply {e.get('turn')}) {e.get('text')}")
        elif t == "result":
            print(f"  FINAL    [{e.get('subtype')} turns={e.get('num_turns')} cost=${e.get('total_cost_usd')}] {clip(e.get('result'))}")
        elif t.startswith("item."):
            it = e.get("item", {})
            kind = it.get("type")
            if kind == "agent_message" and t == "item.completed":
                print("  SAY     ", clip(it.get("text")))
            elif kind == "command_execution" and t == "item.completed":
                print(f"  CALL     shell {clip(it.get('command'))}")
                print(f"  RESULT   [exit {it.get('exit_code')}] {clip(it.get('aggregated_output', ''))}")
            elif kind == "error":
                print("  ERROR   ", clip(it.get("message")))
            elif t == "item.completed" and kind not in ("reasoning",):
                print(f"  {kind:<8}", clip(json.dumps(it)))
        elif t == "turn.completed":
            u = e.get("usage", {})
            print(f"  USAGE    in={u.get('input_tokens')} out={u.get('output_tokens')}")


def main():
    tdir = Path(sys.argv[1])
    meta = json.loads((tdir / "trial.json").read_text())
    vals = {r["role"]: r["value"] for r in meta["roles"]}
    total = sum(vals.values()) if all(isinstance(v, int) for v in vals.values()) else None
    c = meta["condition"]
    print(f"{meta['trial_id']}  env={c['env']}  harness={c['harness']}  model={meta['model']}  "
          f"task={c.get('task')} rung={c.get('rung')} hatch={c.get('hatch')}  shares={vals}  answer={meta.get('truth', total)}")
    for r in meta["roles"]:
        d = tdir / "agents" / r["role"]
        end = json.loads((d / "end.json").read_text()) if (d / "end.json").exists() else {}
        print(f"\n== {r['role']} (value {r['value']})  {end.get('status')} in {end.get('seconds')}s, "
              f"leftovers killed: {end.get('leftover_processes_killed')}")
        show_agent(d / "events.jsonl")
    refused = [l for l in (tdir / "egress.log").read_text().splitlines() if "refused" in l]
    print(f"\nproxy refusals: {len(refused)}",
          sorted({l.split('domain')[-1].strip() for l in refused}) if refused else "")


if __name__ == "__main__":
    main()
