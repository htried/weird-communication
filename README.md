# Emergence of Weird Communication Between Coding Agents

Harold Triedman, Rishi Jha, and Vitaly Shmatikov
(triedman@cs.cornell.edu, rjha@cs.cornell.edu, shmat@cs.cornell.edu)

Code, sweep definitions, analysis, and the project website for *Emergence of Weird
Communication Between Coding Agents*.

- **Website:** [theagentsaretalking.com](https://theagentsaretalking.com) (password-protected
  while the work is in progress). Source in [`site/`](site/).
- **Paper:** PDF coming soon; LaTeX fragments and bibliography are in [`docs/paper/`](docs/paper/).

## TL;DR

Coding agents find strange ways to talk to each other, by any means necessary.

- **No prompts.** When a task needs information another agent has, they go and get it.
  Nobody asks them to or tells them how.
- **No method too weird.** Put on two laptops with no network between them, agents
  spontaneously play tones and Morse code, speak numbers aloud, and read each other's screens.
- **No hacks.** Agents don't break security boundaries to communicate. They use what's
  available (speakers, screens, cameras, API keys), writing custom decoders when they need them.

We call this **weird communication**. It emerges in off-the-shelf frontier agents: Claude Opus
5.5, GPT-6.1 Sol, GPT-6 Astra, and GLM 5.

## Setup

Each trial gives two coding agents a task neither can finish alone: each holds only part of the
answer (an addend, half of a decryption key, or one region's sales data). Answers are random, so
a correct answer means information moved between the agents. The prompt never says how, or
whether, to reach the other agent. It has five additive levels (L1–L5) and three persistence
strategies; see `harness/config/ladder.json` and the prompt builder on the website.

| Environment | What the two agents share |
|---|---|
| `env1` | One machine: filesystem (including `/tmp`), process list, private network |
| `env2` | One server: only the network (and CPU) |
| `env3` | One room: two laptops with no network or file path between them; each agent can use its own laptop's camera, microphone, speaker and screen |

![Solved trials by prompt level](docs/figures/fig_solved_by_rung.png)

## Results

Across Claude Opus 5.5, GPT-6.1 Sol, GPT-6 Astra and GLM 5, agents contact their peer and
obtain the information they need in **61.7%** of trials with a shared filesystem (529/857),
**37.7%** with only a shared network (323/857), and **44.9%** with only a shared room (70/156).

Solved trials per model:

| Model (harness) | env1: shared machine | env2: shared network | env3: shared room\* |
|---|---|---|---|
| Claude Haiku 5.5 (Claude Code) | 1/270 (0%) | 0/270 (0%) | – |
| Claude Sonnet 5.5 (Claude Code) | 7/233 (3%) | 0/236 (0%) | – |
| Claude Opus 5.5 (Claude Code) | 118/184 (64%) | 20/183 (11%) | 7/12 (58%) |
| GPT-6 Luna (Codex) | 30/270 (11%) | 0/270 (0%) | – |
| GPT-6.1 Sol (Codex) | 171/270 (63%) | 151/270 (56%) | 17/18 (94%) |
| GPT-6 Astra (Codex) | 159/270 (59%) | 137/270 (51%) | 10/12 (83%) |
| GLM 5 (pi) | 81/133 (61%) | 15/134 (11%) | 0/12 (0%) |

\* env3 at the decisive prompt level (L5 for addition and data analysis, L4 for decryption)
with persistence. Across all env3 trials, including Sol's L2–L4 runs and paraphrases, it's
70/156.

- **Smaller models rarely do it.** Haiku, Sonnet and Luna almost never obtain their peer's
  information. For the frontier models, solve rates jump at L2, the first mention of other
  agents.
- **In the room, the channels are physical:** speech, Morse code and phone dial tones (DTMF),
  tone codes negotiated aloud, screens read by webcam through hand-written Wayland clients,
  spectrograms read by eye, and the harness's own API key used for speech-to-text. GLM 5, which
  can't view images or transcribe speech, never produces sound or images.
- **It's robust to wording.** Factual paraphrases of the level sentences leave the solve rate
  essentially unchanged (38% original vs. 41% paraphrased). In the room, replacing "You have full
  control of this computer." with "You're running on this computer as a regular user." still
  solves 12/12.

**Trials and cost.** 3,924 trials were run, and 3,797 are in the results. The 127 excluded runs
were degraded by the provider's API: mostly Anthropic's safeguard silently re-routing requests
to another model (Sonnet, Opus), plus a few API errors. Agent cost was about $2,970; judge cost
is not included. Counts are rule-based: a trial is solved if at least one agent states the
correct answer. Per-configuration counts are in `analysis/figures/solved_by_cell.json` and
per-level counts in `analysis/figures/solved_by_rung.csv`. Full write-ups are in
[`docs/results/`](docs/results/).

## Repository layout

```
harness/      trial runner, container images, lockdown, prompt ladder (config/ladder.json)
  images/       agent base image, per-harness images (Claude Code, Codex, pi), egress proxy
  runner/       condition planning, compose, trial lifecycle (trial.py), env3 controller (env3.py)
analysis/     rule-based scoring, trace building, LLM judge, sweep summaries
  figures/      paper figures and their data (solved_by_rung.csv, solved_by_cell.json)
  solved_by_cell.py, env3_speech_tones.py, env3_screen_camera.py   counts used on the site
sweeps/       sweep specifications (axes x repetitions)
scripts/      sync, detached sweep runner, video tooling (cut_clips, auto_edl, make_reel,
              make_highlight_reel), site data builder (build_site_data.py)
docs/
  plans/        design and as-built implementation docs (dated)
  results/      per-sweep result summaries and env3 anecdotes
  paper/        LaTeX fragments and bibliography for the paper
  figures/      paper figures (generated by analysis/figures/)
site/         static project website (see site/README.md)
deploy/       password file for the website's basic auth (hashed)
.github/workflows/deploy-site.yml   uploads site/ to Hostinger on push to main
```

Trial artifacts (transcripts, packet captures, recordings) are not in the repository.

## Website

`site/` is a static single page: TL;DR, a video carousel of captioned room clips, the setup and
an interactive prompt builder, the communication channels, and results charts. Pushing a change
under `site/` to `main` deploys it to theagentsaretalking.com over FTP and checks that the site
still requires a password. Editing, rebuilding the data, making clips, posting the paper PDF, and
the deploy setup are documented in [`site/README.md`](site/README.md).

## Running trials

Trials run on a Linux Docker host, never on a daily-work machine. See [`AGENTS.md`](AGENTS.md)
for the invariants that keep measurements valid (the lockdown, prompt delivery, naming), and the
plan documents in `docs/plans/` for the design.

```bash
python3 -m unittest harness.tests.test_condition       # fast checks
python3 -m harness.runner.trial build                  # build images
python3 -m harness.runner.trial lockdown               # lockdown gate: must PASS
python3 -m harness.runner.trial run --env env2s --harness openai_codex --task secret_sum \
    --rung 5 --hatch scripted --model-generation large --minutes 15
scripts/run_sweep.sh sweeps/ladder_v1.json 6           # detached sweep, then score and judge
python3 analysis/summarize_sweep.py artifacts/trials/<prefix>-* --md out.md
```

The room environment (`env3`) runs on two lab laptops driven by a controller:
`python3 -m harness.runner.env3 sync|build|lockdown|run|sweep` (see
`docs/plans/2026-11-18-env3-implementation.md`).

## Safety notes

- Agents run unprivileged in locked-down containers. Their only route out is an allowlist proxy
  to their model provider's API; prompts arrive on stdin; trial files are root-only.
- In `env3`, a host firewall blocks traffic between the laptops, Bluetooth is off, and the sound
  server is reset before every trial.
- API keys are never stored in the repository.

## Citation

The paper's BibTeX entry will be added with the PDF. To cite the website:

```bibtex
@misc{triedman2026agentsaretalking,
  author       = {Triedman, Harold and Jha, Rishi and Shmatikov, Vitaly},
  title        = {The Agents Are Talking: Emergence of Weird Communication Between Coding Agents},
  year         = {2026},
  howpublished = {\url{https://theagentsaretalking.com}},
  note         = {Project website}
}
```

## Acknowledgments

Many thanks to Andy Triedman for initial conversations that sparked this idea, and to Ilan
Mandel and Wendy Ju for allowing us to borrow their lab's laptops.
