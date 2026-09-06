import ast
import base64
import importlib
import json
from pathlib import Path
import re
import sys
import types
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


class DeliveryTests(unittest.TestCase):
    def test_uninstall_failure_preserves_recovery_tools(self):
        try:
            import pwd
        except ImportError:
            sys.modules['pwd'] = types.ModuleType('pwd')
        installer = importlib.import_module('macos_install')
        with patch.object(installer, 'helper', side_effect=RuntimeError('DNS restore failed')), \
                patch.object(Path, 'unlink') as unlink, \
                patch.object(installer.shutil, 'rmtree') as rmtree:
            with self.assertRaises(RuntimeError):
                installer.uninstall()
            unlink.assert_not_called()
            rmtree.assert_not_called()

    def test_embedded_installer_matches_all_sources(self):
        installer = (REPO / 'aeternia-dns-installer.sh').read_text(encoding='utf-8')
        files = re.findall(r'echo "([A-Za-z0-9+/=]+)" \| decode_base64 > "\$AETERNIA_TMP/([^"/]+)"', installer)
        self.assertEqual({name for _, name in files}, {
            'aeternia_dns_switcher.py', 'dns_utils.py', 'install.sh', 'logo.png',
            'macos_helper.py', 'macos_install.py'})
        for payload, name in files:
            with self.subTest(name=name):
                self.assertEqual(base64.b64decode(payload), (REPO / name).read_bytes())

    def test_system_python_39_syntax_compatibility(self):
        for name in ('macos_helper.py', 'macos_install.py', 'dns_utils.py', 'aeternia_dns_switcher.py'):
            with self.subTest(name=name):
                ast.parse((REPO / name).read_text(encoding='utf-8'), feature_version=(3, 9))

    def test_shell_files_have_unix_line_endings(self):
        for path in REPO.glob('*.sh'):
            with self.subTest(path=path.name):
                self.assertNotIn(b'\r', path.read_bytes())


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import curses
        except ImportError:
            sys.modules['curses'] = types.ModuleType('curses')
        cls.ui = importlib.import_module('aeternia_dns_switcher')

    def test_unprivileged_switch_and_disable_never_run_legacy_code(self):
        app = self.ui.App.__new__(self.ui.App)
        app.is_root = False
        app.user_id = '1234abcd'
        app._macos_operation = Mock()
        with patch.object(self.ui, 'IS_MACOS', True), patch.object(self.ui, 'backup_config') as backup:
            app.apply_server({'code': 'de'})
            app.apply_no_proxy()
            app.do_rollback()
            backup.assert_not_called()
        self.assertEqual(app._macos_operation.call_args_list[0].args, ('enable',))
        self.assertEqual(app._macos_operation.call_args_list[1].args, ('disable',))
        self.assertEqual(app._macos_operation.call_args_list[2].args, ('rollback',))

    def test_launch_does_not_request_root_or_confirmation(self):
        with patch.object(self.ui, 'IS_MACOS', True), \
                patch.object(self.ui.os, 'geteuid', return_value=501, create=True), \
                patch.object(self.ui, 'print_banner'), \
                patch.object(self.ui, 'is_dnscrypt_installed', return_value=True), \
                patch.object(self.ui, 'load_servers', return_value=([{'code': 'de'}], '1234abcd')), \
                patch.object(self.ui, 'macos_request', return_value={'ok': True}), \
                patch.object(self.ui.time, 'sleep'), \
                patch('builtins.input', side_effect=AssertionError('No prompts on launch')), \
                patch('builtins.print'):
            self.assertTrue(self.ui.pre_flight_check())

    def test_disable_failure_does_not_clear_active_ui_state(self):
        app = self.ui.App.__new__(self.ui.App)
        app._step = Mock()
        app._log = Mock()
        app._refresh_macos_state = Mock()
        app._ping_background = Mock()
        app.draw = Mock()
        app.current_server = {'code': 'de'}
        with patch.object(self.ui, 'macos_request', return_value={
                'ok': False, 'message': 'restore failed', 'warnings': []}):
            app._macos_operation('disable')
        app._log.assert_called_with('restore failed', 'err')
        self.assertEqual(app.current_server, {'code': 'de'})


if __name__ == '__main__':
    unittest.main()
