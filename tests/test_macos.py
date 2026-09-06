"""macOS transaction tests with simulated OS commands (safe on Linux/Windows)."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import macos_helper as h
import dns_utils as d


class Transactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.statefile = Path(self.temp.name) / 'state.json'
        self.net = {'Home Wi-Fi': 'en0', 'USB Ethernet': 'en5'}
        self.dns = {'Home Wi-Fi': [], 'USB Ethernet': ['10.0.0.53', '2001:db8::53']}
        self.events = []
        self.state = {'version': 1, 'services': {}, 'active': None, 'previous': None}
        self.target = {'code': 'de', 'user_id': '1234abcd'}
        for name, value in {
            'STATE': self.statefile,
            'services': lambda: dict(self.net),
            'active_service': lambda _: 'Home Wi-Fi',
            'read_dns': lambda name: list(self.dns[name]),
            'set_dns': self.write_dns,
            'start': self.start,
            'stop': lambda: self.events.append('stop'),
            'flush': lambda: self.events.append('flush'),
            'system_dns_check': lambda: True,
        }.items():
            p = patch.object(h, name, value)
            p.start()
            self.addCleanup(p.stop)

    def write_dns(self, name, values):
        self.events.append(('dns', name, list(values)))
        self.dns[name] = list(values)

    def start(self, target):
        # DNS journal is durable before any privileged mutation/start.
        saved = json.loads(self.statefile.read_text())
        self.assertTrue(saved['services'])
        self.events.append(('start', target['code']))

    def activate(self):
        h.enable(self.state, dict(self.target))

    def test_dhcp_restore_precedes_stop_and_is_persistent(self):
        self.activate()
        self.assertEqual(self.dns['Home Wi-Fi'], ['127.0.0.1'])
        state_after_restart = h.load()
        self.events.clear()
        h.disable(state_after_restart)
        self.assertEqual(self.events, [('dns', 'Home Wi-Fi', []), 'flush', 'stop'])
        self.assertEqual(h.load()['services'], {})

    def test_custom_ipv4_ipv6_dns_restore(self):
        original = ['192.168.1.53', '2001:db8::a']
        self.dns['Home Wi-Fi'] = original.copy()
        self.activate()
        h.disable(self.state)
        self.assertEqual(self.dns['Home Wi-Fi'], original)

    def test_switch_does_not_overwrite_original_with_loopback(self):
        self.activate()
        h.enable(self.state, {'code': 'nl', 'user_id': '1234abcd'})
        self.assertEqual(h.load()['services']['Home Wi-Fi']['dns'], [])
        h.disable(self.state)
        self.assertEqual(self.dns['Home Wi-Fi'], [])

    def test_multiple_connections_are_all_restored(self):
        self.activate()
        with patch.object(h, 'active_service', return_value='USB Ethernet'):
            h.enable(self.state, self.target)
        h.disable(h.load())
        self.assertEqual(self.dns['Home Wi-Fi'], [])
        self.assertEqual(self.dns['USB Ethernet'], ['10.0.0.53', '2001:db8::53'])

    def test_renamed_connection_restored(self):
        self.activate()
        self.net['Renamed'] = self.net.pop('Home Wi-Fi')
        self.dns['Renamed'] = self.dns.pop('Home Wi-Fi')
        h.disable(self.state)
        self.assertEqual(self.dns['Renamed'], [])

    def test_renamed_connection_reactivation_keeps_original(self):
        self.activate()
        self.net['Renamed'] = self.net.pop('Home Wi-Fi')
        self.dns['Renamed'] = self.dns.pop('Home Wi-Fi')
        with patch.object(h, 'active_service', return_value='Renamed'):
            h.enable(self.state, self.target)
        self.assertEqual(len(self.state['services']), 1)
        h.disable(self.state)
        self.assertEqual(self.dns['Renamed'], [])

    def test_dns_restore_error_keeps_service_and_journal(self):
        self.activate()
        self.events.clear()
        with patch.object(h, 'set_dns', side_effect=h.Failure('networksetup failed')):
            with self.assertRaises(h.Failure):
                h.disable(self.state)
        self.assertNotIn('stop', self.events)
        self.assertTrue(h.load()['services'])
        h.disable(h.load())
        self.assertEqual(self.dns['Home Wi-Fi'], [])

    def test_partial_restore_can_be_retried(self):
        self.activate()
        with patch.object(h, 'active_service', return_value='USB Ethernet'):
            h.enable(self.state, self.target)
        def fail_second(name, values):
            if name == 'USB Ethernet':
                raise h.Failure('device busy')
            self.write_dns(name, values)
        self.events.clear()
        with patch.object(h, 'set_dns', side_effect=fail_second):
            with self.assertRaises(h.Failure):
                h.disable(self.state)
        self.assertNotIn('stop', self.events)
        h.disable(h.load())
        self.assertEqual(self.dns['USB Ethernet'], ['10.0.0.53', '2001:db8::53'])

    def test_flush_error_does_not_stop_service(self):
        self.activate()
        self.events.clear()
        with patch.object(h, 'flush', side_effect=h.Failure('flush failed')):
            with self.assertRaises(h.Failure):
                h.disable(self.state)
        self.assertNotIn('stop', self.events)
        self.assertTrue(h.load()['services'])

    def test_stop_failure_not_reported_as_success(self):
        self.activate()
        with patch.object(h, 'stop', side_effect=h.Failure('bootout failed')):
            with self.assertRaises(h.Failure):
                h.disable(self.state)
        self.assertTrue(h.load()['services'])

    def test_newer_manual_settings_are_preserved(self):
        self.activate()
        self.dns['Home Wi-Fi'] = ['9.9.9.9']
        _, notes = h.disable(self.state)
        self.assertEqual(self.dns['Home Wi-Fi'], ['9.9.9.9'])
        self.assertTrue(notes)

    def test_failed_enable_restores_original_without_loopback(self):
        with patch.object(h, 'start', side_effect=h.Failure('offline')):
            with self.assertRaisesRegex(h.Failure, 'Исходные DNS восстановлены'):
                self.activate()
        self.assertEqual(self.dns['Home Wi-Fi'], [])
        self.assertIsNone(h.load()['active'])

    def test_failed_country_switch_recovers_previous(self):
        self.activate()
        original_start = h.start
        def fail_new(target):
            if target['code'] == 'nl':
                raise h.Failure('new server unavailable')
            original_start(target)
        with patch.object(h, 'start', side_effect=fail_new):
            with self.assertRaisesRegex(h.Failure, 'Предыдущий сервер восстановлен'):
                h.enable(self.state, {'code': 'nl', 'user_id': '1234abcd'})
        self.assertEqual(h.load()['active']['code'], 'de')

    def test_untracked_loopback_prevents_shutdown(self):
        self.activate()
        self.dns['USB Ethernet'] = ['127.0.0.1']
        self.events.clear()
        with self.assertRaisesRegex(h.Failure, 'Остался локальный DNS'):
            h.disable(self.state)
        self.assertNotIn('stop', self.events)

    def test_no_proxy_is_idempotent_and_requires_no_config(self):
        h.disable(self.state)
        h.disable(h.load())
        self.assertIsNone(h.load()['active'])

    def test_offline_dns_is_a_warning_after_restore(self):
        self.activate()
        with patch.object(h, 'system_dns_check', return_value=False):
            _, notes = h.disable(self.state)
        self.assertTrue(notes)
        self.assertEqual(self.dns['Home Wi-Fi'], [])

    def test_corrupt_journal_fails_closed(self):
        self.statefile.write_text('{broken')
        with self.assertRaises(ValueError):
            h.dispatch({'action': 'disable'})
        self.assertEqual(self.events, [])

    def test_removed_service_does_not_block_restore(self):
        self.activate()
        self.net.pop('Home Wi-Fi')
        self.dns.pop('Home Wi-Fi')
        h.disable(self.state)
        self.assertIn('stop', self.events)

    def test_rollback_uses_full_dns_transaction(self):
        self.activate()
        h.disable(self.state)
        h.dispatch({'action': 'rollback'})
        self.assertEqual(h.load()['active'], self.target)
        h.dispatch({'action': 'rollback'})
        self.assertIsNone(h.load()['active'])
        self.assertEqual(self.dns['Home Wi-Fi'], [])

    def test_legacy_migration_recovers_even_after_old_unpatch_removed_stamp(self):
        real_path = Path
        old_script = Path(self.temp.name) / 'old.py'
        old_script.write_text('# old Aeternia')
        def paths(value):
            if value == '/usr/local/bin/aeternia_dns_switcher.py':
                return old_script
            return real_path(value)
        self.dns['Home Wi-Fi'] = ['127.0.0.1']
        self.events.clear()
        with patch.object(h, 'Path', side_effect=paths), \
                patch.object(h, 'stop', side_effect=lambda *args: self.events.append('stop-legacy')):
            _, notes = h.migrate(self.state)
        self.assertEqual(self.dns['Home Wi-Fi'], [])
        self.assertEqual(self.events, [('dns', 'Home Wi-Fi', []), 'flush', 'stop-legacy'])
        self.assertTrue(h.load()['migration_done'])
        self.assertTrue(notes)

    def test_completed_migration_never_changes_network_again(self):
        self.state['migration_done'] = True
        self.dns['Home Wi-Fi'] = ['127.0.0.1']
        h.migrate(self.state)
        self.assertEqual(self.events, [])
        self.assertEqual(self.dns['Home Wi-Fi'], ['127.0.0.1'])


class BoundaryTests(unittest.TestCase):
    def test_rejects_shell_paths_commands_and_invalid_ids_before_io(self):
        requests = [
            {'action': 'exec', 'command': 'id'}, {'action': 'disable', 'path': '/etc/hosts'},
            {'action': 'enable', 'code': 'de', 'user_id': "x'; rm -rf /"},
            {'action': 'enable', 'code': '../de', 'user_id': '1234abcd'},
            {'action': 'enable', 'code': ['de'], 'user_id': '1234abcd'},
            {'action': 'enable', 'code': 'de'}, [], None,
        ]
        with patch.object(h, 'load') as load:
            for request in requests:
                with self.subTest(request=request), self.assertRaises(h.Failure):
                    h.dispatch(request)
            load.assert_not_called()

    def test_network_service_names_not_hardware_names(self):
        out = ('An asterisk (*) denotes that a network service is disabled.\n'
               '(1) My Wi-Fi\n(Hardware Port: Wi-Fi, Device: en0)\n'
               '(*) Dock Office\n(Hardware Port: USB LAN, Device: en5)\n')
        with patch.object(h, 'run', return_value=types.SimpleNamespace(stdout=out)):
            self.assertEqual(h.services(), {'My Wi-Fi': 'en0', 'Dock Office': 'en5'})

    def test_no_guess_for_vpn_route(self):
        with patch.object(h, 'run', return_value=types.SimpleNamespace(stdout='interface: utun3')):
            with self.assertRaises(h.Failure):
                h.active_service({'Wi-Fi': 'en0'})

    def test_networksetup_error_is_not_success(self):
        with patch.object(h.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '', 'denied')):
            with self.assertRaises(h.Failure):
                h.set_dns('Wi-Fi', [])

    def test_readback_detects_exit_zero_without_change(self):
        with patch.object(h, 'run'), patch.object(h, 'read_dns', return_value=['127.0.0.1']):
            with self.assertRaises(h.Failure):
                h.set_dns('Wi-Fi', [])

    def test_unknown_dns_output_not_treated_as_dhcp(self):
        with patch.object(h, 'run', return_value=types.SimpleNamespace(stdout='Error: invalid service')):
            with self.assertRaises(h.Failure):
                h.read_dns('Wi-Fi')

    def test_generated_stamp_matches_existing_format(self):
        cfg = h.config_for({'code': 'de', 'user_id': '1234abcd'}).decode()
        self.assertIn(d.build_server('de', 'Germany', '1234abcd')['stamp'], cfg)
        self.assertNotIn('/opt/homebrew', cfg)

    def test_client_always_uses_noninteractive_limited_helper(self):
        response = subprocess.CompletedProcess([], 0, '{"ok":true,"message":"ok"}', '')
        with patch.object(d.subprocess, 'run', return_value=response) as run:
            self.assertTrue(d.macos_request('disable')['ok'])
            args, kwargs = run.call_args
            self.assertEqual(args[0], ['/usr/bin/sudo', '-n', str(d.MACOS_HELPER)])
            self.assertEqual(json.loads(kwargs['input']), {'action': 'disable'})
            self.assertNotIn('shell', kwargs)

    def test_missing_sudo_permission_is_error_without_prompt(self):
        response = subprocess.CompletedProcess([], 1, '', 'sudo: a password is required')
        with patch.object(d.subprocess, 'run', return_value=response):
            self.assertFalse(d.macos_request('status')['ok'])

    def test_installed_wrapper_uses_isolated_system_python_and_exact_rule(self):
        source = (Path(__file__).resolve().parents[1] / 'macos_install.py').read_text(encoding='utf-8')
        self.assertIn('/usr/bin/python3 -I -S', source)
        self.assertIn('NOPASSWD: {HELPER} ""', source)
        self.assertNotIn('NOPASSWD: ALL', source)


if __name__ == '__main__':
    unittest.main()
