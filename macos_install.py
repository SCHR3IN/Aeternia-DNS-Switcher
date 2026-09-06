#!/usr/bin/python3
"""One-time administrator installation; no password storage and no general sudo grant."""
from __future__ import annotations

import json
import argparse
import os
from pathlib import Path
import plistlib
import pwd
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path('/Library/PrivilegedHelperTools/space.aeternia.dns')
HELPER = Path('/Library/PrivilegedHelperTools/space.aeternia.dns-helper')
DATA = Path('/private/var/db/space.aeternia.dns')
RULE = Path('/private/etc/sudoers.d/aeternia-dns-helper')
APP = Path('/Applications/Aeternia DNS.app')
CLI = Path('/usr/local/bin/aeternia-dns-switcher')
ENV = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'C', 'LC_ALL': 'C', 'HOME': '/var/root'}


def run(args, **kwargs):
    return subprocess.run(args, check=True, env=ENV, **kwargs)


def secure_dir(path, mode=0o755):
    if path != path.parent:
        secure_dir(path.parent)
    if path.is_symlink():
        raise RuntimeError(f'Установка отменена: символическая ссылка {path}')
    if not path.exists():
        path.mkdir(mode=mode)
        os.chown(path, 0, 0)
    st = path.stat()
    if st.st_uid != 0 or st.st_mode & 0o022:
        raise RuntimeError(f'Установка отменена: небезопасные права {path}')


def put(path, contents, mode=0o644):
    secure_dir(path.parent)
    fd, tmp = tempfile.mkstemp(prefix='.aeternia-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(contents)
        os.chmod(tmp, mode)
        os.chown(tmp, 0, 0)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def helper(action):
    # main() holds the same lock as runtime operations throughout maintenance.
    # Import only the root-owned installed file; never import from a user directory.
    import importlib.util
    secure_dir(ROOT)
    spec = importlib.util.spec_from_file_location('aeternia_maintenance', ROOT / 'macos_helper.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.dispatch({'action': action})
    if not result['ok']:
        raise RuntimeError(result['message'])
    print(result['message'])
    for note in result.get('warnings', []):
        print('Внимание:', note)


def installation_account(username=None):
    """Legacy launchers can invoke sudo twice, replacing SUDO_UID with zero."""
    def usable(account):
        return account.pw_uid >= 500 and account.pw_name not in ('root', 'nobody', 'loginwindow')

    if username is not None:
        try:
            account = pwd.getpwnam(username)
        except KeyError:
            raise RuntimeError(f'Учётная запись {username!r} не найдена.')
        if not usable(account):
            raise RuntimeError('Укажите обычного пользователя Mac, а не root/системную учётную запись.')
        return account
    try:
        account = pwd.getpwuid(int(os.environ.get('SUDO_UID', '-1')))
        if usable(account):
            return account
    except (ValueError, KeyError, OverflowError):
        pass
    try:
        account = pwd.getpwnam(os.environ.get('SUDO_USER', ''))
        if usable(account):
            return account
    except KeyError:
        pass
    # The kernel-owned console records the logged-in Mac user even after nested sudo.
    try:
        account = pwd.getpwuid(os.stat('/dev/console').st_uid)
        if usable(account):
            return account
    except (OSError, KeyError):
        pass
    raise RuntimeError('Не удалось определить пользователя Mac. Запустите установку одной командой '
                       'из обычного Терминала или укажите: sudo bash install.sh --user ИМЯ_ПОЛЬЗОВАТЕЛЯ')


def dnscrypt_binary():
    # Homebrew installs this daemon in sbin (including keg-only/unlinked installs).
    for prefix in (Path('/opt/homebrew'), Path('/usr/local')):
        for relative in ('opt/dnscrypt-proxy/sbin/dnscrypt-proxy',
                         'sbin/dnscrypt-proxy', 'bin/dnscrypt-proxy'):
            candidate = prefix / relative
            if candidate.is_file():
                return candidate
    raise RuntimeError('DNS-движок не найден. Выполните без sudo: brew install dnscrypt-proxy')


def install(account):
    src = Path(__file__).resolve().parent
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', account.pw_name):
        raise RuntimeError('Имя учётной записи не поддерживается правилом sudoers.')
    binary = dnscrypt_binary()
    # Ensure the actual interpreter and stdlib work before making any changes.
    run(['/usr/bin/python3', '-I', '-S', '-c',
         'import sys,curses; assert sys.version_info >= (3,9), "Нужен Python 3.9+"'])
    # The privileged service must not load writable Homebrew libraries at runtime.
    dependencies = run(['/usr/bin/otool', '-L', str(binary)], capture_output=True, text=True).stdout
    for line in dependencies.splitlines()[1:]:
        lib = line.strip().split(' (', 1)[0]
        if lib and not lib.startswith(('/usr/lib/', '/System/Library/')):
            raise RuntimeError(f'dnscrypt-proxy использует внешнюю библиотеку: {lib}')
    for name in ('macos_helper.py', 'macos_install.py', 'aeternia_dns_switcher.py', 'dns_utils.py'):
        if not (src / name).is_file():
            raise RuntimeError(f'Отсутствует файл установщика: {name}')
    secure_dir(ROOT)
    secure_dir(DATA, 0o700)
    # An upgrade must not replace an executable while its service is running.
    if (ROOT / 'macos_helper.py').exists():
        helper('disable')
    for name in ('macos_helper.py', 'macos_install.py', 'aeternia_dns_switcher.py', 'dns_utils.py'):
        put(ROOT / name, (src / name).read_bytes())
    put(ROOT / 'dnscrypt-proxy', binary.read_bytes(), 0o755)
    put(HELPER, (f'#!/bin/sh\nexec /usr/bin/python3 -I -S "{ROOT}/macos_helper.py" "$@"\n').encode(), 0o755)
    helper('migrate')
    for name in ('aeternia_dns_switcher.py', 'dns_utils.py'):
        (Path('/usr/local/bin') / name).unlink(missing_ok=True)

    # Exactly one root-owned command, no arguments; the helper validates JSON stdin.
    rule = f'{account.pw_name} ALL=(root) NOPASSWD: {HELPER} ""\n'
    secure_dir(RULE.parent)
    fd, candidate = tempfile.mkstemp(prefix='.aeternia-', dir=RULE.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(rule)
        os.chmod(candidate, 0o440)
        run(['/usr/sbin/visudo', '-cf', candidate])
        os.replace(candidate, RULE)
    finally:
        if os.path.exists(candidate):
            os.unlink(candidate)
    # Check effective policy without starting another operation while maintenance is locked.
    run(['/usr/bin/sudo', '-u', account.pw_name,
         '/usr/bin/sudo', '-n', '-l', str(HELPER)], capture_output=True, text=True)
    # Migrate user data without granting root access to user-writable Python code.
    home = Path(account.pw_dir)
    config = home / 'Library/Application Support/Aeternia DNS'
    # Do these filesystem operations as the user, so symlinks cannot redirect root writes.
    for legacy in (config / 'servers.json', home / '.config/aeternia-dns/servers.json',
                   Path('/var/root/.config/aeternia-dns/servers.json')):
        if legacy.is_file():
            data = legacy.read_bytes()
            try:
                parsed = json.loads(data)
                if not isinstance(parsed, dict):
                    continue
            except ValueError:
                continue
            # Copy only through an unprivileged process, never chown a user path as root.
            if legacy == config / 'servers.json':
                break
            try:
                run(['/usr/bin/sudo', '-u', account.pw_name, '/usr/bin/python3', '-I', '-S', '-c',
                 'import pathlib,sys; p=pathlib.Path(sys.argv[1]); '
                 'p.mkdir(mode=0o700,parents=True,exist_ok=True); '
                 '(p/"servers.json").write_bytes(sys.stdin.buffer.read()); '
                 '(p/"servers.json").chmod(0o600)', str(config)], input=data)
            except subprocess.CalledProcessError:
                print('Не удалось перенести серверы в профиль пользователя; потребуется повторный ввод ID.')
            break
    # Old installs can have a root-owned ~/.config directory; don't recursively chown it.
    launcher = (f'#!/bin/sh\nexport PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin\n'
                f'exec /usr/bin/python3 -I -S -c \'import sys; sys.path.insert(0, "{ROOT}"); '
                'from aeternia_dns_switcher import main; main()\' "$@"\n')
    # /usr/local/bin may be Homebrew-owned. This launcher never has a sudoers grant.
    CLI.parent.mkdir(parents=True, exist_ok=True)
    # Write in our private directory then replace the directory entry; don't follow links.
    staged = ROOT / 'cli.new'
    put(staged, launcher.encode(), 0o755)
    os.replace(staged, CLI)
    APP.mkdir(parents=True, exist_ok=True)
    # Existing .app bundles are user-owned; replace them without traversing their contents as root.
    if APP.is_symlink():
        raise RuntimeError(f'Удалите символическую ссылку {APP} и повторите установку.')
    # Bundle content is only a nonprivileged launcher. Build in a private temporary directory.
    with tempfile.TemporaryDirectory(prefix='aeternia-app-') as temp:
        bundle = Path(temp) / 'Aeternia DNS.app'
        (bundle / 'Contents/MacOS').mkdir(parents=True)
        (bundle / 'Contents/Resources').mkdir()
        entry = bundle / 'Contents/MacOS/launcher'
        entry.write_text('#!/bin/sh\nexec /usr/bin/open -a Terminal.app /usr/local/bin/aeternia-dns-switcher\n')
        entry.chmod(0o755)
        (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
            'CFBundleExecutable': 'launcher', 'CFBundleName': 'Aeternia DNS',
            'CFBundleIdentifier': 'space.aeternia.dns-switcher',
            'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': '2.2.1',
            'CFBundleIconFile': 'icon.png',
        }))
        shutil.copyfile(src / 'logo.png', bundle / 'Contents/Resources/icon.png')
        shutil.rmtree(APP)
        shutil.copytree(bundle, APP)


def uninstall():
    # Abort deletion if any DNS restoration fails. Keep helper + journal for retry.
    helper('disable')
    RULE.unlink(missing_ok=True)
    HELPER.unlink(missing_ok=True)
    CLI.unlink(missing_ok=True)
    if APP.is_symlink():
        APP.unlink()
    elif APP.exists():
        shutil.rmtree(APP)
    for path in (ROOT, DATA):
        secure_dir(path)
        shutil.rmtree(path)
    print('Программа и её системная служба удалены; DNS восстановлены.')
    print('Пользовательские серверы при необходимости удалите: ~/Library/Application Support/Aeternia DNS')


def main():
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise RuntimeError('Установщик необходимо запускать на macOS через sudo.')
    if sys.version_info < (3, 9):
        raise RuntimeError('Обновите Apple Command Line Tools: нужен системный Python 3.9+.')
    os.umask(0o022)
    parser = argparse.ArgumentParser(description='Установка Aeternia DNS для macOS')
    parser.add_argument('--user', help='Пользователь Mac, которому разрешено переключать DNS')
    parser.add_argument('--uninstall', '-u', action='store_true')
    args = parser.parse_args()
    account = None if args.uninstall else installation_account(args.user)
    import fcntl
    secure_dir(DATA, 0o700)
    with (DATA / 'operation.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Другая операция DNS ещё выполняется. Повторите установку/удаление позже.')
        if args.uninstall:
            uninstall()
        else:
            install(account)
    if not args.uninstall:
        # After releasing the maintenance lock, verify an actual passwordless request.
        verification = run(['/usr/bin/sudo', '-u', account.pw_name,
                            '/usr/bin/sudo', '-n', str(HELPER)],
                           input='{"action":"status"}\n', capture_output=True, text=True)
        if not json.loads(verification.stdout).get('ok'):
            raise RuntimeError('Не удалось проверить запуск помощника без пароля.')
        print('Установка завершена. Откройте Aeternia DNS: запуск и переключение без пароля.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'Ошибка: {error}', file=sys.stderr)
        sys.exit(1)
