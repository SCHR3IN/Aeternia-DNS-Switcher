#!/usr/bin/env bash
# Установщик Aeternia DNS Switcher
set -e

# ─── Пути ────────────────────────────────────────────────────────────────────

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_BIN="/usr/local/bin"

PY_SCRIPT="$SRC_DIR/aeternia_dns_switcher.py"
PY_UTILS="$SRC_DIR/dns_utils.py"
ICON_SRC="$SRC_DIR/aeternia-dns-switcher.svg"

PY_DST="$INSTALL_BIN/aeternia_dns_switcher.py"
UTILS_DST="$INSTALL_BIN/dns_utils.py"
LAUNCHER_DST="$INSTALL_BIN/aeternia-dns-switcher"

ICON_DIR="/usr/share/icons/hicolor/scalable/apps"
ICON_DST="$ICON_DIR/aeternia-dns-switcher.svg"

# Определяем реального пользователя (при запуске через sudo)
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")

APP_DIR="$REAL_HOME/.local/share/applications"
APP_DST="$APP_DIR/aeternia-dns-switcher.desktop"

DESKTOP_DIR=$(sudo -u "$REAL_USER" xdg-user-dir DESKTOP 2>/dev/null || echo "$REAL_HOME/Desktop")

# ─── Проверки ────────────────────────────────────────────────────────────────

if [[ "$1" == "--uninstall" || "$1" == "-u" ]]; then
    echo "=== Удаление Aeternia DNS Switcher ==="
    rm -f "$PY_DST" "$UTILS_DST" "$LAUNCHER_DST" "$ICON_DST"
    rm -f "$APP_DST" "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
    rm -f /etc/sudoers.d/aeternia-dns-switcher
    gtk-update-icon-cache "$ICON_DIR" 2>/dev/null || true
    update-desktop-database "$APP_DIR" 2>/dev/null || true
    echo "Удалено."
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Установщик требует прав root."
    echo "Запустите: sudo bash install.sh"
    exit 1
fi

if [[ ! -f "$PY_SCRIPT" ]]; then
    echo "Не найден aeternia_dns_switcher.py рядом с install.sh"
    exit 1
fi

if [[ ! -f "$PY_UTILS" ]]; then
    echo "Не найден dns_utils.py рядом с install.sh"
    exit 1
fi

if [[ ! -f "$ICON_SRC" ]]; then
    echo "Не найден aeternia-dns-switcher.svg рядом с install.sh"
    exit 1
fi

# ─── Установка ───────────────────────────────────────────────────────────────

echo "=== Установка Aeternia DNS Switcher v2 ==="
echo

# 1. Python-скрипты
echo "[1/6] Копирую скрипты в $INSTALL_BIN ..."
install -m 755 "$PY_SCRIPT" "$PY_DST"
install -m 644 "$PY_UTILS" "$UTILS_DST"

# 1.5. Создаю директорию конфигов
echo "[2/6] Создаю /etc/aeternia-dns/ ..."
mkdir -p /etc/aeternia-dns

# 2. Лаунчер-скрипт (открывает терминал с sudo)
echo "[3/6] Создаю лаунчер ..."
cat > "$LAUNCHER_DST" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
# Лаунчер Aeternia DNS Switcher — открывает терминал с sudo

PY_SCRIPT="/usr/local/bin/aeternia_dns_switcher.py"
TITLE="Aeternia DNS Switcher"
CLOSE='echo; echo "  Нажмите Enter для закрытия..."; read'
CMD="sudo python3 $PY_SCRIPT; $CLOSE"

if command -v gnome-terminal &>/dev/null; then
    exec gnome-terminal --title="$TITLE" -- bash -c "$CMD"
elif command -v xfce4-terminal &>/dev/null; then
    exec xfce4-terminal --title="$TITLE" -e "bash -c '$CMD'"
elif command -v konsole &>/dev/null; then
    exec konsole --title "$TITLE" -e bash -c "$CMD"
elif command -v xterm &>/dev/null; then
    exec xterm -title "$TITLE" -fa 'Monospace' -fs 11 -e bash -c "$CMD"
else
    zenity --error --text="Не найден эмулятор терминала.\n\nУстановите:\n  sudo apt install gnome-terminal" 2>/dev/null \
        || echo "Ошибка: не найден эмулятор терминала" >&2
    exit 1
fi
LAUNCHER_EOF
chmod 755 "$LAUNCHER_DST"

# 3. Иконка
echo "[4/6] Устанавливаю иконку ..."
mkdir -p "$ICON_DIR"
install -m 644 "$ICON_SRC" "$ICON_DST"
gtk-update-icon-cache "$ICON_DIR" 2>/dev/null || true

# 4. .desktop файл (в меню приложений)
echo "[5/6] Создаю .desktop файл ..."
mkdir -p "$APP_DIR"
cat > "$APP_DST" << DESKTOP_EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Aeternia DNS Switcher
Name[ru]=Aeternia DNS Switcher
Comment=Переключение Aeternia DoH DNS серверов
Comment[ru]=Переключение Aeternia DoH DNS серверов
Exec=$LAUNCHER_DST
Icon=aeternia-dns-switcher
Terminal=false
Categories=Network;System;Settings;
Keywords=dns;dnscrypt;vpn;aeternia;proxy;
StartupNotify=true
DESKTOP_EOF
chown "$REAL_USER:$REAL_USER" "$APP_DST"
update-desktop-database "$APP_DIR" 2>/dev/null || true

# 5. Ярлык на рабочем столе
echo "[6/6] Кладу ярлык на рабочий стол ($DESKTOP_DIR) ..."
if [[ -d "$DESKTOP_DIR" ]]; then
    cp "$APP_DST" "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
    chmod +x "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
    chown "$REAL_USER:$REAL_USER" "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
    # Пометить как доверенный (Ubuntu/GNOME 42+)
    sudo -u "$REAL_USER" \
        gio set "$DESKTOP_DIR/aeternia-dns-switcher.desktop" \
        metadata::trusted true 2>/dev/null || true
else
    echo "  Папка рабочего стола не найдена, ярлык пропущен."
    echo "  Приложение доступно в меню приложений."
fi

# ─── Итог ────────────────────────────────────────────────────────────────────

echo
echo "=== Установка завершена! ==="
echo
echo "  Ярлык на рабочем столе: $DESKTOP_DIR/aeternia-dns-switcher.desktop"
echo "  Меню приложений: поиск «Aeternia DNS»"
echo "  Терминал: aeternia-dns-switcher"
echo
echo "При первом запуске Ubuntu может спросить:"
echo "  «Недоверенный файл запустить?» — нажмите «Запустить»"
echo

# ─── Опционально: sudo без пароля ────────────────────────────────────────────

echo "──────────────────────────────────────────────"
echo "Необязательно: запуск без запроса пароля sudo"
echo
echo "Добавить правило?"
read -r -p "[y/N]: " SUDOERS_ANS
if [[ "$SUDOERS_ANS" =~ ^[Yy]$ ]]; then
    SUDOERS_FILE="/etc/sudoers.d/aeternia-dns-switcher"
    echo "$REAL_USER ALL=(ALL) NOPASSWD: $PY_DST" > "$SUDOERS_FILE"
    chmod 440 "$SUDOERS_FILE"
    echo "  Правило добавлено: $SUDOERS_FILE"
    echo "  Теперь sudo не будет спрашивать пароль для этого скрипта."
else
    echo "  Пропущено. При запуске будет запрашиваться пароль sudo."
fi
echo
