#!/usr/bin/env python3
"""Aeternia DNS Switcher v2 — TUI-переключатель Aeternia DoH в dnscrypt-proxy"""

import curses
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from dns_utils import (
    CONFIG_PATH, SERVER_NAME, COUNTRIES,
    generate_doh_stamp, build_server, generate_all_servers,
    load_servers, save_servers,
    is_dnscrypt_installed, install_dnscrypt_proxy,
    read_config, get_current_stamp, get_current_server,
    backup_config, find_latest_backup,
    patch_config, write_config_atomic,
    check_config_file, restart_services, get_service_statuses,
    check_dns, rollback_config,
    measure_ping, measure_all_pings,
    VERSION, check_for_update, run_update,
)

# ─── Предварительный экран (до curses) ───────────────────────────────────────

_GREEN = "\033[92m"
_RED = "\033[91m"
_YELLOW = "\033[93m"
_CYAN = "\033[96m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def print_banner():
    print(f"{_CYAN}{_BOLD}")
    print("╔═══════════════════════════════════════════════╗")
    print("║         Aeternia DNS Switcher  v2.0           ║")
    print("╚═══════════════════════════════════════════════╝")
    print(f"{_RESET}")


def pre_flight_check() -> bool:
    """Проверяет dnscrypt-proxy, серверы. Возвращает True если можно запускать TUI."""
    print_banner()

    # 1. Проверка root
    if os.geteuid() != 0:
        print(f"{_YELLOW}⚠  Приложение запущено без прав root.{_RESET}")
        print("   Для работы необходим sudo.")
        print()
        print("   sudo python3 aeternia_dns_switcher.py")
        print()
        ans = input("Продолжить в режиме просмотра? [y/N]: ")
        if ans.strip().lower() != "y":
            return False

    # 2. Проверка dnscrypt-proxy
    print(f"\n{_CYAN}[1/3] Проверка dnscrypt-proxy...{_RESET}")
    if is_dnscrypt_installed():
        print(f"  {_GREEN}✓ dnscrypt-proxy установлен{_RESET}")
    else:
        print(f"\n{_RED}{'═' * 50}{_RESET}")
        print(f"{_RED}{_BOLD}  ⚠  dnscrypt-proxy НЕ установлен!{_RESET}")
        print(f"{_RED}{'═' * 50}{_RESET}")
        print()
        print(f"  {_BOLD}[1]{_RESET} Установить dnscrypt-proxy (apt)")
        print(f"  {_BOLD}[2]{_RESET} Отмена (выход)")
        print()
        choice = input("Выберите действие: ").strip()
        if choice != "1":
            print("Выход.")
            return False
        if os.geteuid() != 0:
            print(f"{_RED}Для установки нужен root. Запустите: sudo python3 aeternia_dns_switcher.py{_RESET}")
            return False
        print(f"\n{_CYAN}Устанавливаю dnscrypt-proxy...{_RESET}")
        ok, msg = install_dnscrypt_proxy()
        if ok:
            print(f"  {_GREEN}✓ {msg}{_RESET}")
        else:
            print(f"  {_RED}✗ {msg}{_RESET}")
            return False

    # 3. Проверка серверов
    print(f"\n{_CYAN}[2/3] Проверка конфигурации серверов...{_RESET}")
    servers, user_id = load_servers()
    if servers:
        print(f"  {_GREEN}✓ Найдено серверов: {len(servers)} (ID: {user_id}){_RESET}")
    else:
        print(f"  {_YELLOW}Серверы не настроены.{_RESET}")
        if os.geteuid() != 0:
            print(f"  {_RED}Для настройки нужен root.{_RESET}")
            return False
        if not add_servers_wizard():
            return False

    # 4. Проверка конфига dnscrypt-proxy
    print(f"\n{_CYAN}[3/3] Проверка конфига dnscrypt-proxy...{_RESET}")
    if CONFIG_PATH.exists():
        print(f"  {_GREEN}✓ {CONFIG_PATH} найден{_RESET}")
    else:
        print(f"  {_YELLOW}⚠ {CONFIG_PATH} не найден — будет создан при первом переключении{_RESET}")

    print(f"\n{_GREEN}{_BOLD}Запускаю интерфейс...{_RESET}\n")
    time.sleep(0.5)
    return True


def add_servers_wizard() -> bool:
    """Wizard ввода Aeternia ID. Возвращает True при успехе."""
    print(f"\n{_CYAN}{'═' * 50}{_RESET}")
    print(f"{_BOLD}  Настройка серверов Aeternia{_RESET}")
    print(f"{_CYAN}{'═' * 50}{_RESET}")
    print()
    print("  Доступные страны:")
    for code, name in COUNTRIES.items():
        print(f"    {code} — {name}")
    print()
    print("  Введите ваш Aeternia ID.")
    print(f"  {_YELLOW}(это число из URL: /dns-query/XXXXXXXX){_RESET}")
    print()

    user_id = input("  Aeternia ID: ").strip()
    if not user_id:
        print(f"{_RED}ID не может быть пустым.{_RESET}")
        return False
    if not user_id.isdigit():
        print(f"{_RED}ID должен содержать только цифры.{_RESET}")
        return False

    servers = generate_all_servers(user_id)

    print(f"\n  {_GREEN}Будет создано {len(servers)} серверов:{_RESET}")
    for s in servers:
        print(f"    • {s['name']:12s}  {s['url']}")

    print()
    ans = input("  Сохранить? [Y/n]: ").strip()
    if ans.lower() == "n":
        print("Отменено.")
        return False

    save_servers(servers, user_id)
    print(f"  {_GREEN}✓ Серверы сохранены{_RESET}")
    return True


# ─── TUI ─────────────────────────────────────────────────────────────────────

_LOG_META = {
    "info": (0, "  "), "step": (4, ">>"),
    "ok": (1, "OK"), "warn": (3, "WW"), "err": (2, "EE"),
}


class App:
    def __init__(self, stdscr) -> None:
        self.scr = stdscr
        self.selected = 0
        self.log_lines = []
        self.is_root = os.geteuid() == 0
        self.last_backup = None
        self.svc_statuses = {}
        self.current_server = None
        self.servers, self.user_id = load_servers()
        self.pings = {}

        self._init_curses()
        self._load_initial_state()

    def _init_curses(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_GREEN, -1)
        curses.init_pair(2, curses.COLOR_RED, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_CYAN, -1)
        curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)
        curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_WHITE)
        curses.curs_set(0)
        self.scr.keypad(True)

    def _load_initial_state(self):
        try:
            cfg = read_config()
            self.current_server = get_current_server(cfg, self.servers)
            if self.current_server:
                for i, s in enumerate(self.servers):
                    if s["stamp"] == self.current_server["stamp"]:
                        self.selected = i
                        break
        except Exception as e:
            self._log(f"Ошибка чтения конфига: {e}", "err")
        self._refresh_statuses()
        # Автоматический пинг при запуске
        self._log("Измеряю пинг до серверов...", "step")
        self.pings = measure_all_pings(self.servers)

    def _refresh_statuses(self):
        self.svc_statuses = get_service_statuses()

    def _log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_lines.append((ts, msg, level))
        if len(self.log_lines) > 300:
            self.log_lines.pop(0)

    # ── Drawing ──────────────────────────────────────────────────────────────

    def _cp(self, pair, bold=False):
        a = curses.color_pair(pair)
        return a | curses.A_BOLD if bold else a

    def _put(self, row, col, text, attr=0):
        h, w = self.scr.getmaxyx()
        if row >= h or col >= w:
            return
        avail = w - col - 1
        if avail <= 0:
            return
        try:
            self.scr.addstr(row, col, text[:avail], attr)
        except curses.error:
            pass

    def _ping_str(self, code):
        p = self.pings.get(code)
        if p is None:
            return "  ---  ", 0
        if p < 80:
            return f" {p:5.0f}ms", self._cp(1)
        if p < 150:
            return f" {p:5.0f}ms", self._cp(3)
        return f" {p:5.0f}ms", self._cp(2)

    def draw(self):
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        if h < 20 or w < 50:
            self._put(0, 0, "Окно слишком маленькое (мин. 50x20)")
            self.scr.refresh()
            return

        row = 0

        # Title
        title = f"  Aeternia DNS Switcher v{VERSION}  [ID: {self.user_id}]  "
        self._put(row, 0, "─" * (w - 1), self._cp(4))
        row += 1
        self._put(row, max(0, (w - len(title)) // 2), title, self._cp(4, bold=True))
        row += 1
        self._put(row, 0, "─" * (w - 1), self._cp(4))
        row += 1

        # Info
        cur_name = self.current_server["name"] if self.current_server else "не выбран"
        svc_ok = all(v == "active" for v in self.svc_statuses.values())
        svc_color = self._cp(1, True) if svc_ok else self._cp(2, True)
        svc_label = "active" if svc_ok else " / ".join(
            f"{k.split('.')[0]}:{v}" for k, v in self.svc_statuses.items()
        )

        self._put(row, 2, "Текущий DNS:    ")
        self._put(row, 18, cur_name, curses.A_BOLD)
        row += 1
        self._put(row, 2, "Локальный DNS:  127.0.2.1")
        row += 1
        self._put(row, 2, "dnscrypt-proxy: ")
        self._put(row, 18, svc_label, svc_color)
        if not self.is_root:
            self._put(row, w - 16, "[нужен sudo]", self._cp(2))
        row += 1
        self._put(row, 0, "─" * (w - 1), self._cp(4))
        row += 1

        # Server list
        self._put(row, 2, "Выберите сервер:", curses.A_BOLD)
        ping_hint = "  (P — обновить пинг)" if not self.pings else ""
        self._put(row, 20, ping_hint, self._cp(4))
        row += 1

        for i, srv in enumerate(self.servers):
            prefix = " > " if i == self.selected else "   "
            num = f"[{i + 1}]" if i < 9 else f"[{i + 1}]"
            label = f"{prefix}{num} {srv['name']:12s}"

            ping_s, ping_attr = self._ping_str(srv.get("code", ""))

            is_active = bool(
                self.current_server and srv["stamp"] == self.current_server["stamp"]
            )

            if i == self.selected:
                self._put(row, 0, label.ljust(min(w - 12, 28)), self._cp(5, True))
                self._put(row, min(28, w - 12), ping_s, self._cp(5, True))
            elif is_active:
                self._put(row, 0, label, self._cp(1, True))
                self._put(row, len(label), ping_s, ping_attr)
            else:
                self._put(row, 0, label)
                self._put(row, len(label), ping_s, ping_attr)
            row += 1

        row += 1
        self._put(row, 0, "─" * (w - 1), self._cp(4))
        row += 1

        # Log
        self._put(row, 2, "Лог операций:", curses.A_BOLD)
        row += 1
        log_rows = h - row - 2
        if log_rows > 0:
            lc = {"ok": self._cp(1), "err": self._cp(2), "warn": self._cp(3), "step": self._cp(4), "info": 0}
            for ts, msg, lvl in self.log_lines[-log_rows:]:
                if row >= h - 2:
                    break
                badge = _LOG_META.get(lvl, (0, "  "))[1]
                line = f"  {ts} [{badge}] {msg}"
                self._put(row, 0, line, lc.get(lvl, 0))
                row += 1

        # Keys bar
        keys = " ↑↓ выбор  Enter примен.  A доб.  D удал.  P пинг  U обновить  R проверить  B откат  Q выход "
        self._put(h - 1, 0, keys.ljust(w - 1), self._cp(6))
        self.scr.refresh()

    # ── Operations ───────────────────────────────────────────────────────────

    def _step(self, msg):
        self._log(msg, "step")
        self.draw()

    def apply_server(self, srv):
        if not self.is_root:
            self._log("Нет прав root — запустите с sudo", "err")
            self.draw()
            return

        self._step(f"Переключаю DNS → {srv['name']}...")

        # Backup
        self._step("Создаю бэкап конфига...")
        try:
            bak = backup_config()
            self.last_backup = bak
            self._log(f"Бэкап: {bak.name}", "ok")
        except Exception as e:
            self._log(f"Ошибка бэкапа: {e}", "err")
            self.draw()
            return
        self.draw()

        # Patch
        self._step("Патчу конфиг...")
        try:
            cfg = read_config()
            new_cfg = patch_config(cfg, srv["stamp"])
            write_config_atomic(new_cfg)
            self._log("Конфиг обновлён", "ok")
        except Exception as e:
            self._log(f"Ошибка записи конфига: {e}", "err")
            self.draw()
            return
        self.draw()

        # Check
        self._step("Проверяю конфиг (до 20 сек)...")
        ok, out = check_config_file()
        if ok:
            if "lying" in out.lower():
                self._log("Конфиг OK (warning: may be lying resolver)", "warn")
            else:
                self._log("Конфиг OK", "ok")
        else:
            first_line = (out.splitlines()[0] if out else "неизвестная ошибка")[:80]
            self._log(f"Ошибка конфига: {first_line}", "err")
            self._step("Автооткат к бэкапу...")
            rb_ok, rb_msg = rollback_config(bak)
            self._log(rb_msg, "ok" if rb_ok else "err")
            self._refresh_statuses()
            self.draw()
            return
        self.draw()

        # Restart
        self._step("Перезапускаю dnscrypt-proxy...")
        ok, msg = restart_services()
        if ok:
            self._log("Сервисы перезапущены", "ok")
        else:
            self._log(f"Ошибка: {msg}", "err")
            self._step("Автооткат...")
            rb_ok, rb_msg = rollback_config(bak)
            self._log(rb_msg, "ok" if rb_ok else "err")
            self._refresh_statuses()
            self.draw()
            return
        self.draw()

        time.sleep(2)

        # Status
        self._refresh_statuses()
        svc_ok = all(v == "active" for v in self.svc_statuses.values())
        self._log(f"Статус: {'active' if svc_ok else str(self.svc_statuses)}", "ok" if svc_ok else "warn")

        try:
            self.current_server = get_current_server(read_config(), self.servers)
        except Exception:
            self.current_server = srv
        self.draw()

        # DNS test
        self._step("Проверяю DNS: dig google.com @127.0.2.1...")
        dns_ok, dns_msg = check_dns()
        if dns_ok:
            self._log(f"DNS OK: {dns_msg}", "ok")
            self._log(f"Переключено на {srv['name']}", "ok")
        else:
            self._log(f"DNS: {dns_msg}", "warn")
            self._log("Сервис работает, DNS не прошёл — проверьте сеть", "warn")

        # Обновляем пинг после переключения
        self._step("Обновляю пинг...")
        self.pings = measure_all_pings(self.servers)
        self.draw()

    def do_rollback(self):
        if not self.is_root:
            self._log("Нет прав root", "err")
            self.draw()
            return
        backup = self.last_backup or find_latest_backup()
        if not backup:
            self._log("Бэкап не найден", "err")
            self.draw()
            return
        self._step(f"Откат к {backup.name}...")
        ok, msg = rollback_config(backup)
        self._log(msg, "ok" if ok else "err")
        try:
            self.current_server = get_current_server(read_config(), self.servers)
        except Exception:
            pass
        self._refresh_statuses()
        self.draw()

    def do_recheck(self):
        self._step("Перепроверяю...")
        self._refresh_statuses()
        svc_ok = all(v == "active" for v in self.svc_statuses.values())
        self._log("Сервисы: " + ("active" if svc_ok else str(self.svc_statuses)),
                  "ok" if svc_ok else "warn")
        dns_ok, dns_msg = check_dns()
        self._log(f"DNS: {dns_msg}", "ok" if dns_ok else "warn")
        self.draw()

    def do_ping(self):
        self._step("Измеряю пинг до серверов...")
        self.draw()
        self.pings = measure_all_pings(self.servers)
        for s in self.servers:
            p = self.pings.get(s.get("code", ""))
            label = f"{p:.0f} ms" if p else "timeout"
            self._log(f"  {s['name']:12s} → {label}", "ok" if p and p < 150 else "warn")
        self.draw()

    def do_add_server(self):
        """Выход из curses → wizard → возврат."""
        curses.endwin()
        print()
        add_servers_wizard()
        self.servers, self.user_id = load_servers()
        self.scr = curses.initscr()
        self._init_curses()
        self._log("Серверы обновлены", "ok")
        self.draw()

    def do_delete_server(self):
        if not self.servers:
            return
        if not self.is_root:
            self._log("Нет прав root", "err")
            self.draw()
            return
        srv = self.servers[self.selected]
        self._log(f"Удалить {srv['name']}? Нажмите D ещё раз для подтверждения", "warn")
        self.draw()
        key = self.scr.getch()
        if key in (ord("d"), ord("D")):
            self.servers.pop(self.selected)
            save_servers(self.servers, self.user_id)
            if self.selected >= len(self.servers) and self.servers:
                self.selected = len(self.servers) - 1
            self._log(f"Сервер {srv['name']} удалён", "ok")
        else:
            self._log("Удаление отменено", "info")
        self.draw()

    def do_update(self):
        if not self.is_root:
            self._log("Нет прав root для обновления", "err")
            self.draw()
            return
        self._step(f"Проверяю обновления (текущая: v{VERSION})...")
        has_update, remote, err = check_for_update()
        if err:
            self._log(f"Ошибка проверки: {err}", "err")
            self.draw()
            return
        if not has_update:
            self._log(f"Установлена последняя версия (v{VERSION})", "ok")
            self.draw()
            return
        self._log(f"Доступна v{remote}! Устанавливаю...", "warn")
        self.draw()
        ok, msg = run_update()
        if ok:
            self._log(f"✓ {msg}", "ok")
        else:
            self._log(f"Ошибка: {msg}", "err")
        self.draw()

    # ── Main loop ────────────────────────────────────────────────────────────

    def run(self):
        self.draw()
        while True:
            key = self.scr.getch()
            if key == curses.ERR:
                continue
            if key in (ord("q"), ord("Q")):
                break
            elif key == curses.KEY_UP:
                if self.servers:
                    self.selected = (self.selected - 1) % len(self.servers)
                self.draw()
            elif key == curses.KEY_DOWN:
                if self.servers:
                    self.selected = (self.selected + 1) % len(self.servers)
                self.draw()
            elif ord("1") <= key <= ord("9") and (key - ord("1")) < len(self.servers):
                self.selected = key - ord("1")
                self.draw()
            elif key in (curses.KEY_ENTER, 10, 13):
                if self.servers:
                    self.apply_server(self.servers[self.selected])
            elif key in (ord("r"), ord("R")):
                self.do_recheck()
            elif key in (ord("b"), ord("B")):
                self.do_rollback()
            elif key in (ord("p"), ord("P")):
                self.do_ping()
            elif key in (ord("a"), ord("A")):
                self.do_add_server()
            elif key in (ord("d"), ord("D")):
                self.do_delete_server()
            elif key in (ord("u"), ord("U")):
                self.do_update()


# ─── Entry point ─────────────────────────────────────────────────────────────

def cli_update():
    """Обновление через командную строку: sudo aeternia-dns-switcher --update"""
    print_banner()
    print(f"  Текущая версия: {VERSION}")
    print(f"  Проверяю обновления...")
    has_update, remote, err = check_for_update()
    if err:
        print(f"  {_RED}Ошибка: {err}{_RESET}")
        sys.exit(1)
    if not has_update:
        print(f"  {_GREEN}✓ Установлена последняя версия ({VERSION}){_RESET}")
        sys.exit(0)
    print(f"  {_YELLOW}Доступна версия {remote} (текущая: {VERSION}){_RESET}")
    ans = input("  Обновить? [Y/n]: ").strip()
    if ans.lower() == "n":
        print("  Отменено.")
        sys.exit(0)
    print(f"  {_CYAN}Скачиваю и устанавливаю...{_RESET}")
    ok, msg = run_update()
    if ok:
        print(f"  {_GREEN}✓ {msg}{_RESET}")
    else:
        print(f"  {_RED}✗ {msg}{_RESET}")
        sys.exit(1)


def main():
    # CLI: --update
    if len(sys.argv) > 1 and sys.argv[1] in ("--update", "-u", "update"):
        cli_update()
        sys.exit(0)

    if not pre_flight_check():
        sys.exit(0)

    try:
        curses.wrapper(lambda s: App(s).run())
    except KeyboardInterrupt:
        pass
    print("Aeternia DNS Switcher закрыт.")


if __name__ == "__main__":
    main()
