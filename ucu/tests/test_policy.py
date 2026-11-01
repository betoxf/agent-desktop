"""How ucu-proxy.py applies policy.json and logs what it decides."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_proxy import Proxy  # noqa: E402


class PolicyTest(unittest.TestCase):
    def server(self, **deny):
        server = Proxy(self)
        server.policy(**deny)
        server.initialize()
        return server

    def test_uses_every_app_except_those_denied(self):
        server = self.server(deny=["Mail"])
        self.assertFalse(server.look()["result"]["isError"])
        reply = server.look(app="Mail")
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("deny list", server.text(reply))

    def test_never_reads_or_touches_a_denied_app(self):
        server = self.server(deny=["Mail"])
        server.look(app="Mail")
        server.act({"do": "press", "keys": "Return"}, app="Mail")
        self.assertEqual(server.engine_calls(), [])

    def test_denies_names_with_invisible_marks_and_any_case(self):
        server = self.server(deny=["\u200eWhatsApp"])
        self.assertTrue(server.look(app="whatsapp")["result"]["isError"])

    def test_refuses_every_app_when_the_policy_cannot_be_read(self):
        server = Proxy(self, policy=False)
        server.initialize()
        self.assertTrue(server.look()["result"]["isError"])
        self.assertEqual(server.engine_calls(), [])

    def test_logs_each_app_once_with_who_asked(self):
        server = self.server(deny=["Mail"])
        server.look()
        server.look()
        server.look(app="Mail")
        self.assertEqual(server.approvals(), ["test-client:My-Project allowed Calculator",
                                              "test-client:My-Project denied Mail"])

    def test_starts_a_new_log_when_it_is_full(self):
        server = self.server(deny=[])
        log = Path(server.home.name) / "Library/Logs/ucu/approve.log"
        log.parent.mkdir(parents=True)
        log.write_text("x" * 1_000_001)
        server.look()
        self.assertTrue(log.with_name("approve.log.1").exists())
        self.assertEqual(len(server.approvals()), 1)


if __name__ == "__main__":
    unittest.main()
