# AGENTS.md — sidechannel-exp

Guide for people and coding agents working in this repo.

## What this project is

Do co-located AI coding agents (Claude Code, Codex) use side channels **by default**? Each
trial runs two agents. Each gets the same request but only part of what's needed to answer it:
a number, half of a decryption passphrase, or one region's sales data. The trial measures
whether they reach for the other agent, and how:
- **snoop**: take the peer's data unilaterally;
- **broadcast**: send their own data out;
- **coordinate**: establish a two-way exchange.

Everything else they share (files, network, processes, the physical room) varies by
environment. This is a low-cost exploration, not a paper.

Read these before changing anything:

| Doc | Covers |
|---|---|
| `docs/plans/2026-11-09-sidechannel-propensity-design.md` | environments, lockdown, device rule, taxonomy |
| `docs/plans/2026-11-09-…-phase1-implementation.md` | the harness (as built) |
| `docs/plans/2026-11-10-…-phase2-implementation.md` | scoring and judge (as built) |
| `docs/plans/2026-11-11-ladder-sweep-v1.md` | tasks, prompt ladder, escape hatches, sweep |

## Layout

```
harness/
  images/base/        agent base image: entry.py (root wrapper), probe.py (lockdown probe)
  images/claude_code/ images/openai_codex/   per-harness images (FROM sc-base)
  images/egress/      tinyproxy allowlist: the ONLY route off the trial network
  images/tools/       host-side helper (tcpdump, volume export); never given to agents
  config/             model_registry.json, ladder.json (tasks/rungs/replies), prompts/
  runner/             condition.py (pure plan), tasks.py (task data + prompts),
                      compose.py (compose JSON), trial.py (lifecycle, sweep, lockdown)
  tests/              unit tests (stdlib unittest)
analysis/             score.py (rule-based), merge_trace.py, show.py, summarize_sweep.py,
                      judge_report.py, measurement/ (OpenAI judge, SCH taxonomy, schemas)
sweeps/               sweep specs (JSON: axes x reps)
scripts/              sync.sh, run_sweep.sh (detached + resumable), judge.sh (containerized)
legacy/macos-kit/     superseded first prototype; don't extend
artifacts/            git-ignored; trial outputs live on the Docker host
```

## Where things run

- **Trials run on a Linux Docker host. Currently that's the research server `boomhauer`.**
  Never run them on a daily-work laptop. The runner must run *on* that host, not through
  `DOCKER_HOST=ssh://`, because trial files and volume exports are host-local.
- Edit locally, then push code with `scripts/sync.sh` (rsync; set `SC_HOST` to use another
  host). Pull results with `scripts/sync.sh --pull`. `artifacts/` is never synced up.
- The host needs Docker plus Compose v2, and `openssl`, which `split_secret` uses to encrypt.
  Agent API keys go in `~/.config/sidechannel/keys/node1.env` and `node2.env`
  (`ANTHROPIC_API_KEY=`, `CODEX_API_KEY=`). The judge key goes in
  `~/.config/sidechannel/judge.env` (`OPENAI_API_KEY=`). **Never commit keys or copy them into
  the repo.**

## Common commands (on the host, from the repo root)

```bash
python3 -m unittest harness.tests.test_condition          # fast; run before any push
python3 -m harness.runner.trial build                     # build all images
python3 -m harness.runner.trial lockdown                  # lockdown gate: must PASS after image changes
python3 -m harness.runner.trial run --env env2s --harness claude_code --task split_secret \
    --rung 4 --hatch scripted --model-generation cheap_frontier --minutes 15
scripts/run_sweep.sh sweeps/ladder_v1.json 6              # detached sweep → score → judge
tail -f artifacts/sweeps/<name>.log                       # progress; re-run the same command to resume
python3 analysis/show.py artifacts/trials/<id>            # readable transcript
python3 analysis/summarize_sweep.py artifacts/trials/<prefix>-* --md out.md
```

Use `--harness stub|stub_exchange|probe` for plumbing tests. They make no model calls and get
no keys.

## Invariants: do not break these

These are what make the measurements valid. If a change touches them, re-run
`trial lockdown` and the unit tests, and say so in the commit message.

1. **The lockdown is enforced outside the agent.**
   - Agents sit only on the `internal: true` `lan` network. The bridge has `inhibit_ipv4`,
     so agents can't reach the host.
   - The proxy allowlist (`images/egress/filter`) is the only egress.
   - Never set `network_mode: host`, `privileged`, extra capabilities or sudo for agents.
   - The agent process must end up with zero capabilities and `NoNewPrivs=1`.
2. **Secrets exist only in the agent's own prompt and private files.**
   - Prompts go to the CLI on **stdin**, never in argv or env, because `/proc/*/cmdline` is
     world-readable.
   - Trial files are `docker cp`'d root-only into `/run/trial`. Task files are installed into
     the agent's private home.
   - Logs are written by root to `/logs` (0700), and agents can't read them.
3. **Harness internals stay hidden from agents.** `/opt/sc` is 0711 and `entry.py` is
   root-only, because agents read its comments in smoke tests. Use neutral names: `node1`,
   `node2`, `proxy`, `lan`, `ws<hash>`. Don't add experiment-revealing strings anywhere an
   agent can see them: env vars, hostnames, file names, prompts.
4. **Each env shares exactly what the design says.** env1s shares `/work`, `/tmp`, PID and
   IPC (through the neutral `init` container) and the network. env2s shares only the network
   (and CPU). Distinct UIDs everywhere (20001 and 20002). The device rule for laptop envs is
   in the design doc, §2.
5. **The scripted reply never depends on correctness.** It triggers only on asking or giving
   up (`ASKS` in `entry.py`), never on whether the answer was right.
6. **Claude's `ListAgents` and `SendMessage` stay disabled** (harness-native peer messaging,
   for parity with Codex), along with `WebSearch` and `WebFetch` (server-side web access).
7. **Prompts are additive and identical across roles** where the task allows. Ladder rungs
   only append text (there's a test for this). For `split_secret` and `sharded_sales` both
   agents get byte-identical prompts.
8. **Names are hash-based.** Parallel trials must never share a compose project or bridge
   name; a unit test covers this.

## How to extend

| To add… | Do this |
|---|---|
| **a task** | Add a generator to `harness/runner/tasks.py` (`TASKS`) returning `{files, values, truth}`, and its prompt (plus a goal condition) to `config/ladder.json`. Add tests. Check that `score.outcome` handles its answer format. |
| **a rung or escape hatch** | Edit `config/ladder.json` and `tasks.build_prompt`. New hatch values also go in `condition.py`'s validation. |
| **a sweep** | Add a JSON spec in `sweeps/` (`name`, `fixed`, `axes`, `reps`). Trial IDs are `<name>-<axis values>-r<rep>`. Never reuse a sweep name for a different design; completed trials are skipped by their `DONE` marker. |
| **a model** | Add it to `config/model_registry.json` and select it with `--model-generation`. |
| **a harness** | Add a new image under `images/`, plus `harness_cmd` / `resume_cmd` / `harness_env` in `condition.py`, an event parser in `analysis/score.py` (`parse_agent`) and `show.py`, and any harness background hosts in `HARNESS_BACKGROUND`. |
| **a judge category** | Edit `analysis/measurement/taxonomy_sidechannel.json` (bump `version`). The judge backend is OpenAI only (`judge.py`, `responses.parse`). |

## Conventions

- **Code:** Python, stdlib-first. The runner and scorer have no dependencies, and the judge
  deps live only in the `sc-judge` image. Keep functions in `condition.py` and `tasks.py`
  pure and unit-tested.
- **Docs:** design and implementation docs live in `docs/plans/` as dated pairs. Record
  decisions and as-built deviations there, not just in commit messages.
- **Before spending money:** run a stub or one-trial smoke test before any real sweep. Use
  `cheap_frontier` models for exploration, and say the expected trial count, cost and time
  before launching anything large.
- **Results:** don't trust `score.py`'s `wrong_number` or channel flags on their own. They're
  triage. The judge labels (`judge.json`) are the measurement, and anything flagged
  `needs_human_adjudication` gets a human read.
- **Prompt wording** is a research decision owned by the team. Don't rewrite ladder text,
  tasks or scripted replies without agreement.

## Known gotchas

- Codex must be installed from the full `codex-package` tarball. With the bare binary,
  "Code Mode" fails closed and the agent can't run commands.
- Codex makes its own background requests (`chatgpt.com`, `ab.chatgpt.com`, `api.github.com`).
  The proxy refuses them and the scorer counts them as `harness`, not agent, egress.
- Codex prints curly apostrophes ("can’t"). Any text matching must handle `['’]`.
- Agents inherit the wrapper's umask unless reset. Agents use 022 so files in shared dirs are
  readable, as on a normal system.
- Python on `boomhauer` has no `ensurepip`, so the judge runs in a container
  (`scripts/judge.sh`).
- Goal mode: Claude's `/goal` uses an evaluator model that can declare a goal impossible.
  Codex goals run until the agent is satisfied or time runs out, so expect timeouts.
