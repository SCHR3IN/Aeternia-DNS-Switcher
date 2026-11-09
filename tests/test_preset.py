"""Встроенный пресет серверов: используется при первом запуске и не перезаписывает свой список."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dns_utils as d


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.servers = root / "profile" / "servers.json"
        self.preset = root / "app" / "servers.preset.json"
        self.preset.parent.mkdir()
        for name, value in (("SERVERS_PATH", self.servers), ("PRESET_PATH", self.preset)):
            p = patch.object(d, name, value); p.start(); self.addCleanup(p.stop)
        self.own = d.build_server("fi", "Свой", "", "203.0.113.10", 8443)

    def test_first_run_seeds_servers_from_preset(self):
        self.preset.write_text(json.dumps({"user_id": "", "servers": [self.own]}, ensure_ascii=False), encoding="utf-8")
        servers, user_id = d.load_servers()
        self.assertEqual([s["name"] for s in servers], ["Свой"])
        self.assertTrue(self.servers.exists())

    def test_existing_list_is_not_overwritten(self):
        d.save_servers([d.build_server("de", "Германия", "aabbccdd")], "aabbccdd")
        self.preset.write_text(json.dumps({"user_id": "", "servers": [self.own]}), encoding="utf-8")
        servers, user_id = d.load_servers()
        self.assertEqual((servers[0]["code"], user_id), ("de", "aabbccdd"))

    def test_no_preset_or_broken_preset_means_empty(self):
        self.assertEqual(d.load_servers(), ([], ""))
        self.preset.write_text("{broken", encoding="utf-8")
        self.assertEqual(d.load_servers(), ([], ""))
        self.assertFalse(self.servers.exists())


if __name__ == "__main__":
    unittest.main()
