#!/usr/bin/env python3
"""Утилиты Aeternia DNS Switcher: stamp, ping, storage, dnscrypt install"""

import base64
import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ─── Платформа ────────────────────────────────────────────────────────────────

IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

# ─── Пути ────────────────────────────────────────────────────────────────────

def _detect_config_path() -> Path:
    """Detect dnscrypt-proxy config path based on OS."""
    if IS_MACOS:
        # Homebrew: Apple Silicon vs Intel
        for p in ["/opt/homebrew/etc/dnscrypt-proxy.toml",
                  "/usr/local/etc/dnscrypt-proxy.toml"]:
            if Path(p).exists():
                return Path(p)
        # Default for Apple Silicon
        return Path("/opt/homebrew/etc/dnscrypt-proxy.toml")
    return Path("/etc/dnscrypt-proxy/dnscrypt-proxy.toml")


CONFIG_PATH = _detect_config_path()
SERVERS_PATH = Path("/etc/aeternia-dns/servers.json") if IS_LINUX else Path.home() / ".config" / "aeternia-dns" / "servers.json"
SERVER_NAME = "aeternia-doh"

# Все доступные страны Aeternia (код → название)
COUNTRIES = {
    "de": "Германия",
    "nl": "Нидерланды",
    "fi": "Финляндия",
    "fr": "Франция",
    "in": "Индия",
    "kz": "Казахстан",
    "us": "США",
    "tr": "Турция",
}

DEFAULT_PORT = "8443"
DEFAULT_PATH_PREFIX = "/dns-query/"


# ─── DNS Stamp ───────────────────────────────────────────────────────────────

def generate_doh_stamp(hostname_port: str, path: str) -> str:
    buf = bytearray()
    buf.append(0x02)
    buf.extend(b'\x00' * 8)
    buf.append(0x00)  # LP(addr) empty
    buf.append(0x00)  # VLP(hashes) empty
    buf.append(len(hostname_port))
    buf.extend(hostname_port.encode())
    buf.append(len(path))
    buf.extend(path.encode())
    encoded = base64.urlsafe_b64encode(bytes(buf)).rstrip(b'=').decode()
    return f"sdns://{encoded}"


def build_server(code: str, name: str, user_id: str) -> dict:
    hostname_port = f"{code}.aeternia.space:{DEFAULT_PORT}"
    path = f"{DEFAULT_PATH_PREFIX}{user_id}"
    url = f"https://{hostname_port}{path}"
    stamp = generate_doh_stamp(hostname_port, path)
    return {"name": name, "code": code, "url": url, "stamp": stamp}


def generate_all_servers(user_id: str) -> list:
    return [build_server(code, name, user_id) for code, name in COUNTRIES.items()]


# ─── Server Storage ──────────────────────────────────────────────────────────

def load_servers() -> tuple[list, str]:
    if not SERVERS_PATH.exists():
        return [], ""
    try:
        data = json.loads(SERVERS_PATH.read_text())
        return data.get("servers", []), data.get("user_id", "")
    except Exception:
        return [], ""


def save_servers(servers: list, user_id: str) -> None:
    SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SERVERS_PATH.write_text(json.dumps(
        {"user_id": user_id, "servers": servers}, ensure_ascii=False, indent=2
    ))


# ─── dnscrypt-proxy check/install ────────────────────────────────────────────

def is_dnscrypt_installed() -> bool:
    return shutil.which("dnscrypt-proxy") is not None


def install_dnscrypt_proxy() -> tuple[bool, str]:
    try:
        if IS_MACOS:
            # macOS: установка через Homebrew
            if not shutil.which("brew"):
                return False, "Homebrew не найден. Установите: https://brew.sh"
            # brew нельзя запускать под root
            user = os.environ.get("SUDO_USER", os.environ.get("USER", ""))
            if user and os.geteuid() == 0:
                r = subprocess.run(
                    ["sudo", "-u", user, "brew", "install", "dnscrypt-proxy"],
                    capture_output=True, text=True, timeout=300
                )
            else:
                r = subprocess.run(
                    ["brew", "install", "dnscrypt-proxy"],
                    capture_output=True, text=True, timeout=300
                )
            if r.returncode != 0:
                return False, f"brew install failed: {r.stderr[:200]}"
            return True, "dnscrypt-proxy установлен (brew)"
        else:
            # Linux: установка через apt
            r = subprocess.run(["apt", "update"], capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                return False, f"apt update failed: {r.stderr[:200]}"
            r = subprocess.run(
                ["apt", "install", "-y", "dnscrypt-proxy"],
                capture_output=True, text=True, timeout=300
            )
            if r.returncode != 0:
                return False, f"apt install failed: {r.stderr[:200]}"
            subprocess.run(["systemctl", "enable", "dnscrypt-proxy"], capture_output=True)
            return True, "dnscrypt-proxy установлен"
    except subprocess.TimeoutExpired:
        return False, "Timeout при установке"
    except Exception as e:
        return False, str(e)


# ─── Config utilities ────────────────────────────────────────────────────────

def read_config() -> str:
    return CONFIG_PATH.read_text()


_BLOCK_RE = re.compile(r"""^\[static\.['"]aeternia-doh['"]\]\s*$""")


def get_current_stamp(text: str) -> Optional[str]:
    m = re.search(
        r"""\[static\.['"]aeternia-doh['"]\].*?stamp\s*=\s*['"]([^'"]+)['"]""",
        text, re.DOTALL,
    )
    return m.group(1) if m else None


def get_current_server(text: str, servers: list) -> Optional[dict]:
    stamp = get_current_stamp(text)
    if not stamp:
        return None
    idx = {s["stamp"]: s for s in servers}
    return idx.get(stamp)


def backup_config() -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dst = CONFIG_PATH.parent / f"dnscrypt-proxy.toml.bak.{ts}"
    shutil.copy2(CONFIG_PATH, dst)
    return dst


def find_latest_backup() -> Optional[Path]:
    backups = sorted(CONFIG_PATH.parent.glob("dnscrypt-proxy.toml.bak.*"), reverse=True)
    return backups[0] if backups else None


def patch_config(text: str, new_stamp: str) -> str:
    lines = text.splitlines(keepends=True)
    result, in_block = [], False
    server_names_done = stamp_done = False

    for line in lines:
        stripped = line.strip()
        if not server_names_done and re.match(r"^\s*server_names\s*=", line):
            result.append(f"server_names = ['{SERVER_NAME}']\n")
            server_names_done = True
            continue
        if _BLOCK_RE.match(stripped):
            in_block = True
            result.append(f"[static.'{SERVER_NAME}']\n")
            continue
        if in_block:
            if stripped.startswith("[") and not _BLOCK_RE.match(stripped):
                in_block = False
                if not stamp_done:
                    result.append(f"stamp = '{new_stamp}'\n")
                    stamp_done = True
            elif not stamp_done and re.match(r"^\s*stamp\s*=", line):
                result.append(f"stamp = '{new_stamp}'\n")
                stamp_done = True
                continue
        result.append(line)

    if not server_names_done:
        result.insert(0, f"server_names = ['{SERVER_NAME}']\n")
    if not stamp_done:
        if result and not result[-1].endswith("\n"):
            result.append("\n")
        result.append(f"\n[static.'{SERVER_NAME}']\n")
        result.append(f"stamp = '{new_stamp}'\n")
    return "".join(result)


def write_config_atomic(text: str) -> None:
    tmp = CONFIG_PATH.with_suffix(".toml.tmp")
    tmp.write_text(text)
    tmp.rename(CONFIG_PATH)


# ─── System operations ───────────────────────────────────────────────────────

def run_cmd(cmd: list, timeout: int = 30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def check_config_file() -> tuple[bool, str]:
    try:
        cmd = ["dnscrypt-proxy", "-check", "-config", str(CONFIG_PATH)]
        if IS_LINUX:
            cmd = ["timeout", "20s"] + cmd
        r = run_cmd(cmd, timeout=25)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            return False, out
        return True, out
    except subprocess.TimeoutExpired:
        return False, "Timeout при проверке конфига"
    except FileNotFoundError:
        return False, "dnscrypt-proxy не найден в PATH"


def restart_services() -> tuple[bool, str]:
    if IS_MACOS:
        try:
            r = run_cmd(["brew", "services", "restart", "dnscrypt-proxy"])
            if r.returncode != 0:
                return False, f"Ошибка рестарта: {r.stderr.strip()}"
            return True, "OK"
        except FileNotFoundError:
            return False, "brew не найден"
    else:
        for svc in ["dnscrypt-proxy.socket", "dnscrypt-proxy.service"]:
            try:
                r = run_cmd(["systemctl", "restart", svc])
                if r.returncode != 0:
                    return False, f"Ошибка рестарта {svc}: {r.stderr.strip()}"
            except FileNotFoundError:
                return False, "systemctl не найден"
        return True, "OK"


def stop_services() -> tuple[bool, str]:
    """Останавливает dnscrypt-proxy."""
    if IS_MACOS:
        try:
            run_cmd(["brew", "services", "stop", "dnscrypt-proxy"])
            return True, "dnscrypt-proxy остановлен"
        except FileNotFoundError:
            return False, "brew не найден"
    else:
        for svc in ["dnscrypt-proxy.service", "dnscrypt-proxy.socket"]:
            try:
                run_cmd(["systemctl", "stop", svc])
                run_cmd(["systemctl", "disable", svc])
            except FileNotFoundError:
                return False, "systemctl не найден"
        run_cmd(["systemctl", "restart", "systemd-resolved"], timeout=10)
        return True, "dnscrypt-proxy остановлен, DNS по умолчанию"


def enable_services() -> tuple[bool, str]:
    """Включает и запускает dnscrypt-proxy."""
    if IS_MACOS:
        try:
            r = run_cmd(["brew", "services", "start", "dnscrypt-proxy"])
            if r.returncode != 0:
                return False, f"Ошибка запуска: {r.stderr.strip()}"
            return True, "OK"
        except FileNotFoundError:
            return False, "brew не найден"
    else:
        for svc in ["dnscrypt-proxy.socket", "dnscrypt-proxy.service"]:
            try:
                run_cmd(["systemctl", "enable", svc])
                r = run_cmd(["systemctl", "start", svc])
                if r.returncode != 0:
                    return False, f"Ошибка запуска {svc}: {r.stderr.strip()}"
            except FileNotFoundError:
                return False, "systemctl не найден"
        return True, "OK"


def get_service_statuses() -> dict:
    if IS_MACOS:
        try:
            r = run_cmd(["brew", "services", "list"], timeout=5)
            for line in r.stdout.splitlines():
                if "dnscrypt-proxy" in line:
                    parts = line.split()
                    status = parts[1] if len(parts) > 1 else "unknown"
                    # brew services: started, none, error
                    mapped = "active" if status == "started" else status
                    return {"dnscrypt-proxy": mapped}
            return {"dnscrypt-proxy": "not found"}
        except Exception:
            return {"dnscrypt-proxy": "unknown"}
    else:
        out = {}
        for svc in ["dnscrypt-proxy.socket", "dnscrypt-proxy.service"]:
            try:
                r = run_cmd(["systemctl", "is-active", svc], timeout=5)
                out[svc] = r.stdout.strip() or "unknown"
            except Exception:
                out[svc] = "unknown"
        return out


def check_dns() -> tuple[bool, str]:
    listen_addr = "127.0.2.1" if IS_LINUX else "127.0.0.1"
    try:
        r = run_cmd(["dig", "google.com", f"@{listen_addr}", "+time=5", "+tries=2"], timeout=15)
        if r.returncode != 0:
            return False, "dig завершился с ошибкой"
        if "NOERROR" in r.stdout and "ANSWER SECTION" in r.stdout:
            return True, f"google.com разрешён через {listen_addr}"
        return False, "Нет NOERROR/ANSWER SECTION в ответе dig"
    except subprocess.TimeoutExpired:
        return False, "Timeout DNS-запроса"
    except FileNotFoundError:
        hint = "sudo apt install dnsutils" if IS_LINUX else "brew install bind"
        return False, f"dig не найден ({hint})"


def rollback_config(backup: Path) -> tuple[bool, str]:
    try:
        shutil.copy2(backup, CONFIG_PATH)
        ok, msg = restart_services()
        if ok:
            return True, f"Откат выполнен из {backup.name}"
        return False, f"Файл восстановлен, рестарт не удался: {msg}"
    except Exception as e:
        return False, str(e)


# ─── Ping ────────────────────────────────────────────────────────────────────

def measure_ping(hostname: str) -> Optional[float]:
    try:
        # macOS ping: -W в миллисекундах, Linux: в секундах
        w_flag = "3000" if IS_MACOS else "3"
        r = subprocess.run(
            ["ping", "-c", "1", "-W", w_flag, hostname],
            capture_output=True, text=True, timeout=5
        )
        m = re.search(r"time=([\d.]+)\s*ms", r.stdout)
        return float(m.group(1)) if m else None
    except Exception:
        return None


def measure_all_pings(servers: list) -> dict:
    result = {}
    for s in servers:
        host = f"{s['code']}.aeternia.space"
        result[s["code"]] = measure_ping(host)
    return result


# ─── Обновления ──────────────────────────────────────────────────────────────

VERSION = "2.1.0"
GITHUB_REPO = "SCHR3IN/Aeternia-DNS-Switcher"
GITHUB_RAW = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"


def _get_ssl_context():
    """SSL context с поддержкой macOS (certifi / unverified fallback)."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    # macOS: Python без сертификатов — пробуем системные
    if IS_MACOS:
        ctx = ssl.create_default_context()
        try:
            ctx.load_default_certs()
        except Exception:
            # Fallback: отключаем верификацию (только для проверки VERSION)
            ctx = ssl._create_unverified_context()
        return ctx
    return ssl.create_default_context()


def check_for_update() -> tuple[bool, str, str]:
    """Проверяет наличие обновлений на GitHub.
    Returns: (has_update, remote_version, error_message)
    """
    try:
        import urllib.request
        url = f"{GITHUB_RAW}/VERSION"
        req = urllib.request.Request(url, headers={"User-Agent": "AeterniaDNS"})
        ctx = _get_ssl_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            remote = resp.read().decode().strip()
        if remote and remote != VERSION:
            return True, remote, ""
        return False, remote, ""
    except Exception as e:
        return False, "", str(e)


def run_update() -> tuple[bool, str]:
    """Скачивает и запускает последний установщик с GitHub."""
    try:
        import urllib.request
        import tempfile
        url = f"{GITHUB_RAW}/aeternia-dns-installer.sh"
        req = urllib.request.Request(url, headers={"User-Agent": "AeterniaDNS"})
        ctx = _get_ssl_context()
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            data = resp.read()

        tmp = Path(tempfile.mktemp(suffix=".sh", prefix="aeternia-update-"))
        tmp.write_bytes(data)
        tmp.chmod(0o755)

        r = subprocess.run(
            ["bash", str(tmp)],
            capture_output=True, text=True, timeout=300
        )
        tmp.unlink(missing_ok=True)

        if r.returncode != 0:
            return False, f"Ошибка установки: {r.stderr[:200]}"
        return True, "Обновление установлено. Перезапустите программу."
    except subprocess.TimeoutExpired:
        return False, "Timeout при установке"
    except Exception as e:
        return False, str(e)
