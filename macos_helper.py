#!/usr/bin/python3
"""Private macOS helper. Installed root-owned; JSON stdin, no shell or path inputs.

Run only through the installed wrapper (/usr/bin/python3 -I -S).
The UI never supplies TOML, executable paths, service names or shell commands.
"""
from __future__ import annotations

import base64
import copy
import ipaddress
import json
import os
from pathlib import Path
import plistlib
import re
import socket
import subprocess
import sys
import time

ROOT = Path('/Library/PrivilegedHelperTools/space.aeternia.dns')
DATA = Path('/private/var/db/space.aeternia.dns')
STATE = DATA / 'state.json'
CONFIG = DATA / 'dnscrypt-proxy.toml'
BINARY = ROOT / 'dnscrypt-proxy'
LABEL = 'space.aeternia.dns'
PLIST = Path('/Library/LaunchDaemons/space.aeternia.dns.plist')
COUNTRIES = {'de', 'nl', 'fi', 'fr', 'in', 'kz', 'us', 'tr'}
ENV = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/var/root'}
# NAVIS: all traffic through Cloudflare WARP (WireGuard/AmneziaWG) with Aeternia DoH inside the tunnel.
MODES = {'dns', 'navis'}
NAVIS_LABEL = 'space.aeternia.navis'
NAVIS_PLIST = Path('/Library/LaunchDaemons/space.aeternia.navis.plist')
NAVIS_CONFIG = DATA / 'navis.json'
ENGINE = ROOT / 'sing-box'
WARP_PROFILE = DATA / 'warp-profile.json'
NAVIS_TUN = '198.18.2.1/30'
NAVIS_DNS = '198.18.2.2'          # tun peer address; DNS is hijacked by the engine
TUNNEL_RANGE = ipaddress.ip_network('198.18.0.0/15')
WARP_API = 'https://api.cloudflareclient.com/v0a2158/reg'
WARP_ENDPOINT_IPS = ['162.159.192.1', '162.159.193.1', '162.159.195.1']


class Failure(Exception):
    pass


def run(args, timeout=15, check=True):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                       env=ENV, cwd='/')
    if check and r.returncode:
        raise Failure(f'{Path(args[0]).name}: {(r.stderr or r.stdout).strip()[:400]}')
    return r


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('wb') as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    tmp.chmod(0o600)
    os.replace(tmp, path)


def load():
    if not STATE.exists():
        return {'version': 1, 'services': {}, 'active': None, 'previous': None}
    state = json.loads(STATE.read_text())
    if state.get('version') != 1 or not isinstance(state.get('services'), dict):
        raise Failure('Повреждена сохранённая конфигурация DNS; остановка отменена.')
    return state


def save(state):
    atomic(STATE, json.dumps(state, ensure_ascii=False).encode())


def services():
    """Use network SERVICE names, never hardware-port names; include disabled ones."""
    out = run(['/usr/sbin/networksetup', '-listnetworkserviceorder']).stdout
    result = {}
    name = None
    for line in out.splitlines():
        m = re.match(r'^\((?:\d+|\*)\) (.+)$', line)
        if m:
            name = m[1]
        elif name:
            m = re.search(r'Device: ([^)]+)\)', line)
            if m:
                result[name] = m[1].strip()
                name = None
    if not result:
        raise Failure('Не удалось получить список сетевых подключений.')
    return result


def active_service(available):
    for args in (['/sbin/route', '-n', 'get', 'default'],
                 ['/sbin/route', '-n', 'get', '-inet6', 'default']):
        r = run(args, check=False)
        m = re.search(r'interface:\s*(\S+)', r.stdout)
        if m:
            names = [n for n, dev in available.items() if dev == m[1]]
            if len(names) == 1:
                return names[0]
    raise Failure('Не удалось однозначно определить подключение. Проверьте сеть/VPN.')


def read_dns(name):
    out = run(['/usr/sbin/networksetup', '-getdnsservers', name]).stdout.strip()
    if out.startswith("There aren't any DNS Servers set on "):
        return []  # An empty explicit list means DHCP, not "no DNS".
    values = out.splitlines()
    if not values:
        raise Failure(f'Пустой ответ DNS для {name}')
    try:
        for value in values:
            ipaddress.ip_address(value)
    except ValueError:
        raise Failure(f'Не удалось прочитать DNS для {name}: {out[:160]}')
    return values


def local_dns(values):
    return any(ipaddress.ip_address(v).is_loopback or v == NAVIS_DNS for v in values)


def foreign_tunnel_dns(values):
    """Another tun-based client (e.g. ATLAS) that hijacks DNS; switching under it silently does nothing."""
    return [v for v in values if v != NAVIS_DNS and ipaddress.ip_address(v) in TUNNEL_RANGE]


def listen_address(target):
    return NAVIS_DNS if target.get('mode', 'dns') == 'navis' else '127.0.0.1'


def set_dns(name, values):
    run(['/usr/sbin/networksetup', '-setdnsservers', name] + (values or ['empty']))
    if read_dns(name) != values:
        raise Failure(f'DNS для {name} не соответствует записанным настройкам.')


def flush():
    run(['/usr/bin/dscacheutil', '-flushcache'])
    run(['/usr/bin/killall', '-HUP', 'mDNSResponder'])


def system_dns_check():
    # dscacheutil uses the macOS resolver, including scoped DNS; dig without @ does not.
    r = run(['/usr/bin/dscacheutil', '-q', 'host', '-a', 'name', 'example.com'],
            timeout=12, check=False)
    return r.returncode == 0 and ('ip_address:' in r.stdout or 'ipv6_address:' in r.stdout)


def loaded(label=LABEL):
    r = run(['/bin/launchctl', 'print', f'system/{label}'], check=False)
    if r.returncode and not any(s in (r.stdout + r.stderr).lower()
                               for s in ('could not find service', 'not found', 'no such process')):
        raise Failure(f'Не удалось проверить службу {label}: {r.stderr[:200]}')
    return r.returncode == 0


def running():
    for label in (LABEL, NAVIS_LABEL):
        if loaded(label):
            r = run(['/bin/launchctl', 'print', f'system/{label}'])
            if re.search(r'\bpid = \d+', r.stdout):
                return True
    return False


def stop(label=None, plist=None):
    """Without arguments stops both Aeternia services (DNS and NAVIS)."""
    if label is None:
        for one_label, one_plist in ((LABEL, PLIST), (NAVIS_LABEL, NAVIS_PLIST)):
            stop(one_label, one_plist)
        return
    if loaded(label):
        run(['/bin/launchctl', 'bootout', f'system/{label}'])
    if loaded(label):
        raise Failure(f'Служба {label} всё ещё загружена.')
    plist.unlink(missing_ok=True)


def config_for(target):
    code, uid = target['code'], target['user_id']
    host = f'{code}.aeternia.space:8443'.encode()
    path = f'/dns-query/{uid}'.encode()
    stamp = 'sdns://' + base64.urlsafe_b64encode(
        b'\x02' + b'\0' * 10 + bytes([len(host)]) + host + bytes([len(path)]) + path
    ).rstrip(b'=').decode()
    return ("server_names = ['aeternia-doh']\n"
            "listen_addresses = ['127.0.0.1:53']\n"
            "max_clients = 250\ncache = true\nignore_system_dns = true\n"
            "bootstrap_resolvers = ['1.1.1.1:53', '9.9.9.9:53']\n"
            "netprobe_timeout = 10\n"
            f"[static.'aeternia-doh']\nstamp = '{stamp}'\n").encode()


def start(target):
    if target.get('mode', 'dns') == 'navis':
        return start_navis(target)
    start_dns(target)
    return []


def start_dns(target):
    atomic(CONFIG, config_for(target))
    run([str(BINARY), '-check', '-config', str(CONFIG)], timeout=30)
    stop()
    # Do not mistake another resolver's reply for a successful Aeternia startup.
    for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
        with socket.socket(socket.AF_INET, kind) as sock:
            try:
                sock.bind(('127.0.0.1', 53))
            except OSError:
                raise Failure('Порт 127.0.0.1:53 занят другим DNS-сервисом.')
    atomic(PLIST, plistlib.dumps({
        'Label': LABEL, 'ProgramArguments': [str(BINARY), '-config', str(CONFIG)],
        'RunAtLoad': True, 'KeepAlive': True, 'WorkingDirectory': str(DATA),
        'StandardOutPath': str(DATA / 'proxy.log'),
        'StandardErrorPath': str(DATA / 'proxy-error.log'),
        'EnvironmentVariables': ENV,
    }))
    PLIST.chmod(0o644)
    run(['/bin/launchctl', 'bootstrap', 'system', str(PLIST)])
    for _ in range(5):
        r = run(['/usr/bin/dig', '@127.0.0.1', 'example.com', '+time=2', '+tries=1'],
                timeout=4, check=False)
        status = run(['/bin/launchctl', 'print', f'system/{LABEL}'], check=False)
        if (r.returncode == 0 and 'status: NOERROR' in r.stdout
                and 'ANSWER SECTION' in r.stdout and re.search(r'\bpid = \d+', status.stdout)):
            return
        time.sleep(0.5)
    raise Failure('Aeternia не отвечает на DNS-запросы; перенаправление отменено.')

# ─── NAVIS (WARP) ─────────────────────────────────────────────────────────────

_P = 2 ** 255 - 19


def _clamp(key):
    key = bytearray(key)
    key[0] &= 248
    key[31] &= 127
    key[31] |= 64
    return bytes(key)


def x25519(scalar, point):
    """RFC 7748 X25519 in pure Python: the helper runs with -I -S and has no crypto libraries."""
    k = int.from_bytes(_clamp(scalar), 'little')
    u = int.from_bytes(point, 'little') & ((1 << 255) - 1)
    x1, x2, z2, x3, z3, swap = u, 1, 0, u, 1, 0
    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = kt
        a = (x2 + z2) % _P
        aa = a * a % _P
        b = (x2 - z2) % _P
        bb = b * b % _P
        e = (aa - bb) % _P
        c = (x3 + z3) % _P
        d = (x3 - z3) % _P
        da = d * a % _P
        cb = c * b % _P
        x3 = (da + cb) % _P
        x3 = x3 * x3 % _P
        z3 = (da - cb) % _P
        z3 = x1 * (z3 * z3 % _P) % _P
        x2 = aa * bb % _P
        z2 = e * ((aa + 121665 * e) % _P) % _P
    if swap:
        x2, x3, z2, z3 = x3, x2, z3, z2
    return (x2 * pow(z2, _P - 2, _P) % _P).to_bytes(32, 'little')


def wireguard_keypair():
    private = _clamp(os.urandom(32))
    public = x25519(private, (9).to_bytes(32, 'little'))
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def _varint(value):
    if value < 64:
        return bytes([value])
    if value < 16384:
        return (0x4000 | value).to_bytes(2, 'big')
    return (0x80000000 | value).to_bytes(4, 'big')


def fake_quic_initial(size=None):
    """AmneziaWG 1.5 'I1' packet shaped like a QUIC Initial: DPI sees a browser-like first datagram."""
    size = size or 1200 + int.from_bytes(os.urandom(1), 'big') % 100
    dcid, scid = os.urandom(8), os.urandom(8)
    header = bytes([0xC0 | os.urandom(1)[0] & 0x0F]) + b'\x00\x00\x00\x01'
    header += bytes([len(dcid)]) + dcid + bytes([len(scid)]) + scid + _varint(0)
    payload_len = size - len(header) - 2
    packet = header + _varint(payload_len) + os.urandom(payload_len)
    return '<b 0x' + packet.hex() + '>'


def fake_quic_short(size=None):
    size = size or 100 + int.from_bytes(os.urandom(1), 'big') % 60
    packet = bytes([0x40 | os.urandom(1)[0] & 0x3F]) + os.urandom(8) + os.urandom(size - 9)
    return '<b 0x' + packet.hex() + '>'


def default_obfuscation():
    return {'jc': 4, 'jmin': 40, 'jmax': 70, 's1': 0, 's2': 0, 's3': 0, 's4': 0,
            'h1': 1, 'h2': 2, 'h3': 3, 'h4': 4, 'i1': fake_quic_initial(), 'i2': fake_quic_short()}


def _key(value):
    try:
        return isinstance(value, str) and len(base64.b64decode(value, validate=True)) == 32
    except (ValueError, TypeError):
        return False


def _int_in(value, low, high):
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def validate_profile(profile):
    """Closed schema: the UI may hand over an imported profile, but never arbitrary engine options."""
    if not isinstance(profile, dict):
        raise Failure('Профиль WARP должен быть JSON-объектом.')
    allowed = {'private_key', 'address_v4', 'address_v6', 'peer_public_key',
               'endpoint_host', 'endpoint_port', 'awg', 'source', 'updated'}
    if set(profile) - allowed or not {'private_key', 'address_v4', 'peer_public_key',
                                       'endpoint_host', 'endpoint_port'} <= set(profile):
        raise Failure('Профиль WARP: неверный набор полей.')
    if not _key(profile['private_key']) or not _key(profile['peer_public_key']):
        raise Failure('Профиль WARP: неверный формат ключа.')
    try:
        ipaddress.IPv4Address(profile['address_v4'])
        if profile.get('address_v6'):
            ipaddress.IPv6Address(profile['address_v6'])
    except ValueError:
        raise Failure('Профиль WARP: неверный адрес интерфейса.')
    if (not isinstance(profile['endpoint_host'], str)
            or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', profile['endpoint_host'])
            or not _int_in(profile['endpoint_port'], 1, 65535)):
        raise Failure('Профиль WARP: неверная точка подключения.')
    awg = profile.get('awg')
    if awg is not None:
        if not isinstance(awg, dict) or set(awg) - {'jc', 'jmin', 'jmax', 's1', 's2', 's3', 's4',
                                                   'h1', 'h2', 'h3', 'h4', 'i1', 'i2'}:
            raise Failure('Профиль WARP: неверные параметры обфускации.')
        for name, high in (('jc', 128), ('jmin', 1280), ('jmax', 1280), ('s1', 1280), ('s2', 1280),
                           ('s3', 1280), ('s4', 1280), ('h1', 2 ** 32 - 1), ('h2', 2 ** 32 - 1),
                           ('h3', 2 ** 32 - 1), ('h4', 2 ** 32 - 1)):
            if name in awg and not _int_in(awg[name], 0, high):
                raise Failure(f'Профиль WARP: неверное значение {name}.')
        for name in ('i1', 'i2'):
            if name in awg and not (isinstance(awg[name], str)
                                    and re.fullmatch(r'<b 0x[0-9a-fA-F]{2,6000}>', awg[name])):
                raise Failure(f'Профиль WARP: неверный пакет {name}.')
    if 'source' in profile and profile['source'] not in ('register', 'atlas'):
        raise Failure('Профиль WARP: неизвестный источник.')
    return profile


def save_profile(profile):
    validate_profile(profile)
    profile = dict(profile, updated=int(time.time()))
    atomic(WARP_PROFILE, json.dumps(profile, ensure_ascii=False, indent=1).encode())
    WARP_PROFILE.chmod(0o600)


def load_profile():
    if not WARP_PROFILE.exists():
        return None
    return validate_profile(json.loads(WARP_PROFILE.read_text()))


def register_warp():
    """Register a fresh WARP device directly with Cloudflare; no third-party account server involved."""
    import ssl
    import urllib.request
    private, public = wireguard_keypair()
    body = {'key': public, 'install_id': '', 'fcm_token': '', 'model': 'PC', 'serial_number': '',
            'locale': 'en_US', 'tos': time.strftime('%Y-%m-%dT%H:%M:%S.000Z', time.gmtime())}
    request = urllib.request.Request(WARP_API, data=json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'User-Agent': 'okhttp/3.12.1',
        'CF-Client-Version': 'a-6.30-2158'})
    context = ssl.create_default_context()
    context.load_default_certs()
    try:
        with urllib.request.urlopen(request, timeout=25, context=context) as response:
            answer = json.loads(response.read())
        interface = answer['config']['interface']['addresses']
        peer = answer['config']['peers'][0]
        host, _, port = peer['endpoint']['host'].rpartition(':')
        profile = {'private_key': private, 'address_v4': interface['v4'], 'address_v6': interface.get('v6'),
                   'peer_public_key': peer['public_key'], 'endpoint_host': host,
                   'endpoint_port': int(port), 'awg': default_obfuscation(), 'source': 'register'}
    except Failure:
        raise
    except Exception as error:
        raise Failure(f'Не удалось зарегистрировать WARP у Cloudflare: {error}. '
                      'Если API недоступен, импортируйте профиль из ATLAS (клавиша W).')
    save_profile(profile)
    return 'Профиль WARP зарегистрирован у Cloudflare.'


def resolve_hosts(host):
    """Pre-resolve through public resolvers before the tunnel exists; empty means 'let the engine bootstrap'."""
    found = []
    for server in ('9.9.9.9', '1.1.1.1', '8.8.8.8'):
        for rtype in ('A', 'AAAA'):
            r = run(['/usr/bin/dig', f'@{server}', host, rtype, '+short', '+time=2', '+tries=1'],
                    timeout=6, check=False)
            for line in r.stdout.split():
                try:
                    ipaddress.ip_address(line)
                    found.append(line)
                except ValueError:
                    pass
        if found:
            break
    return sorted(set(found))


def navis_config(target, profile, hosts, obfuscated=True):
    code, uid = target['code'], target['user_id']
    host = f'{code}.aeternia.space'
    predefined = {'engage.cloudflareclient.com': WARP_ENDPOINT_IPS}
    if hosts:
        predefined[host] = hosts
    if profile['endpoint_host'] != 'engage.cloudflareclient.com':
        predefined.pop('engage.cloudflareclient.com')
    address = [f"{profile['address_v4']}/32"]
    if profile.get('address_v6'):
        address.append(f"{profile['address_v6']}/128")
    endpoint = {
        'type': 'wireguard', 'tag': 'warp', 'address': address, 'private_key': profile['private_key'],
        'mtu': 1280, 'domain_resolver': 'bootstrap-hosts',
        'peers': [{'address': profile['endpoint_host'], 'port': profile['endpoint_port'],
                   'public_key': profile['peer_public_key'], 'allowed_ips': ['0.0.0.0/0', '::/0'],
                   'persistent_keepalive_interval': 25}],
    }
    if obfuscated and profile.get('awg'):
        endpoint.update(profile['awg'])
    return {
        'log': {'level': 'info', 'timestamp': True, 'output': str(DATA / 'navis.log')},
        'dns': {
            'servers': [
                {'tag': 'aeternia-doh', 'type': 'https', 'server': host, 'server_port': 8443,
                 'path': f'/dns-query/{uid}', 'detour': 'warp',
                 'domain_resolver': 'bootstrap-hosts' if hosts else 'bootstrap-quad9'},
                {'tag': 'bootstrap-hosts', 'type': 'hosts', 'predefined': predefined},
                {'tag': 'bootstrap-quad9', 'type': 'udp', 'server': '9.9.9.9', 'server_port': 53},
                {'tag': 'bootstrap-cloudflare', 'type': 'udp', 'server': '1.1.1.1', 'server_port': 53},
                {'tag': 'bootstrap-local', 'type': 'local'},
            ],
            'final': 'aeternia-doh', 'strategy': 'prefer_ipv4', 'timeout': '8s',
        },
        'endpoints': [endpoint],
        'inbounds': [{'type': 'tun', 'tag': 'tun-in', 'address': [NAVIS_TUN], 'mtu': 1280,
                      'auto_route': True, 'strict_route': True, 'dns_mode': 'hijack', 'stack': 'gvisor',
                      'route_exclude_address': ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
                                                '169.254.0.0/16', 'fc00::/7', 'fe80::/10']}],
        'outbounds': [{'type': 'direct', 'tag': 'direct'}],
        'route': {
            'auto_detect_interface': True, 'default_domain_resolver': 'bootstrap-local', 'final': 'warp',
            'rules': [
                {'action': 'reject', 'network': ['tcp', 'udp'], 'port': 853},
                {'action': 'hijack-dns', 'network': ['tcp', 'udp'], 'port': 53},
                {'action': 'hijack-dns', 'protocol': 'dns'},
                {'action': 'sniff', 'sniffer': ['tls', 'http', 'quic', 'dns'], 'timeout': '2s'},
                {'action': 'route', 'domain_suffix': ['.ru'], 'outbound': 'direct'},
            ],
        },
        'experimental': {'cache_file': {'enabled': True, 'path': str(DATA / 'navis-cache.db')}},
    }


def _engine_log_tail():
    try:
        text = (DATA / 'navis-error.log').read_text(errors='replace').strip().splitlines()
        return (' Журнал: ' + text[-1][-200:]) if text else ''
    except OSError:
        return ''


def start_navis(target):
    if not ENGINE.is_file():
        raise Failure('Движок NAVIS (sing-box) не установлен. Повторите установку: '
                      'sudo bash install.sh --engine /путь/к/sing-box')
    profile = load_profile()
    if not profile:
        raise Failure('Профиль WARP не настроен. Нажмите W в приложении.')
    notes = []
    hosts = resolve_hosts(f"{target['code']}.aeternia.space")
    if not hosts:
        notes.append('Адрес сервера Aeternia не удалось определить заранее; движок разрешит его сам.')
    atomic(NAVIS_CONFIG, json.dumps(navis_config(target, profile, hosts), indent=1).encode())
    check = run([str(ENGINE), 'check', '-c', str(NAVIS_CONFIG)], timeout=30, check=False)
    if check.returncode and profile.get('awg'):
        # Upstream sing-box without AmneziaWG: fall back to plain WireGuard to the same WARP peer.
        atomic(NAVIS_CONFIG, json.dumps(navis_config(target, profile, hosts, obfuscated=False), indent=1).encode())
        check = run([str(ENGINE), 'check', '-c', str(NAVIS_CONFIG)], timeout=30, check=False)
        notes.append('Движок без поддержки AmneziaWG: используется обычный WireGuard без обфускации.')
    if check.returncode:
        raise Failure(f'Конфигурация NAVIS отклонена движком: {(check.stderr or check.stdout)[-300:]}')
    stop()
    atomic(NAVIS_PLIST, plistlib.dumps({
        'Label': NAVIS_LABEL, 'ProgramArguments': [str(ENGINE), 'run', '-c', str(NAVIS_CONFIG)],
        'RunAtLoad': True, 'KeepAlive': True, 'WorkingDirectory': str(DATA),
        'StandardOutPath': str(DATA / 'navis-stdout.log'),
        'StandardErrorPath': str(DATA / 'navis-error.log'),
        'EnvironmentVariables': ENV,
    }))
    NAVIS_PLIST.chmod(0o644)
    run(['/bin/launchctl', 'bootstrap', 'system', str(NAVIS_PLIST)])
    # WARP handshake plus DoH inside the tunnel: allow more time than the local DNS mode.
    for _ in range(30):
        r = run(['/usr/bin/dig', f'@{NAVIS_DNS}', 'example.com', '+time=2', '+tries=1'],
                timeout=4, check=False)
        status = run(['/bin/launchctl', 'print', f'system/{NAVIS_LABEL}'], check=False)
        if (r.returncode == 0 and 'status: NOERROR' in r.stdout
                and 'ANSWER SECTION' in r.stdout and re.search(r'\bpid = \d+', status.stdout)):
            return notes
        time.sleep(1)
    raise Failure('NAVIS: туннель WARP не отвечает на DNS-запросы; перенаправление отменено.'
                  + _engine_log_tail())


def locate(name, saved, available):
    if name in available:
        if available[name] != saved['device']:
            raise Failure(f'Подключение {name} теперь относится к другому интерфейсу.')
        return name
    matches = [n for n, d in available.items() if d == saved['device']]
    if len(matches) == 1:
        return matches[0]  # Renamed since activation.
    if matches:
        raise Failure(f'Неоднозначное переименование подключения {name}.')
    return None  # Removed service; nothing remains to reset.


def restore(state):
    available = services()
    notes = []
    # Keep the entire journal until every restore and the cache flush succeeds.
    for name, saved in state['services'].items():
        actual = locate(name, saved, available)
        if actual is None:
            notes.append(f'{name}: подключение удалено')
            continue
        current = read_dns(actual)
        if local_dns(current):
            set_dns(actual, saved['dns'])
        elif current != saved['dns']:
            notes.append(f'{actual}: сохранены более новые настройки DNS')
        if saved.get('legacy'):
            notes.append(f'{actual}: старая версия не сохранила DNS, включён DHCP')
    # Never kill the resolver while ANY service still points at it.
    remaining = [name for name in available if local_dns(read_dns(name))]
    if remaining:
        raise Failure('Остался локальный DNS у: ' + ', '.join(remaining)
                      + '. Служба сохранена; проверьте настройки этих подключений.')
    flush()
    return notes


def disable(state):
    notes = restore(state)  # This must finish BEFORE stop(), even when config is absent.
    stop()
    old = state['active']
    state.update(services={}, active=None)
    if old:
        state['previous'] = old
    save(state)
    try:
        if not system_dns_check():
            notes.append('Системный DNS пока не отвечает; проверьте DNS роутера/провайдера')
    except subprocess.TimeoutExpired:
        notes.append('Проверка системного DNS превысила время ожидания')
    return 'Без прокси: настройки DNS восстановлены, перезагрузка не нужна.', notes


def enable(state, target):
    available = services()
    name = active_service(available)
    current = read_dns(name)
    # Match a previously renamed service before taking a new snapshot.
    tracked = any(locate(n, s, available) == name for n, s in state['services'].items())
    if not tracked:
        if local_dns(current):
            raise Failure('Подключение уже использует локальный DNS. Выполните миграцию установщиком.')
        foreign = foreign_tunnel_dns(current)
        if foreign:
            raise Failure(f'{name}: DNS перехвачен другим туннелем ({", ".join(foreign)}), например ATLAS. '
                          'Отключите его и повторите; иначе переключение стран не действует.')
        state['services'][name] = {'device': available[name], 'dns': current}
    # Persist original DNS BEFORE touching config, the service, or network settings.
    save(state)
    old = copy.deepcopy(state['active'])
    notes = []
    try:
        notes = start(target) or []
        set_dns(name, [listen_address(target)])
        flush()
    except Exception as error:
        recovery = ''
        try:
            if old:
                start(old)
                flush()
                recovery = 'Предыдущий сервер восстановлен.'
            else:
                disable(state)
                recovery = 'Исходные DNS восстановлены.'
        except Exception as rollback_error:
            # Keep the journal; "Без прокси" can retry even after a crash/reboot.
            recovery = f'Восстановление не завершено: {rollback_error}. Повторите «Без прокси».'
        raise Failure(f'{error} {recovery}')
    state.update(active=target, previous=old)
    save(state)
    mode = 'NAVIS (WARP)' if target.get('mode', 'dns') == 'navis' else 'DNS'
    return f"{mode}: переключено на {target['code']}; перезагрузка не нужна.", notes


def migrate(state):
    """Upgrade only the legacy Aeternia configuration; never adopt arbitrary DNS."""
    if state.get('migration_done'):
        return 'Миграция старой версии уже выполнена.', []
    if state['active'] or state['services']:
        return 'Сохранённое состояние новой версии оставлено без изменений.', []
    legacy = Path('/usr/local/bin/aeternia_dns_switcher.py').exists() or any(p.exists() and "aeternia-doh" in p.read_text()
                 for p in (Path('/opt/homebrew/etc/dnscrypt-proxy.toml'),
                           Path('/usr/local/etc/dnscrypt-proxy.toml')))
    if not legacy:
        state['migration_done'] = True
        save(state)
        return 'Старая конфигурация Aeternia не обнаружена.', []
    for name, device in services().items():
        dns = read_dns(name)
        if local_dns(dns):
            # Preserve any real resolvers in a mixed legacy list.
            state['services'][name] = {'device': device,
                                      'dns': [v for v in dns if not ipaddress.ip_address(v).is_loopback],
                                      'legacy': True}
    save(state)
    notes = restore(state)
    stop('homebrew.mxcl.dnscrypt-proxy',
         Path('/Library/LaunchDaemons/homebrew.mxcl.dnscrypt-proxy.plist'))
    state['services'] = {}
    state['migration_done'] = True
    save(state)
    return 'Старая конфигурация DNS отключена без перезагрузки.', notes


def dispatch(request):
    if not isinstance(request, dict):
        raise Failure('Ожидался JSON-объект.')
    action = request.get('action')
    if action not in {'status', 'enable', 'disable', 'rollback', 'migrate', 'warp'}:
        raise Failure('Недопустимая операция.')
    allowed = {'action'}
    if action == 'enable':
        allowed = {'action', 'code', 'user_id', 'mode'}
    elif action == 'warp':
        allowed = {'action', 'source', 'profile'}
    if set(request) - allowed or not (allowed - {'mode', 'profile'}) <= set(request):
        raise Failure('Недопустимые параметры операции.')
    if action == 'enable':
        if (not isinstance(request['code'], str) or request['code'] not in COUNTRIES
                or not isinstance(request['user_id'], str)
                or not re.fullmatch(r'[0-9a-fA-F]{8,64}', request['user_id'])
                or request.get('mode', 'dns') not in MODES):
            raise Failure('Неверная страна, режим или Aeternia ID.')
    if action == 'warp':
        if request['source'] == 'register':
            if 'profile' in request:
                raise Failure('Недопустимые параметры операции.')
            return {'ok': True, 'message': register_warp(), 'warnings': [], 'active': load()['active']}
        if request['source'] == 'atlas':
            if not isinstance(request.get('profile'), dict):
                raise Failure('Для импорта нужен объект profile.')
            save_profile(dict(request['profile'], source='atlas'))
            return {'ok': True, 'message': 'Профиль WARP импортирован из ATLAS (с обфускацией AmneziaWG).',
                    'warnings': [], 'active': load()['active']}
        raise Failure('Неизвестный источник профиля WARP.')
    state = load()
    if action == 'status':
        return {'ok': True, 'active': state['active'], 'running': running(),
                'pending_restore': bool(state['services']), 'message': '', 'warnings': [],
                'navis_available': ENGINE.is_file(), 'warp_ready': WARP_PROFILE.is_file()}
    if action == 'enable':
        message, notes = enable(state, {'code': request['code'], 'user_id': request['user_id'],
                                        'mode': request.get('mode', 'dns')})
    elif action == 'disable':
        message, notes = disable(state)
    elif action == 'migrate':
        message, notes = migrate(state)
    elif state['previous']:
        message, notes = enable(state, copy.deepcopy(state['previous']))
    else:
        message, notes = disable(state)
    return {'ok': True, 'message': message, 'warnings': notes, 'active': state['active']}


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1:
            raise Failure('Используйте установленный помощник без аргументов.')
        os.umask(0o077)
        DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
        import fcntl
        with (DATA / 'operation.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure('Другая операция DNS ещё выполняется.')
            # One bounded line; do not wait for an untrusted caller to close stdin.
            # Bound the time an incomplete caller can hold the operation lock.
            import select
            raw = bytearray()
            deadline = time.monotonic() + 5
            while len(raw) <= 32768 and not raw.endswith(b'\n'):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([sys.stdin.buffer], [], [], remaining)[0]:
                    raise Failure('Истекло время передачи запроса.')
                chunk = os.read(sys.stdin.fileno(), 1)
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > 32768:
                raise Failure('Запрос слишком большой.')
            result = dispatch(json.loads(raw))
    except Exception as error:
        result = {'ok': False, 'message': str(error), 'warnings': []}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
