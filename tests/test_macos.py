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


KEY = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8='  # any 32 bytes, base64
PROFILE = {'private_key': KEY, 'address_v4': '172.16.0.2', 'address_v6': '2606:4700:110::1',
           'peer_public_key': KEY, 'endpoint_host': 'engage.cloudflareclient.com', 'endpoint_port': 2408,
           'awg': {'jc': 4, 'jmin': 40, 'jmax': 70, 's1': 0, 's2': 0, 's3': 0, 's4': 0,
                   'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'i1': '<b 0xc0000000010800>', 'i2': '<b 0x4001>'}}


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

    def test_enable_mode_is_validated_and_forwarded(self):
        with patch.object(h, 'load', return_value={'active': None, 'services': {}}), \
                patch.object(h, 'enable', return_value=('ok', [])) as enable:
            h.dispatch({'action': 'enable', 'code': 'de', 'user_id': '1234abcd', 'mode': 'navis'})
            self.assertEqual(enable.call_args[0][1]['mode'], 'navis')
            h.dispatch({'action': 'enable', 'code': 'de', 'user_id': '1234abcd'})
            self.assertEqual(enable.call_args[0][1]['mode'], 'dns')
        for bad in ({'mode': 'tun'}, {'mode': 1}, {'mode': 'navis', 'engine': '/bin/sh'}):
            with self.subTest(bad=bad), self.assertRaises(h.Failure):
                h.dispatch({'action': 'enable', 'code': 'de', 'user_id': '1234abcd', **bad})

    def test_warp_action_accepts_only_register_or_validated_atlas_profile(self):
        with patch.object(h, 'register_warp', return_value='registered') as register, \
                patch.object(h, 'load', return_value={'active': None, 'services': {}}):
            self.assertEqual(h.dispatch({'action': 'warp', 'source': 'register'})['message'], 'registered')
            register.assert_called_once()
        with patch.object(h, 'save_profile') as save, \
                patch.object(h, 'load', return_value={'active': None, 'services': {}}):
            h.dispatch({'action': 'warp', 'source': 'atlas', 'profile': PROFILE})
            self.assertEqual(save.call_args[0][0]['source'], 'atlas')
        for request in ({'action': 'warp', 'source': 'file', 'profile': PROFILE},
                        {'action': 'warp', 'source': 'register', 'profile': PROFILE},
                        {'action': 'warp'}):
            with self.subTest(request=request), self.assertRaises(h.Failure):
                h.dispatch(request)

    def test_accepts_hex_ids_between_8_and_64_chars(self):
        for user_id in ('1234abcd', 'b8bcea266753a420a5b754e78ca7df56', 'A' * 64):
            with self.subTest(user_id=user_id), \
                    patch.object(h, 'load', return_value={'active': None, 'services': {}}), \
                    patch.object(h, 'enable', return_value=('ok', [])) as enable:
                h.dispatch({'action': 'enable', 'code': 'de', 'user_id': user_id})
                enable.assert_called_once()
        for user_id in ('1234abc', 'A' * 65, 'b8bcea266753a420a5b754e78ca7df5g'):
            with self.subTest(user_id=user_id), self.assertRaises(h.Failure):
                h.dispatch({'action': 'enable', 'code': 'de', 'user_id': user_id})

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


class NavisTests(Transactions):
    """NAVIS reuses the DNS journal: the tunnel address is 'ours' for restore purposes."""

    def test_navis_points_dns_at_tunnel_and_restores(self):
        h.enable(self.state, {'code': 'de', 'user_id': '1234abcd', 'mode': 'navis'})
        self.assertEqual(self.dns['Home Wi-Fi'], [h.NAVIS_DNS])
        self.assertEqual(h.load()['active']['mode'], 'navis')
        h.enable(self.state, {'code': 'nl', 'user_id': '1234abcd', 'mode': 'dns'})
        self.assertEqual(self.dns['Home Wi-Fi'], ['127.0.0.1'])
        self.assertEqual(h.load()['services']['Home Wi-Fi']['dns'], [])
        h.disable(self.state)
        self.assertEqual(self.dns['Home Wi-Fi'], [])

    def test_foreign_tunnel_dns_is_rejected_before_any_change(self):
        self.dns['Home Wi-Fi'] = ['198.18.0.2']
        with self.assertRaisesRegex(h.Failure, 'другим туннелем'):
            self.activate()
        self.assertEqual(self.events, [])
        self.assertFalse(self.statefile.exists())

    def test_start_notes_are_returned_as_warnings(self):
        with patch.object(h, 'start', return_value=['note']):
            message, notes = h.enable(self.state, {'code': 'de', 'user_id': '1234abcd', 'mode': 'navis'})
        self.assertIn('NAVIS', message)
        self.assertEqual(notes, ['note'])


class WarpProfileTests(unittest.TestCase):
    def test_x25519_rfc7748_vector_and_public_key(self):
        k = bytes.fromhex('a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4')
        u = bytes.fromhex('e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c')
        self.assertEqual(h.x25519(k, u).hex(),
                         'c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552')
        private, public = h.wireguard_keypair()
        self.assertTrue(h._key(private) and h._key(public))

    def test_profile_validation(self):
        self.assertEqual(h.validate_profile(dict(PROFILE)), PROFILE)
        self.assertIsNotNone(h.validate_profile({k: v for k, v in PROFILE.items() if k != 'awg'}))
        bad = [dict(PROFILE, private_key='short'), dict(PROFILE, address_v4='localhost'),
               dict(PROFILE, endpoint_port=70000), dict(PROFILE, endpoint_host='a b'),
               dict(PROFILE, extra=1), dict(PROFILE, awg={'jc': 4, 'cmd': 'id'}),
               dict(PROFILE, awg={'i1': 'deadbeef'}), dict(PROFILE, awg={'jc': True}), 'x']
        for profile in bad:
            with self.subTest(profile=profile), self.assertRaises(h.Failure):
                h.validate_profile(profile)

    def test_default_obfuscation_looks_like_quic(self):
        awg = h.default_obfuscation()
        h.validate_profile(dict(PROFILE, awg=awg))
        i1 = bytes.fromhex(awg['i1'][5:-1])
        self.assertTrue(0xC0 <= i1[0] <= 0xCF)
        self.assertEqual(i1[1:5], b'\x00\x00\x00\x01')
        self.assertTrue(1200 <= len(i1) <= 1300)
        self.assertTrue(0x40 <= bytes.fromhex(awg['i2'][5:-1])[0] <= 0x7F)

    def test_navis_config_targets_country_through_warp(self):
        cfg = h.navis_config({'code': 'nl', 'user_id': 'b8bcea26'}, PROFILE, ['1.2.3.4'])
        doh = cfg['dns']['servers'][0]
        self.assertEqual((doh['server'], doh['path'], doh['detour']),
                         ('nl.aeternia.space', '/dns-query/b8bcea26', 'warp'))
        self.assertEqual(cfg['dns']['servers'][1]['predefined']['nl.aeternia.space'], ['1.2.3.4'])
        endpoint = cfg['endpoints'][0]
        self.assertEqual(endpoint['i1'], PROFILE['awg']['i1'])
        self.assertEqual(endpoint['peers'][0]['public_key'], KEY)
        self.assertEqual(cfg['inbounds'][0]['address'], [h.NAVIS_TUN])
        self.assertEqual(cfg['route']['final'], 'warp')
        plain = h.navis_config({'code': 'nl', 'user_id': 'b8bcea26'}, PROFILE, [], obfuscated=False)
        self.assertNotIn('i1', plain['endpoints'][0])
        self.assertEqual(plain['dns']['servers'][0]['domain_resolver'], 'bootstrap-quad9')

    def test_atlas_profile_import_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'warp-profile.json'
            path.write_text(json.dumps({
                'privateKey': KEY, 'addressIpv4': '172.16.0.2', 'addressIpv6': '2606:4700:110::1',
                'peerPublicKey': KEY, 'endpointHost': 'engage.cloudflareclient.com', 'endpointPort': 2408,
                'obfuscation': {'s1': 0, 's2': 0, 's3': 0, 's4': 0, 'h1': '1', 'h2': '2', 'h3': '3',
                                'h4': '4', 'jc': 4, 'jmin': 40, 'jmax': 70,
                                'i1': '<b 0xc0000000010800>', 'i2': '<b 0x4001>'},
                'source': 'wgcf-server'}))
            profile = d.read_atlas_warp_profile(path)
            self.assertEqual(h.validate_profile(profile)['awg']['h1'], 1)
            self.assertEqual(profile['endpoint_port'], 2408)
            path.write_text('{"broken": true}')
            self.assertIsNone(d.read_atlas_warp_profile(path))
            self.assertIsNone(d.read_atlas_warp_profile(Path(temp) / 'missing.json'))
