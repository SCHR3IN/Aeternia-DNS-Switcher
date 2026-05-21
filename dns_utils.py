#!/usr/bin/env python3
"""Утилиты Aeternia DNS Switcher: stamp, ping, storage, dnscrypt install"""

import base64
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# ─── Пути ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path("/etc/dnscrypt-proxy/dnscrypt-proxy.toml")
SERVERS_PATH = Path("/etc/aeternia-dns/servers.json")
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
        r = run_cmd(["timeout", "20s", "dnscrypt-proxy", "-check", "-config", str(CONFIG_PATH)], timeout=25)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            return False, out
        return True, out
    except subprocess.TimeoutExpired:
        return False, "Timeout при проверке конфига"
    except FileNotFoundError:
        return False, "dnscrypt-proxy не найден в PATH"


def restart_services() -> tuple[bool, str]:
    for svc in ["dnscrypt-proxy.socket", "dnscrypt-proxy.service"]:
        try:
            r = run_cmd(["systemctl", "restart", svc])
            if r.returncode != 0:
                return False, f"Ошибка рестарта {svc}: {r.stderr.strip()}"
        except FileNotFoundError:
            return False, "systemctl не найден"
    return True, "OK"


def get_service_statuses() -> dict:
    out = {}
    for svc in ["dnscrypt-proxy.socket", "dnscrypt-proxy.service"]:
        try:
            r = run_cmd(["systemctl", "is-active", svc], timeout=5)
            out[svc] = r.stdout.strip() or "unknown"
        except Exception:
            out[svc] = "unknown"
    return out


def check_dns() -> tuple[bool, str]:
    try:
        r = run_cmd(["dig", "google.com", "@127.0.2.1", "+time=5", "+tries=2"], timeout=15)
        if r.returncode != 0:
            return False, "dig завершился с ошибкой"
        if "NOERROR" in r.stdout and "ANSWER SECTION" in r.stdout:
            return True, "google.com разрешён через 127.0.2.1"
        return False, "Нет NOERROR/ANSWER SECTION в ответе dig"
    except subprocess.TimeoutExpired:
        return False, "Timeout DNS-запроса"
    except FileNotFoundError:
        return False, "dig не найден (sudo apt install dnsutils)"


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
        r = subprocess.run(
            ["ping", "-c", "1", "-W", "3", hostname],
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
