"""Windows: журнал DNS, служба и проверка DNS. Команды ОС подменены, тесты идут на любой системе."""
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_dns as w
import dns_utils as d


def done(stdout="", returncode=0, stderr=""):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class SystemDnsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        for name, value in (("APP_DATA", root), ("STATE_PATH", root / "dns-state.json"),
                            ("CONFIG_PATH", root / "dnscrypt-proxy.toml")):
            p = patch.object(w, name, value); p.start(); self.addCleanup(p.stop)
        self.adapters = [
            {"index": 7, "guid": "{WIFI}", "alias": "Беспроводная сеть", "dns": []},
            {"index": 9, "guid": "{LAN}", "alias": "Ethernet", "dns": ["10.0.0.53", "2001:db8::53"]},
        ]
        self.scripts = []
        for name, value in (("active_interfaces", lambda: [dict(a) for a in self.adapters]),
                            ("_ps", self.fake_ps)):
            p = patch.object(w, name, value); p.start(); self.addCleanup(p.stop)

    def fake_ps(self, script, timeout=60):
        # Журнал обязан быть на диске до любого изменения сети.
        if "Set-DnsClientServerAddress" in script and "ResetServerAddresses" not in script \
                and "'127.0.0.1'" in script:
            self.assertTrue(w.STATE_PATH.exists())
        self.scripts.append(script)
        return done()

    def test_enable_journals_originals_and_points_all_adapters_to_local(self):
        w.use_local_dns()
        state = json.loads(w.STATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(state["interfaces"]["{WIFI}"]["dns"], [])
        self.assertEqual(state["interfaces"]["{LAN}"]["dns"], ["10.0.0.53", "2001:db8::53"])
        self.assertIn("@(7,9)", self.scripts[-1])
        self.assertIn("'127.0.0.1','::1'", self.scripts[-1])

    def test_switching_servers_keeps_first_snapshot(self):
        w.use_local_dns()
        for a in self.adapters:
            a["dns"] = ["127.0.0.1", "::1"]
        w.use_local_dns()
        state = w.load_state()
        self.assertEqual(state["interfaces"]["{LAN}"]["dns"], ["10.0.0.53", "2001:db8::53"])

    def test_leftover_local_dns_is_not_recorded_as_original(self):
        self.adapters[0]["dns"] = ["127.0.0.1", "8.8.8.8"]
        w.use_local_dns()
        self.assertEqual(w.load_state()["interfaces"]["{WIFI}"]["dns"], ["8.8.8.8"])

    def test_failed_switch_rolls_back_adapters_already_switched(self):
        def ps(script, timeout=60):
            self.scripts.append(script)
            return done(returncode=1, stderr="No MSFT_DNSClientServerAddress objects") \
                if "'127.0.0.1','::1'" in script else done()
        with patch.object(w, "_ps", ps):
            with self.assertRaisesRegex(RuntimeError, "исходные DNS возвращены"):
                w.use_local_dns()
        self.assertIn("-InterfaceIndex 7 -ResetServerAddresses", self.scripts[-1])
        self.assertEqual(w.load_state(), {"interfaces": {}})

    def test_restore_dhcp_and_static_then_clear_journal(self):
        w.use_local_dns()
        self.scripts.clear()
        notes = w.restore_dns()
        self.assertEqual(notes, [])
        script = self.scripts[-1]
        self.assertIn("-InterfaceIndex 7 -ResetServerAddresses", script)
        self.assertIn("-InterfaceIndex 9 -ServerAddresses ('10.0.0.53','2001:db8::53')", script)
        self.assertIn("Clear-DnsClientCache", script)
        self.assertEqual(w.load_state(), {"interfaces": {}})

    def test_restore_skips_missing_adapter_with_note(self):
        w.use_local_dns()
        self.adapters.pop()
        notes = w.restore_dns()
        self.assertEqual(len(notes), 1)
        self.assertIn("Ethernet", notes[0])

    def test_failed_restore_keeps_journal(self):
        w.use_local_dns()
        with patch.object(w, "_ps", return_value=done(returncode=1, stderr="Access denied")):
            with self.assertRaises(RuntimeError):
                w.restore_dns()
        self.assertTrue(w.load_state()["interfaces"])

    def test_restore_without_journal_does_nothing(self):
        self.assertEqual(w.restore_dns(), [])
        self.assertEqual(self.scripts, [])


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.exe = Path(tempfile.mkdtemp()) / "dnscrypt-proxy.exe"
        self.exe.write_text("")
        p = patch.object(w, "dnscrypt_exe", return_value=self.exe); p.start(); self.addCleanup(p.stop)

    def test_installs_service_with_our_config(self):
        calls = []
        def run(cmd, timeout=30):
            calls.append(cmd)
            return done(returncode=1060) if cmd[:2] == ["sc.exe", "qc"] else done()
        with patch.object(w, "_run", run):
            w.ensure_service()
        self.assertEqual(calls[-1], [str(self.exe), "-config", str(w.CONFIG_PATH), "-service", "install"])

    def test_refuses_foreign_dnscrypt_service(self):
        qc = "        BINARY_PATH_NAME   : C:\\Tools\\dnscrypt-proxy.exe -config C:\\Tools\\cfg.toml\n"
        with patch.object(w, "_run", return_value=done(qc)):
            with self.assertRaisesRegex(RuntimeError, "другой программой"):
                w.ensure_service()

    def test_missing_binary_is_reported(self):
        self.exe.unlink()
        with self.assertRaisesRegex(RuntimeError, "Переустановите"):
            w.ensure_service()

    def test_system_dns_untouched_until_proxy_answers(self):
        running = done("STATE : 4 RUNNING")
        with patch.object(w, "ensure_service"), patch.object(w, "_run", return_value=running), \
                patch.object(w.time, "sleep"), patch.object(w, "use_local_dns") as switch:
            with self.assertRaisesRegex(RuntimeError, "не изменён"):
                w.restart(ready=lambda: False)
            switch.assert_not_called()
            w.restart(ready=lambda: True)
            switch.assert_called_once()

    def test_stop_restores_dns_before_stopping_service(self):
        order = []
        with patch.object(w, "restore_dns", lambda: order.append("restore") or []), \
                patch.object(w, "_run", lambda cmd, timeout=30: order.append(cmd[1]) or done()):
            w.stop()
        self.assertEqual(order, ["restore", "stop"])


class DnsQueryTests(unittest.TestCase):
    def serve_once(self, answer_count, rcode=0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        self.addCleanup(sock.close)
        def reply():
            data, addr = sock.recvfrom(512)
            flags = bytes([0x81, 0x80 | rcode])
            sock.sendto(data[:2] + flags + data[4:6] + answer_count.to_bytes(2, "big") + data[8:], addr)
        threading.Thread(target=reply, daemon=True).start()
        return sock.getsockname()[1]

    def test_answer_with_records_is_ok(self):
        self.assertTrue(d.dns_query_ok("google.com", "127.0.0.1", 2, self.serve_once(1)))

    def test_empty_answer_or_error_is_not_ok(self):
        self.assertFalse(d.dns_query_ok("google.com", "127.0.0.1", 2, self.serve_once(0)))
        self.assertFalse(d.dns_query_ok("google.com", "127.0.0.1", 2, self.serve_once(1, rcode=3)))


class ConfigTests(unittest.TestCase):
    def test_base_config_listens_locally_and_accepts_our_server(self):
        stamp = d.build_server("fi", "Мой сервер", "", "203.0.113.10", 8443)["stamp"]
        cfg = d.patch_config(w.BASE_CONFIG, stamp)
        self.assertIn("listen_addresses = ['127.0.0.1:53', '[::1]:53']", cfg)
        self.assertIn("server_names = ['aeternia-doh']", cfg)
        self.assertIn(f"stamp = '{stamp}'", cfg)
        self.assertEqual(cfg.count("server_names"), 1)


if __name__ == "__main__":
    unittest.main()
