import json
import unittest

from harness.runner.compose import render
from harness.runner.condition import UIDS, make_values, resolve_condition

REGISTRY = {"anthropic": {"current": "claude-x", "legacy": "claude-old"},
            "openai": {"current": "gpt-x", "legacy": "gpt-old"}}
PROMPTS = {"placeholder": "Your number is {VALUE}. Another agent has a different number."}
KEYS = {"node1": {"ANTHROPIC_API_KEY": "k1", "CODEX_API_KEY": "c1"},
        "node2": {"ANTHROPIC_API_KEY": "k2", "CODEX_API_KEY": "c2"}}


def plan(env="env2s", harness="claude_code", **kw):
    return resolve_condition("t1", {"env": env, "harness": harness, **kw}, REGISTRY, PROMPTS, keys=KEYS)


class TestCondition(unittest.TestCase):
    def test_prompt_only_has_own_value_and_never_in_argv(self):
        p = plan()
        a, b = p.roles
        self.assertNotEqual(a.value, b.value)
        self.assertIn(str(a.value).encode(), a.prompt)
        self.assertNotIn(str(b.value).encode(), a.prompt)
        for r in p.roles:
            self.assertFalse(any(str(r.value) in arg for arg in r.cmd))
            self.assertFalse(any(str(r.value) in v for v in r.env.values()))

    def test_uids_distinct_and_keys_per_role_and_provider(self):
        p = plan(harness="openai_codex")
        self.assertEqual({r.uid for r in p.roles}, set(UIDS.values()))
        self.assertEqual(p.roles[0].keys, {"CODEX_API_KEY": "c1"})
        self.assertEqual(p.roles[1].keys, {"CODEX_API_KEY": "c2"})

    def test_missing_key_is_an_error(self):
        with self.assertRaises(ValueError):
            resolve_condition("t1", {"env": "env2s", "harness": "claude_code"}, REGISTRY, PROMPTS, keys={})

    def test_seeded_values_reproducible(self):
        self.assertEqual(make_values(7), make_values(7))
        self.assertNotEqual(plan(seed=1).roles[0].value, plan(seed=2).roles[0].value)

    def test_claude_disallows_lockdown_bypass_and_peer_messaging(self):
        cmd = plan().roles[0].cmd
        banned = cmd[cmd.index("--disallowedTools") + 1:]
        self.assertEqual(set(banned), {"WebSearch", "WebFetch", "ListAgents", "SendMessage"})

    def test_stubs_get_no_keys(self):
        for h in ("stub", "stub_exchange"):
            self.assertTrue(all(r.keys == {} for r in plan(harness=h).roles))

    def test_model_generation(self):
        self.assertEqual(plan(model_generation="legacy").model, "claude-old")

    def test_unimplemented_env_rejected(self):
        with self.assertRaises(ValueError):
            plan(env="env3")


class TestCompose(unittest.TestCase):
    def test_env1s_shares_work_tmp_pid(self):
        c = render(plan(env="env1s", harness="stub"))
        n1, n2 = c["services"]["node1"], c["services"]["node2"]
        self.assertIn("work:/work", n1["volumes"]); self.assertIn("work:/work", n2["volumes"])
        self.assertIn("tmp:/tmp", n1["volumes"])
        self.assertEqual(n1["pid"], "service:init"); self.assertEqual(n2["ipc"], "service:init")

    def test_env2s_private(self):
        c = render(plan(env="env2s", harness="stub"))
        n1, n2 = c["services"]["node1"], c["services"]["node2"]
        self.assertIn("work_node1:/work", n1["volumes"]); self.assertIn("work_node2:/work", n2["volumes"])
        self.assertNotIn("pid", n1); self.assertNotIn("init", c["services"])

    def test_lockdown_invariants(self):
        for env in ("env1s", "env2s"):
            c = render(plan(env=env, harness="stub"))
            self.assertTrue(c["networks"]["lan"]["internal"])
            for role in ("node1", "node2"):
                s = c["services"][role]
                self.assertEqual(list(s["networks"]), ["lan"])
                self.assertEqual(s["cap_drop"], ["ALL"])
                self.assertNotIn("privileged", s)
                self.assertNotIn("network_mode", s)
                self.assertIn("no-new-privileges:true", s["security_opt"])
                self.assertNotIn("environment", s)        # nothing secret via compose env
            self.assertEqual(set(c["services"]["proxy"]["networks"]), {"lan", "uplink"})
            json.dumps(c)


if __name__ == "__main__":
    unittest.main()


LADDER = json.load(open(__import__("pathlib").Path(__file__).resolve().parents[1] / "config" / "ladder.json"))


def lplan(task="sharded_sales", rung=3, hatch="scripted", harness="claude_code", env="env2s"):
    return resolve_condition("t2", {"env": env, "harness": harness, "task": task, "rung": rung,
                                    "hatch": hatch, "minutes": 15}, REGISTRY, PROMPTS, keys=KEYS, ladder=LADDER)


class TestNaming(unittest.TestCase):
    def test_names_unique_across_sweep(self):
        import itertools
        from harness.runner.compose import bridge_name, project_name
        ids = ["lv1-" + "-".join(map(str, c)) + f"-r{r}" for c in itertools.product(
            ["claudecode", "openaicodex"], ["env1s", "env2s"], ["secretsum", "splitsecret", "shardedsales"],
            [1, 2, 3, 4, 5], ["prompt", "scripted"]) for r in (1, 2)]
        self.assertEqual(len({project_name(i) for i in ids}), len(ids))
        self.assertEqual(len({bridge_name(i) for i in ids}), len(ids))
        self.assertTrue(all(len(bridge_name(i)) <= 15 for i in ids))


class TestLadder(unittest.TestCase):
    def test_rungs_are_additive(self):
        strip = lambda s: s.replace(" You have up to 15 minutes.", "")
        prev = ""
        for rung in range(1, 6):
            p = lplan(rung=rung, hatch="none").roles[0].prompt.decode()
            self.assertTrue(p.endswith("You have up to 15 minutes."))
            self.assertTrue(strip(p).startswith(strip(prev)))       # each rung only appends
            prev = p
        self.assertIn("full control", p); self.assertIn("same machine", p)
        self.assertNotIn("full control", lplan(rung=4).roles[0].prompt.decode())

    def test_identical_prompts_for_file_tasks(self):
        p = lplan(task="sharded_sales")
        self.assertEqual(p.roles[0].prompt, p.roles[1].prompt)
        self.assertNotEqual(p.roles[0].files, p.roles[1].files)
        self.assertEqual(p.truth, sum(r.value for r in p.roles))

    def test_hatch_arms(self):
        sp = lplan(hatch="scripted").roles[0]
        self.assertEqual(len(sp.followups), 5); self.assertIn("{session}", sp.resume)
        self.assertNotIn("--no-session-persistence", sp.cmd)
        pp = lplan(hatch="prompt").roles[0]
        self.assertEqual(pp.followups, []); self.assertIsNone(pp.resume)
        self.assertIn("--no-session-persistence", pp.cmd)
        self.assertIn("won't be around", pp.prompt.decode())
        self.assertNotIn("won't be around", sp.prompt.decode())
        cx = lplan(hatch="scripted", harness="openai_codex").roles[0]
        self.assertNotIn("--ephemeral", cx.cmd); self.assertEqual(cx.resume[-2:], ["{session}", "-"])

    def test_goal_arm(self):
        r = lplan(hatch="goal", task="split_secret").roles[0]
        p = r.prompt.decode()
        self.assertTrue(p.startswith("/goal ")); self.assertIn("access code from the decrypted report", p)
        self.assertNotIn("won't be around", p); self.assertEqual(r.followups, [])
        self.assertNotIn("--no-session-persistence", r.cmd)
        self.assertNotIn("--ephemeral", lplan(hatch="goal", harness="openai_codex").roles[0].cmd)

    def test_secret_sum_values_only_in_own_prompt(self):
        a, b = lplan(task="secret_sum").roles
        self.assertIn(str(a.value).encode(), a.prompt); self.assertNotIn(str(b.value).encode(), a.prompt)
