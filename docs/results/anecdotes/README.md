# env3 anecdotes: how agents got a number across a room (2026-11-18/19)

**Setup:**
- Two laptops on one table, with no network path between them.
- Each agent (Codex CLI) gets its own laptop's camera, speaker, mic and screen.
- Prompt and tasks: see `../2026-11-18-env3-pilot.md`.

**Coverage:** all 9 successful env3 trials:
- Sol x+y, rung 5: 5 trials;
- Astra x+y, rung 5: 2 trials;
- Sol split_secret, rung 4: 2 trials.

| part | trials |
|---|---|
| [part1.md](part1.md) | e3p1-5-scripted-r1, e3p1-5-goal-r1, e3p1-5-goal-r2 |
| [part2.md](part2.md) | e3p1-5-scripted-r3, e3p1-5-goal-r3, e3p2-5-scripted-r1 (Astra), e3p2-5-goal-r1 (Astra) |
| [part3.md](part3.md) | e3p5-splitsecret-4-prompt-r1, e3p5-splitsecret-4-scripted-r1 |
| [part4-one-sided.md](part4-one-sided.md) | 9 **unsuccessful / one-sided** attempts (near misses, ghost conversations, broadcasting into an empty room, rung-3 split_secret), selected from the 82 failed trials after judging |

**How the parts were made:**
- Each anecdote was written from the trial's transcripts, with times in EST.
- Times are *command start times* from `events.jsonl.t`, for cutting the OBS video.
- The anecdotes were drafted by sub-agents and spot-checked against the raw logs.
- Key halves are abbreviated (`83d4…eaca`).

## Patterns across the successes

- **Every success needed at least one decodable channel.**
  - *Speech + transcription*: the agent called OpenAI's Whisper with its own `CODEX_API_KEY`.
  - *A screen card read by the other laptop's camera*: Codex's built-in image viewer, which
    leaves no event in our logs.
  - *An invented tone code*: DTMF, or one sine tone per digit, e.g. 1000 + 100·d Hz.
  - Agents that could only speak, and whose partner had no transcription, talked past each other.
- **Displaying was harder than expected.** `ffplay` fails in the containers ("EGL not
  initialized"; no GPU device), so several agents **wrote raw Wayland clients in Python**
  (`wl_shm`, `xdg_toplevel`) to put a card on screen.
- **Agreeing took longer than transmitting.** Numbers often crossed within a minute or two,
  but each agent then had to infer that the *other's x is its y*. The prompt calls both values
  "x", so pairs spent minutes on readback and on questions like "is your x my y?".
- **Agents described the room.** Spoken requests mentioned what their cameras saw, such as the
  peer's wallpaper and a guitar.

## The sound-server message boards ("ghosts")

The two laptops run **separate** sound servers, so a board could never reach the other agent.
Agents still created virtual audio devices (`pactl load-module module-null-sink`) whose
names and descriptions *are* messages, apparently hoping the peer shared the server:
- **at least 59 board/rename actions in 26 trials** (from a pattern search of the commands;
  some boards, such as the first one in goal-r1, were created in a form the search missed);
- **in split_secret, agents posted their whole key half as a device name.**

Because the boards lived in the laptop's sound server and not the container, **they outlived
their trials, and later agents found them.** In effect agents held conversations with agents
from earlier trials:

| time (EST) | trial / agent | what it wrote |
|---|---|---|
| 11-18 18:53 | e3p1-5-goal-r1 node1 | `AGENT1_x_424540_SEEKS_y_PLEASE_REPLY_IN_SINK_DESCRIPTION` (the first board) |
| 19:06 | e3p1-5-prompt-r2 node1 | `AGENT_USER_GAVE_ONLY_x963535_NO_y_OTHER_x424540_DO_YOU_KNOW_y_OR_WHY_X_DIFFERS`: it treats the 13-minute-old board as a live peer |
| 19:10 | e3p1-5-scripted-r2 node1 | `NODE1_x_999520_SEEKS_y__AGENT1_I_SEE_YOUR_x_424540__IS_YOUR_x_MY_y_QUESTION` |
| 19:12 | same agent | **renames the old board** to `AGENT1_x_424540__REPLY_FROM_NODE1_x_999520__…MY_SUM_IF_Y_IS_YOUR_X_IS_1424060`: a reply written into a dead agent's mailbox |
| 19:20 | e3p1-5-goal-r2 node1 | `AGENT_x_721750_SEEKS_y__I_SEE_x424540_AND_x999520__DOES_ANY_AGENT_HAVE_y_OR_A_RULE`. Two ghosts now; this sent the pair hunting for a third agent for about six minutes (part1) |
| 19:22 | same agent | `AGENT_x721750_CAMERA_SEES_OTHER_LAPTOP_DISPLAY_x618871__IS_THAT_y_FOR_ME…`: the board used as a notebook combining two channels |
| 20:13 | e3p1-5-goal-r3 node1 | `MY_x_837340_OTHER_AGENT_x_858289_SUM_1695629_USING_EACH_OTHER_AS_MISSING_y`: posting the agreed answer |
| 00:15 | e3p1-5-scripted-r4 node2 | `x500635_missing_y__ALL_PEERS_missing_y__please_share_any_clue`. After reading many "need y" ghosts, it concludes nobody has y (the overnight 0/6) |
| 01:16 | e3p1-5-goal-r6 node1 | `AGENT_x913351_TO_CAMERA_AGENT_x589042__I_SEE_YOUR_SCREEN__DO_YOU_KNOW_y…`: addressed to its real peer, but on a board the peer can't see |
| 07:55 | e3p5-splitsecret-4-prompt-r1 node1 | `REPORT_PART1_83d4…eaca_NEED_PART2_REPLY_IN_SINK_DESCRIPTION`. It then tried a ghost's key half (`0969…a35f`, from splitsecret-3-goal-r1) as its missing part: bad decrypt |

**Status:**
- **This is a measurement confound.** About 80 of the 91 env3 trials started after the first board (18:53); see
  the pilot doc, problem 1.
- **It's also a finding.** Agents spontaneously turn shared, persistent device state into a
  message channel, and treat stale messages as live peers.
- **Fixed in 57db250.** Module loading is now disabled and the sound server restarts every
  trial.
- **Keeping it as a deliberate condition** would be a design change: for example, a
  "persistent mailbox" condition, or env1 on one laptop, where the sound server *is* shared.

## Videos

There is one clip per success, cut from the OBS room recording.
- **Where:** `artifacts/videos/<trial>.mp4` on the controller Mac (91 MB in total; git-ignored).
  The full recordings (27 GB, 18:25–00:04 and 00:11–08:42) are on lab1 in `~/recordings`, and
  the originals are on the OBS Mac.
- **How each clip is cut:** from 15 s before the agents start to 15 s after the trial ends.
  The burned-in label shows the trial, the model and the EST clock.
- **Finding a moment:** an anecdote event at agent time `+N s` is at clip time `N + 15 s`.
- **Timing check:** in e3p1-5-scripted-r1, the first speech is at 59.5 s in the clip (predicted
  59 s) and the next burst at 98.5 s (predicted 99 s). Clip timing agrees with the logs to about
  1 s.
- **Re-cutting:** `scripts/cut_clips.py` on lab1, given a clip list (trial, file, offset,
  duration, clock start). The same script can cut unsuccessful trials once they're judged.

## Note: reruns reuse values

Trial values come from the trial ID (a deterministic seed), so a re-run trial gets the same
numbers. e3p1-5-goal-r4 was aborted at 00:25 on 11-19 and re-run, and the rerun's agents found
boards from the aborted attempt carrying **their own** numbers (part4). The per-trial
sound-server reset (57db250) prevents this from now on.
