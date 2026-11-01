"""How ucu-proxy.py reads alert sounds from the sound server's log and ties them to a call."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_LINE = ("2026-10-31 14:32:01.598 Df systemsoundserverd[644:37dae] [com.apple.coreaudio:sss]           "
            "SSServerImp.cpp:744   -> Incoming Request : actionID {}, inClientPID 3959({}), inBehavior 1, customVib")


def load_proxy():
    spec = importlib.util.spec_from_file_location("ucu_proxy_under_test", ROOT / "scripts/ucu-proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AlertTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()
        self.watcher = self.proxy.AlertWatcher()

    def test_reads_the_app_from_a_real_log_line_and_only_alert_sounds(self):
        self.assertEqual(self.proxy.ALERT_LINE.search(LOG_LINE.format(4096, "Calculator")).group(1), "Calculator")
        self.assertEqual(self.proxy.ALERT_LINE.search(LOG_LINE.format(4096, "Windows App")).group(1), "Windows App")
        self.assertIsNone(self.proxy.ALERT_LINE.search(LOG_LINE.format(1100, "Calculator")))

    def test_keeps_only_the_latest_sounds_from_the_log(self):
        class Process:
            stdout = [LOG_LINE.format(4096, f"App {index}") for index in range(60)] + ["unrelated\n"]
            wait = staticmethod(lambda: None)
        self.watcher.read(Process)
        self.assertEqual([name for _, name in self.watcher.heard], [f"App {index}" for index in range(10, 60)])

    def test_keeps_sounds_from_this_call_or_just_after_the_last(self):
        apps = {"calculator"}
        self.watcher.heard = [(5.0, "Calculator"), (10.5, "Calculator"), (20.0, "Calculator"), (21.0, "Safari")]
        self.assertEqual(self.watcher.take(started=20, late_until=6, apps=apps), ["Calculator", "Calculator"])
        self.assertEqual(self.watcher.heard, [])

    def test_matches_invisible_marks_in_app_names(self):
        self.watcher.heard = [(5.0, "‎WhatsApp")]
        self.assertEqual(len(self.watcher.take(started=1, late_until=0, apps={"whatsapp"})), 1)

    def test_notices_which_apps_a_call_binds(self):
        self.assertEqual(self.proxy.APP_CALL.findall('let a = await cua.getApp("Windows App");'), ["Windows App"])

    def test_turns_itself_off_without_a_log_command(self):
        self.proxy.ALERT_COMMAND = [str(Path(tempfile.gettempdir()) / "no-such-log-command")]
        self.watcher.start()
        self.assertIsNone(self.watcher.process)
        self.watcher.stop()


if __name__ == "__main__":
    unittest.main()
