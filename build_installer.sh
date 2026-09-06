#!/usr/bin/env bash
# Сборщик единого установщика Aeternia DNS Switcher
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SRC_DIR/aeternia-dns-installer.sh"

# Читаем версию
VER=$(cat "$SRC_DIR/VERSION" | tr -d '\n\r')
echo "=== Сборка установщика v$VER ==="

# Кодируем файлы в base64
B64_PY=$(base64 < "$SRC_DIR/aeternia_dns_switcher.py" | tr -d '\r\n')
B64_UTILS=$(base64 < "$SRC_DIR/dns_utils.py" | tr -d '\r\n')
B64_INSTALL=$(base64 < "$SRC_DIR/install.sh" | tr -d '\r\n')
B64_LOGO=$(base64 < "$SRC_DIR/logo.png" | tr -d '\r\n')
B64_MAC_HELPER=$(base64 < "$SRC_DIR/macos_helper.py" | tr -d '\r\n')
B64_MAC_INSTALL=$(base64 < "$SRC_DIR/macos_install.py" | tr -d '\r\n')

cat > "$OUT" << 'HEADER_EOF'
#!/usr/bin/env bash
# ╔═══════════════════════════════════════════════════════════╗
# ║   Aeternia DNS Switcher — Установщик (self-extracting)    ║
# ║                                                           ║
# ║   Запуск: chmod +x aeternia-dns-installer.sh              ║
# ║           sudo ./aeternia-dns-installer.sh                ║
# ╚═══════════════════════════════════════════════════════════╝
set -e

GREEN='\033[92m'
RED='\033[91m'
CYAN='\033[96m'
BOLD='\033[1m'
RESET='\033[0m'

echo -e "${CYAN}${BOLD}"
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       Aeternia DNS Switcher — Установка               ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo -e "${RESET}"

# Проверка root
if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}Установщик требует прав root.${RESET}"
    echo "Запустите: sudo ./aeternia-dns-installer.sh"
    exit 1
fi

# Создаём временную директорию
AETERNIA_TMP=$(mktemp -d /tmp/aeternia-install.XXXXXX)
trap 'rm -rf -- "$AETERNIA_TMP"' EXIT

echo -e "${CYAN}[1/3] Распаковка файлов...${RESET}"

HEADER_EOF

printf "AETERNIA_INSTALLER_VERSION='%s'\n" "$VER" >> "$OUT"

# Вставляем base64-данные и используем python для платформонезависимого декодирования
cat >> "$OUT" << PAYLOAD_EOF

# --- Embedded files (base64) ---
decode_base64() {
    if [[ "\$(uname -s)" == "Darwin" ]]; then
        /usr/bin/python3 -I -S -c "import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))"
    else
        python3 -c "import base64, sys; sys.stdout.buffer.write(base64.b64decode(sys.stdin.read()))"
    fi
}

echo "$B64_PY" | decode_base64 > "\$AETERNIA_TMP/aeternia_dns_switcher.py"
echo "$B64_UTILS" | decode_base64 > "\$AETERNIA_TMP/dns_utils.py"
echo "$B64_INSTALL" | decode_base64 > "\$AETERNIA_TMP/install.sh"
echo "$B64_LOGO" | decode_base64 > "\$AETERNIA_TMP/logo.png"
echo "$B64_MAC_HELPER" | decode_base64 > "\$AETERNIA_TMP/macos_helper.py"
echo "$B64_MAC_INSTALL" | decode_base64 > "\$AETERNIA_TMP/macos_install.py"

PAYLOAD_EOF

cat >> "$OUT" << 'FOOTER_EOF'
chmod +x "$AETERNIA_TMP/install.sh"
echo -e "${GREEN}  ✓ Файлы распакованы${RESET}"

echo -e "${CYAN}[2/3] Запускаю установщик...${RESET}"
echo

# Запускаем основной install.sh (он сам обрабатывает иконки для Linux и macOS)
bash "$AETERNIA_TMP/install.sh" "$@"

echo
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✓ Aeternia DNS Switcher успешно установлен!${RESET}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo
echo "  Запуск из терминала:  aeternia-dns-switcher"
echo "  Или найдите в меню приложений/Launchpad: «Aeternia DNS»"
echo
FOOTER_EOF

chmod +x "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "✓ Установщик собран: $OUT ($SIZE)"
echo "  Для установки:"
echo "    chmod +x aeternia-dns-installer.sh"
echo "    sudo ./aeternia-dns-installer.sh"
