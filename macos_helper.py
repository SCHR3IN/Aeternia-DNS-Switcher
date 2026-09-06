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
    return any(ipaddress.ip_address(v).is_loopback for v in values)


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
    if not loaded():
        return False
    r = run(['/bin/launchctl', 'print', f'system/{LABEL}'])
    return re.search(r'\bpid = \d+', r.stdout) is not None


def stop(label=LABEL, plist=PLIST):
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
        state['services'][name] = {'device': available[name], 'dns': current}
    # Persist original DNS BEFORE touching config, the service, or network settings.
    save(state)
    old = copy.deepcopy(state['active'])
    try:
        start(target)
        set_dns(name, ['127.0.0.1'])
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
    return f"DNS переключён на {target['code']}; перезагрузка не нужна.", []


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
    if action not in {'status', 'enable', 'disable', 'rollback', 'migrate'}:
        raise Failure('Недопустимая операция.')
    allowed = {'action', 'code', 'user_id'} if action == 'enable' else {'action'}
    if set(request) != allowed:
        raise Failure('Недопустимые параметры операции.')
    if action == 'enable':
        if (not isinstance(request['code'], str) or request['code'] not in COUNTRIES
                or not isinstance(request['user_id'], str)
                or not re.fullmatch(r'[0-9a-fA-F]{8}', request['user_id'])):
            raise Failure('Неверная страна или Aeternia ID.')
    state = load()
    if action == 'status':
        return {'ok': True, 'active': state['active'], 'running': running(),
                'pending_restore': bool(state['services']), 'message': '', 'warnings': []}
    if action == 'enable':
        message, notes = enable(state, {'code': request['code'], 'user_id': request['user_id']})
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
            while len(raw) <= 2048 and not raw.endswith(b'\n'):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not select.select([sys.stdin.buffer], [], [], remaining)[0]:
                    raise Failure('Истекло время передачи запроса.')
                chunk = os.read(sys.stdin.fileno(), 1)
                if not chunk:
                    break
                raw.extend(chunk)
            if len(raw) > 2048:
                raise Failure('Запрос слишком большой.')
            result = dispatch(json.loads(raw))
    except Exception as error:
        result = {'ok': False, 'message': str(error), 'warnings': []}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
