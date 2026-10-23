"""Проверка на настоящем Windows (GitHub Actions, права администратора).

Без аргументов: подключение к Smart DNS серверу через установленный dnscrypt-proxy,
проверка резолва и возврата DNS. Программа остаётся подключённой, чтобы следующий шаг
проверил, что деинсталлятор сам вернёт исходные DNS.
С --check-restored: DNS адаптеров совпадают со снимком до подключения.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("AETERNIA_DNSCRYPT", r"C:\Program Files\Aeternia DNS\dnscrypt-proxy\dnscrypt-proxy.exe")
SNAPSHOT = REPO / "build" / "dns-before.json"
SERVER_IP = "85.204.18.19"


def ps(command):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", command], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"PowerShell: {r.stderr.strip()}")
    return r.stdout


def system_dns():
    out = ps("Get-NetAdapter | Where-Object Status -eq 'Up' | ForEach-Object { "
             "[pscustomobject]@{guid=\"$($_.InterfaceGuid)\"; dns=@(Get-DnsClientServerAddress "
             "-InterfaceIndex $_.ifIndex | ForEach-Object ServerAddresses)} } | ConvertTo-Json -Compress")
    data = json.loads(out or "[]")
    data = data if isinstance(data, list) else [data]
    return {d["guid"]: sorted(d["dns"] or []) for d in data}


def resolve(name):
    ps("Clear-DnsClientCache")
    out = ps(f"(Resolve-DnsName -Name {name} -Type A -DnsOnly -ErrorAction Stop | "
             "Where-Object Type -eq 'A').IPAddress")
    return out.split()


def check(condition, message):
    print(("OK   " if condition else "FAIL ") + message)
    if not condition:
        raise SystemExit(1)


if "--check-restored" in sys.argv:
    before = json.loads(SNAPSHOT.read_text())
    after = system_dns()
    check(after == before, f"DNS после удаления совпадают с исходными: {after}")
    check(not Path(os.environ["ProgramData"], "AeterniaDNS", "dns-state.json").exists()
          or json.loads(Path(os.environ["ProgramData"], "AeterniaDNS", "dns-state.json").read_text())["interfaces"] == {},
          "журнал исходных DNS пуст")
    sys.exit(0)

import dns_utils as d

before = system_dns()
SNAPSHOT.parent.mkdir(exist_ok=True)
SNAPSHOT.write_text(json.dumps(before))
print("исходные DNS:", before)

server = d.build_server("fi", "Мой сервер", "", SERVER_IP, 8443)
d.write_config_atomic(d.patch_config(d.read_config(), server["stamp"]))
ok, out = d.check_config_file()
check(ok, f"конфиг dnscrypt-proxy принят: {out[-120:]}")
ok, msg = d.restart_services()
check(ok, f"служба запущена и DNS переключён: {msg}")
check(d.get_service_statuses()["dnscrypt-proxy"] == "active", "служба в состоянии RUNNING")
check(all(v == ["127.0.0.1", "::1"] for v in system_dns().values()), "все адаптеры смотрят на 127.0.0.1 и ::1")
ok, msg = d.check_dns()
check(ok, f"локальный DNS отвечает: {msg}")
check(SERVER_IP in resolve("chatgpt.com"), "chatgpt.com резолвится в наш сервер")
direct = resolve("example.com")
check(direct and SERVER_IP not in direct, f"example.com резолвится честно: {direct}")
check(d.measure_ping("1.1.1.1") is not None, "пинг на Windows разбирается")

ok, msg = d.stop_services()
check(ok, f"«Без прокси»: {msg}")
check(system_dns() == before, "исходные DNS вернулись")
check(d.get_service_statuses()["dnscrypt-proxy"] != "active", "служба остановлена")

ok, msg = d.restart_services()
check(ok, "повторное подключение для проверки деинсталлятора")
time.sleep(1)
print("готово")
