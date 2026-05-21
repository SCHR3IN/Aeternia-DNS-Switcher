#!/usr/bin/env bash
# Установщик Aeternia DNS Switcher
set -e

# ─── Пути ────────────────────────────────────────────────────────────────────

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_BIN="/usr/local/bin"

PY_SCRIPT="$SRC_DIR/aeternia_dns_switcher.py"
PY_UTILS="$SRC_DIR/dns_utils.py"
ICON_SRC="$SRC_DIR/logo.png"

PY_DST="$INSTALL_BIN/aeternia_dns_switcher.py"
UTILS_DST="$INSTALL_BIN/dns_utils.py"
LAUNCHER_DST="$INSTALL_BIN/aeternia-dns-switcher"

IS_MACOS=0
if [[ "$(uname -s)" == "Darwin" ]]; then
    IS_MACOS=1
fi

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~$REAL_USER")

# ─── Проверки ────────────────────────────────────────────────────────────────

if [[ "$1" == "--uninstall" || "$1" == "-u" ]]; then
    echo "=== Удаление Aeternia DNS Switcher ==="
    rm -f "$PY_DST" "$UTILS_DST" "$LAUNCHER_DST"
    if [[ "$IS_MACOS" == "1" ]]; then
        rm -rf "/Applications/Aeternia DNS.app"
    else
        rm -f "/usr/share/pixmaps/aeternia-dns-switcher.jpg"
        rm -f "$REAL_HOME/.local/share/applications/aeternia-dns-switcher.desktop"
        DESKTOP_DIR=$(sudo -u "$REAL_USER" xdg-user-dir DESKTOP 2>/dev/null || echo "$REAL_HOME/Desktop")
        rm -f "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
        rm -f /etc/sudoers.d/aeternia-dns-switcher
        update-desktop-database "$REAL_HOME/.local/share/applications" 2>/dev/null || true
    fi
    echo "Удалено."
    exit 0
fi

if [[ $EUID -ne 0 ]]; then
    echo "Установщик требует прав root."
    echo "Запустите: sudo bash install.sh"
    exit 1
fi

if [[ ! -f "$PY_SCRIPT" || ! -f "$PY_UTILS" || ! -f "$ICON_SRC" ]]; then
    echo "Файлы установки не найдены!"
    exit 1
fi

# ─── Установка ───────────────────────────────────────────────────────────────

echo "=== Установка Aeternia DNS Switcher ==="
echo

# 1. Python-скрипты
echo "[1/6] Копирую скрипты в $INSTALL_BIN ..."
install -m 755 "$PY_SCRIPT" "$PY_DST"
install -m 644 "$PY_UTILS" "$UTILS_DST"

if [[ "$IS_MACOS" == "0" ]]; then
    echo "[2/6] Создаю /etc/aeternia-dns/ ..."
    mkdir -p /etc/aeternia-dns
else
    echo "[2/6] macOS: Пропускаю создание /etc/aeternia-dns/ (используется ~/.config) ..."
fi

echo "[3/6] Создаю лаунчер CLI ..."
if [[ "$IS_MACOS" == "1" ]]; then
cat > "$LAUNCHER_DST" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
# Лаунчер Aeternia DNS Switcher (macOS)
exec sudo python3 "/usr/local/bin/aeternia_dns_switcher.py" "$@"
LAUNCHER_EOF
else
cat > "$LAUNCHER_DST" << 'LAUNCHER_EOF'
#!/usr/bin/env bash
# Лаунчер Aeternia DNS Switcher (Linux)

PY_SCRIPT="/usr/local/bin/aeternia_dns_switcher.py"
TITLE="Aeternia DNS Switcher"

if [[ $# -gt 0 ]]; then
    exec sudo python3 "$PY_SCRIPT" "$@"
fi

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
    zenity --error --text="Не найден эмулятор терминала." 2>/dev/null || echo "Ошибка: не найден эмулятор терминала" >&2
    exit 1
fi
LAUNCHER_EOF
fi
chmod 755 "$LAUNCHER_DST"

if [[ "$IS_MACOS" == "1" ]]; then
    echo "[4/6] Создаю macOS .app bundle ..."
    APP_BUNDLE="/Applications/Aeternia DNS.app"
    mkdir -p "$APP_BUNDLE/Contents/MacOS"
    mkdir -p "$APP_BUNDLE/Contents/Resources"
    
    # Скрипт запуска для GUI
    cat > "$APP_BUNDLE/Contents/MacOS/launcher" << 'SCRIPT_EOF'
#!/bin/bash
open -a Terminal.app /usr/local/bin/aeternia-dns-switcher
SCRIPT_EOF
    chmod +x "$APP_BUNDLE/Contents/MacOS/launcher"
    
    # Info.plist
    cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>CFBundleName</key>
    <string>Aeternia DNS</string>
    <key>CFBundleIconFile</key>
    <string>icon</string>
    <key>CFBundleIdentifier</key>
    <string>space.aeternia.dns-switcher</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.1</string>
    <key>CFBundleSupportedPlatforms</key>
    <array>
        <string>MacOSX</string>
    </array>
</dict>
</plist>
PLIST_EOF

    # Иконка
    echo "[5/6] Генерирую иконку icon.icns ..."
    if command -v sips &>/dev/null; then
        sips -s format icns "$ICON_SRC" --out "$APP_BUNDLE/Contents/Resources/icon.icns" 2>/dev/null || cp "$ICON_SRC" "$APP_BUNDLE/Contents/Resources/icon.png"
    else
        cp "$ICON_SRC" "$APP_BUNDLE/Contents/Resources/icon.png"
    fi
    # Принудительно обновляем кэш иконок macOS
    touch "$APP_BUNDLE"

    echo "[6/6] Назначение прав для .app ..."
    chown -R "$REAL_USER" "$APP_BUNDLE"

    echo
    echo "=== Установка завершена! ==="
    echo "  Приложение Aeternia DNS установлено в /Applications"
    echo "  Откройте Launchpad, чтобы его запустить."
else
    # Linux Desktop Entry and Icon logic
    ICON_DIR="/usr/share/pixmaps"
    ICON_DST="$ICON_DIR/aeternia-dns-switcher.jpg"
    APP_DIR="$REAL_HOME/.local/share/applications"
    APP_DST="$APP_DIR/aeternia-dns-switcher.desktop"
    DESKTOP_DIR=$(sudo -u "$REAL_USER" xdg-user-dir DESKTOP 2>/dev/null || echo "$REAL_HOME/Desktop")

    echo "[4/6] Устанавливаю иконку ..."
    mkdir -p "$ICON_DIR"
    install -m 644 "$ICON_SRC" "$ICON_DST"

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
Icon=/usr/share/pixmaps/aeternia-dns-switcher.jpg
Terminal=false
Categories=Network;System;Settings;
Keywords=dns;dnscrypt;vpn;aeternia;proxy;
StartupNotify=true
DESKTOP_EOF
    chown "$REAL_USER:$REAL_USER" "$APP_DST"
    update-desktop-database "$APP_DIR" 2>/dev/null || true

    echo "[6/6] Кладу ярлык на рабочий стол ..."
    if [[ -d "$DESKTOP_DIR" ]]; then
        cp "$APP_DST" "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
        chmod +x "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
        chown "$REAL_USER:$REAL_USER" "$DESKTOP_DIR/aeternia-dns-switcher.desktop"
        sudo -u "$REAL_USER" gio set "$DESKTOP_DIR/aeternia-dns-switcher.desktop" metadata::trusted true 2>/dev/null || true
    fi

    echo
    echo "=== Установка завершена! ==="
    echo "  Меню приложений: поиск «Aeternia DNS»"
    echo "  Терминал: aeternia-dns-switcher"
    
    echo
    echo "──────────────────────────────────────────────"
    echo "Необязательно: запуск без запроса пароля sudo"
    echo "Добавить правило?"
    read -r -p "[y/N]: " SUDOERS_ANS
    if [[ "$SUDOERS_ANS" =~ ^[Yy]$ ]]; then
        SUDOERS_FILE="/etc/sudoers.d/aeternia-dns-switcher"
        echo "$REAL_USER ALL=(ALL) NOPASSWD: $PY_DST" > "$SUDOERS_FILE"
        chmod 440 "$SUDOERS_FILE"
        echo "  Правило добавлено."
    fi
fi
