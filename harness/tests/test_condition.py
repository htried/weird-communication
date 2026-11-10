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
