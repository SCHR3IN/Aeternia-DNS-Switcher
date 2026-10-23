"""Windows: служба dnscrypt-proxy и системный DNS с журналом исходных настроек.

Импортируется только на Windows. Программа работает от имени администратора
(манифест .exe запрашивает повышение прав), поэтому данные лежат в ProgramData.
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

APP_DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "AeterniaDNS"
CONFIG_PATH = APP_DATA / "dnscrypt-proxy.toml"
SERVERS_PATH = APP_DATA / "servers.json"
STATE_PATH = APP_DATA / "dns-state.json"
SERVICE = "dnscrypt-proxy"
LOCAL_DNS = ["127.0.0.1", "::1"]

# Основа конфига: server_names и блок [static.'aeternia-doh'] дописывает patch_config.
BASE_CONFIG = """listen_addresses = ['127.0.0.1:53', '[::1]:53']
server_names = []
max_clients = 250
ipv4_servers = true
ipv6_servers = false
doh_servers = true
cache = true
ignore_system_dns = true
bootstrap_resolvers = ['1.1.1.1:53', '9.9.9.9:53']
netprobe_address = '9.9.9.9:53'
netprobe_timeout = 30
"""

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_admin() -> bool:
    import ctypes
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except (AttributeError, OSError):
        return False


def dnscrypt_exe() -> Path:
    """В собранной программе dnscrypt-proxy лежит рядом с .exe, при запуске из исходников — в PATH."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "dnscrypt-proxy" / "dnscrypt-proxy.exe"
    found = os.environ.get("AETERNIA_DNSCRYPT") or shutil.which("dnscrypt-proxy")
    return Path(found) if found else Path("dnscrypt-proxy.exe")


def ensure_config() -> None:
    APP_DATA.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(BASE_CONFIG, encoding="utf-8")


def _run(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=_NO_WINDOW, errors="replace")


def _ps(script, timeout=60):
    """PowerShell с выводом в UTF-8: имена адаптеров бывают кириллицей."""
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='Stop'; "
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-Command", prefix + script],
                       capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    r.stdout = r.stdout.decode("utf-8", errors="replace")
    r.stderr = r.stderr.decode("utf-8", errors="replace")
    return r


# ─── Системный DNS ───────────────────────────────────────────────────────────

def _split(value):
    return [v for v in str(value or "").replace(",", " ").split() if v]


def active_interfaces():
    """Включённые адаптеры и их ручные DNS из реестра (пусто означает DHCP)."""
    script = (
        "$r = foreach ($a in Get-NetAdapter | Where-Object Status -eq 'Up') { "
        "$g = $a.InterfaceGuid; "
        "$v4 = (Get-ItemProperty \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\Interfaces\\$g\" "
        "-Name NameServer -ErrorAction SilentlyContinue).NameServer; "
        "$v6 = (Get-ItemProperty \"HKLM:\\SYSTEM\\CurrentControlSet\\Services\\Tcpip6\\Parameters\\Interfaces\\$g\" "
        "-Name NameServer -ErrorAction SilentlyContinue).NameServer; "
        "[pscustomobject]@{index=[int]$a.ifIndex; guid=\"$g\"; alias=$a.Name; v4=\"$v4\"; v6=\"$v6\"} }; "
        "ConvertTo-Json -InputObject @($r) -Compress"
    )
    r = _ps(script)
    if r.returncode != 0:
        raise RuntimeError(f"Не удалось прочитать сетевые адаптеры: {r.stderr.strip()[:200]}")
    data = json.loads(r.stdout or "[]")
    return [{"index": d["index"], "guid": d["guid"], "alias": d.get("alias") or "",
             "dns": _split(d.get("v4")) + _split(d.get("v6"))} for d in data]


def load_state():
    if not STATE_PATH.exists():
        return {"interfaces": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state):
    APP_DATA.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def use_local_dns():
    """Направляет все включённые адаптеры на dnscrypt-proxy. Исходные DNS сохраняются до изменений."""
    state = load_state()
    interfaces = active_interfaces()
    if not interfaces:
        raise RuntimeError("Нет включённых сетевых адаптеров")
    for iface in interfaces:
        if iface["guid"] not in state["interfaces"]:
            # Наш же адрес исходным не считаем: он остаётся после сбоя или старой версии.
            original = [v for v in iface["dns"] if v not in LOCAL_DNS]
            state["interfaces"][iface["guid"]] = {"alias": iface["alias"], "dns": original}
    save_state(state)
    indexes = ",".join(str(i["index"]) for i in interfaces)
    r = _ps(f"foreach ($i in @({indexes})) {{ Set-DnsClientServerAddress -InterfaceIndex $i "
            f"-ServerAddresses ('127.0.0.1','::1') }}; Clear-DnsClientCache")
    if r.returncode != 0:
        raise RuntimeError(f"Не удалось задать DNS: {r.stderr.strip()[:200]}")


def restore_dns():
    """Возвращает исходные DNS (DHCP или ручные) всем адаптерам из журнала."""
    state = load_state()
    if not state["interfaces"]:
        return []
    current = {i["guid"]: i for i in active_interfaces()}
    notes, commands = [], []
    for guid, saved in state["interfaces"].items():
        iface = current.get(guid)
        if iface is None:
            notes.append(f"{saved.get('alias') or guid}: адаптер выключен, пропущен")
            continue
        if saved["dns"]:
            servers = ",".join(f"'{s}'" for s in saved["dns"])
            commands.append(f"Set-DnsClientServerAddress -InterfaceIndex {iface['index']} -ServerAddresses ({servers})")
        else:
            commands.append(f"Set-DnsClientServerAddress -InterfaceIndex {iface['index']} -ResetServerAddresses")
    if commands:
        r = _ps("; ".join(commands) + "; Clear-DnsClientCache")
        if r.returncode != 0:
            raise RuntimeError(f"Не удалось вернуть DNS: {r.stderr.strip()[:200]}")
    save_state({"interfaces": {}})
    return notes


# ─── Служба dnscrypt-proxy ───────────────────────────────────────────────────

def _service_binary():
    r = _run(["sc.exe", "qc", SERVICE])
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if "BINARY_PATH_NAME" in line:
            return line.split(":", 1)[1].strip()
    return ""


def service_running() -> bool:
    r = _run(["sc.exe", "query", SERVICE])
    return r.returncode == 0 and "RUNNING" in r.stdout


def ensure_service():
    exe = dnscrypt_exe()
    if not exe.is_file():
        raise RuntimeError(f"Не найден {exe}. Переустановите программу.")
    binary = _service_binary()
    if binary is None:
        r = _run([str(exe), "-config", str(CONFIG_PATH), "-service", "install"])
        if r.returncode != 0:
            raise RuntimeError(f"Не удалось установить службу: {(r.stderr or r.stdout).strip()[:200]}")
    elif str(exe).lower() not in binary.lower():
        raise RuntimeError(f"Служба {SERVICE} уже установлена другой программой: {binary}")


def restart(ready=None):
    """Перезапускает службу с текущим конфигом и переводит системный DNS на неё.

    ready() проверяет, что dnscrypt-proxy уже отвечает. Пока он молчит, системный DNS
    не трогаем: иначе при недоступном сервере пропадёт весь интернет."""
    ensure_service()
    _run(["sc.exe", "stop", SERVICE])
    for _ in range(20):
        if not service_running():
            break
        time.sleep(0.5)
    r = _run(["sc.exe", "start", SERVICE])
    for _ in range(30):
        if service_running():
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(f"Служба не запустилась: {(r.stdout or r.stderr).strip()[:200]}")
    if ready is not None:
        for _ in range(40):
            if ready():
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("dnscrypt-proxy запущен, но не отвечает: сервер недоступен. Системный DNS не изменён.")
    use_local_dns()


def stop():
    """Сначала возвращает DNS, затем останавливает службу: интернет не пропадает."""
    notes = restore_dns()
    _run(["sc.exe", "stop", SERVICE])
    return notes


def uninstall():
    notes = stop()
    if _service_binary() is not None:
        _run([str(dnscrypt_exe()), "-service", "uninstall"])
        _run(["sc.exe", "delete", SERVICE])
    return notes
