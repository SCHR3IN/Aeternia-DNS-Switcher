"""Linux: systemd-resolved follows dnscrypt-proxy on switch and is released on no-proxy."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dns_utils as d


class ResolvedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dropin = Path(self.temp.name) / "resolved.conf.d" / "aeternia-dns.conf"
        for name, value in (("IS_LINUX", True), ("RESOLVED_DROPIN", self.dropin)):
            p = patch.object(d, name, value); p.start(); self.addCleanup(p.stop)

    def test_enable_writes_dropin_and_restarts_resolved(self):
        with patch.object(d.shutil, "which", return_value="/usr/bin/resolvectl"), \
                patch.object(d, "run_cmd") as run:
            d.resolved_use_proxy(True)
        self.assertIn("DNS=127.0.2.1", self.dropin.read_text())
        self.assertIn("Domains=~.", self.dropin.read_text())
        run.assert_called_once_with(["systemctl", "restart", "systemd-resolved"], timeout=10)

    def test_disable_removes_dropin(self):
        self.dropin.parent.mkdir(parents=True); self.dropin.write_text("x")
        with patch.object(d.shutil, "which", return_value="/usr/bin/resolvectl"), patch.object(d, "run_cmd"):
            d.resolved_use_proxy(False)
        self.assertFalse(self.dropin.exists())

    def test_noop_without_resolved(self):
        with patch.object(d.shutil, "which", return_value=None), patch.object(d, "run_cmd") as run:
            d.resolved_use_proxy(True)
        self.assertFalse(self.dropin.exists()); run.assert_not_called()

    def test_restart_and_stop_call_resolver_switch(self):
        ok = Mock(returncode=0, stderr="")
        with patch.object(d, "IS_MACOS", False), patch.object(d, "run_cmd", return_value=ok), \
                patch.object(d, "resolved_use_proxy") as switch:
            self.assertTrue(d.restart_services()[0]); switch.assert_called_with(True)
            self.assertTrue(d.stop_services()[0]); switch.assert_called_with(False)


if __name__ == "__main__":
    unittest.main()
