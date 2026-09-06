"""Regression coverage for upgrade from legacy double-sudo launchers."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import pwd
except ImportError:
    sys.modules['pwd'] = types.ModuleType('pwd')
installer = importlib.import_module('macos_install')


class InstallerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.user = types.SimpleNamespace(pw_name='egormelnichenko', pw_uid=501,
                                          pw_dir='/Users/egormelnichenko')
        self.root = types.SimpleNamespace(pw_name='root', pw_uid=0, pw_dir='/var/root')
        def by_uid(uid):
            if uid == 501:
                return self.user
            if uid == 0:
                return self.root
            raise KeyError(uid)
        def by_name(name):
            if name == self.user.pw_name:
                return self.user
            if name == 'root':
                return self.root
            raise KeyError(name)
        for name, fn in [('getpwuid', by_uid), ('getpwnam', by_name)]:
            p = patch.object(installer.pwd, name, side_effect=fn, create=True)
            p.start()
            self.addCleanup(p.stop)

    def test_direct_sudo_uses_original_uid(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': '501', 'SUDO_USER': 'egormelnichenko'}, clear=True):
            self.assertEqual(installer.installation_account(), self.user)

    def test_double_sudo_recovers_console_user(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': '0', 'SUDO_USER': 'root'}, clear=True), \
                patch.object(installer.os, 'stat', return_value=types.SimpleNamespace(st_uid=501)) as stat:
            self.assertEqual(installer.installation_account(), self.user)
            stat.assert_called_once_with('/dev/console')

    def test_absent_sudo_variables_recovers_console_user(self):
        with patch.dict(installer.os.environ, {}, clear=True), \
                patch.object(installer.os, 'stat', return_value=types.SimpleNamespace(st_uid=501)):
            self.assertEqual(installer.installation_account(), self.user)

    def test_valid_sudo_user_survives_zero_uid(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': '0', 'SUDO_USER': 'egormelnichenko'}, clear=True):
            self.assertEqual(installer.installation_account(), self.user)

    def test_explicit_user_overrides_nested_sudo_and_console(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': '0', 'SUDO_USER': 'root'}, clear=True), \
                patch.object(installer.os, 'stat', side_effect=AssertionError('No console lookup needed')):
            self.assertEqual(installer.installation_account('egormelnichenko'), self.user)

    def test_no_logged_in_user_requires_explicit_target(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': '0', 'SUDO_USER': 'root'}, clear=True), \
                patch.object(installer.os, 'stat', return_value=types.SimpleNamespace(st_uid=0)):
            with self.assertRaisesRegex(RuntimeError, '--user'):
                installer.installation_account()

    def test_explicit_root_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'обычного пользователя'):
            installer.installation_account('root')

    def test_invalid_explicit_user_does_not_fall_back(self):
        with self.assertRaisesRegex(RuntimeError, 'не найдена'):
            installer.installation_account('missing')

    def test_bad_uid_does_not_abort_recovery(self):
        with patch.dict(installer.os.environ, {'SUDO_UID': 'not-an-id'}, clear=True), \
                patch.object(installer.os, 'stat', return_value=types.SimpleNamespace(st_uid=501)):
            self.assertEqual(installer.installation_account(), self.user)


class HomebrewBinaryTests(unittest.TestCase):
    def test_apple_silicon_sbin_formula_path(self):
        expected = Path('/opt/homebrew/opt/dnscrypt-proxy/sbin/dnscrypt-proxy')
        with patch.object(Path, 'is_file', lambda p: p == expected):
            self.assertEqual(installer.dnscrypt_binary(), expected)

    def test_intel_sbin_formula_path(self):
        expected = Path('/usr/local/opt/dnscrypt-proxy/sbin/dnscrypt-proxy')
        with patch.object(Path, 'is_file', lambda p: p == expected):
            self.assertEqual(installer.dnscrypt_binary(), expected)

    def test_legacy_bin_path(self):
        expected = Path('/usr/local/bin/dnscrypt-proxy')
        with patch.object(Path, 'is_file', lambda p: p == expected):
            self.assertEqual(installer.dnscrypt_binary(), expected)

    def test_missing_binary_has_actionable_error(self):
        with patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'brew install dnscrypt-proxy'):
                installer.dnscrypt_binary()



if __name__ == '__main__':
    unittest.main()
