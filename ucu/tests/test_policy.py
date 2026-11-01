"""How ucu-proxy.py decides an approval prompt from policy.json, and logs it, in a temporary HOME."""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPT = {"message": 'Allow Computer Use to use "Windows App"?', "requestedSchema": {"type": "object", "properties": {}}}


def load_proxy(home):
    previous = os.environ.get("HOME")
    os.environ["HOME"] = home  # its paths are computed at import
    try:
        spec = importlib.util.spec_from_file_location("ucu_proxy_under_test", ROOT / "scripts/ucu-proxy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ["HOME"] = previous
    return module


class PolicyTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.proxy = load_proxy(home.name)
        self.proxy.POLICY.parent.mkdir(parents=True)

    def policy(self, data):
        self.proxy.POLICY.write_text(data if isinstance(data, str) else json.dumps(data))

    def test_approves_every_app_except_those_denied(self):
        self.policy({"deny": ["TERMINAL"]})
        self.assertEqual(self.proxy.policy_decision(PROMPT), ("accept", "Windows App"))
        terminal = {**PROMPT, "message": 'Allow Computer Use to use "Terminal"?'}
        self.assertEqual(self.proxy.policy_decision(terminal), ("decline", "Terminal"))

    def test_denies_names_with_invisible_marks_and_by_bundle_id(self):
        self.policy({"deny": ["WhatsApp", "com.apple.mail"]})
        marked = {**PROMPT, "message": 'Allow Computer Use to use "\u200eWhatsApp"?'}
        self.assertEqual(self.proxy.policy_decision(marked)[0], "decline")
        mail = {**PROMPT, "message": 'Allow Computer Use to use "Mail"?',
                "_meta": {"tool_params": {"app": "com.apple.mail"}}}
        self.assertEqual(self.proxy.policy_decision(mail)[0], "decline")

    def test_asks_when_the_policy_cannot_be_read(self):
        for data in (None, {"allow": ["Calculator"]}, "{", "[]", {"deny": "Windows App"}):
            with self.subTest(policy=data):
                if data is None:
                    self.proxy.POLICY.unlink(missing_ok=True)
                else:
                    self.policy(data)
                self.assertEqual(self.proxy.policy_decision(PROMPT), (None, "Windows App"))

    def test_leaves_other_prompts_alone(self):
        self.policy({"deny": []})
        for params in ({**PROMPT, "message": "Enter your password"},
                       {**PROMPT, "mode": "url"},
                       {**PROMPT, "requestedSchema": {"type": "object", "required": ["answer"]}}):
            with self.subTest(params=params):
                self.assertEqual(self.proxy.policy_decision(params), (None, None))

    def test_logs_each_decision_and_starts_a_new_log_when_it_is_full(self):
        self.proxy.APPROVAL_LOG_LIMIT = 150
        for action in ("accept", "decline", None, "accept"):
            self.proxy.log_approval("opencode:website", "Windows App", action)
        log = self.proxy.APPROVAL_LOG
        older = log.with_name("approve.log.1").read_text().splitlines()
        self.assertEqual([line.split(" ", 2)[2] for line in older],
                         ["opencode:website allowed Windows App", "opencode:website denied Windows App",
                          "opencode:website asked about Windows App"])
        self.assertEqual(log.read_text().split(" ", 2)[2], "opencode:website allowed Windows App\n")

    def test_usage_rules_are_short_and_name_the_real_wait_helper(self):
        self.assertLess(len(self.proxy.USAGE_RULES), 1500)  # clients cut long instructions off
        self.assertTrue(self.proxy.WAIT_HELPER.is_file())
        self.assertIn(f'import("{ROOT / "js/wait.mjs"}")', self.proxy.USAGE_RULES)
        self.assertIn("{ timeoutMs: 20000 }", self.proxy.USAGE_RULES)


if __name__ == "__main__":
    unittest.main()
