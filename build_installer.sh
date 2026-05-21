#!/usr/bin/env bash
# Сборщик единого установщика Aeternia DNS Switcher
set -e

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$SRC_DIR/aeternia-dns-installer.sh"

# Читаем версию
VER=$(cat "$SRC_DIR/VERSION" | tr -d '\n\r')
echo "=== Сборка установщика v$VER ==="

# Кодируем файлы в base64
B64_PY=$(base64 -w0 "$SRC_DIR/aeternia_dns_switcher.py")
B64_UTILS=$(base64 -w0 "$SRC_DIR/dns_utils.py")
B64_INSTALL=$(base64 -w0 "$SRC_DIR/install.sh")
B64_LOGO=$(base64 -w0 "$SRC_DIR/logo.jpg")

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
TMPDIR=$(mktemp -d /tmp/aeternia-install.XXXXXX)
trap "rm -rf $TMPDIR" EXIT

echo -e "${CYAN}[1/3] Распаковка файлов...${RESET}"

HEADER_EOF

# Вставляем base64-данные
cat >> "$OUT" << PAYLOAD_EOF

# --- Embedded files (base64) ---
echo "$B64_PY" | base64 -d > "\$TMPDIR/aeternia_dns_switcher.py"
echo "$B64_UTILS" | base64 -d > "\$TMPDIR/dns_utils.py"
echo "$B64_INSTALL" | base64 -d > "\$TMPDIR/install.sh"
echo "$B64_LOGO" | base64 -d > "\$TMPDIR/logo.jpg"

PAYLOAD_EOF

cat >> "$OUT" << 'FOOTER_EOF'
chmod +x "$TMPDIR/install.sh"
echo -e "${GREEN}  ✓ Файлы распакованы${RESET}"

echo -e "${CYAN}[2/3] Запускаю установщик...${RESET}"
echo

# Запускаем основной install.sh
bash "$TMPDIR/install.sh"

echo
echo -e "${CYAN}[3/3] Установка логотипа...${RESET}"

# Копируем logo.jpg для иконки приложения
ICON_SIZES_DIR="/usr/share/icons/hicolor"
for SIZE in 64 128 256; do
    ICON_DIR="$ICON_SIZES_DIR/${SIZE}x${SIZE}/apps"
    mkdir -p "$ICON_DIR"
    if command -v convert &>/dev/null; then
        convert "$TMPDIR/logo.jpg" -resize ${SIZE}x${SIZE} "$ICON_DIR/aeternia-dns-switcher.png" 2>/dev/null && \
            echo -e "  ${GREEN}✓ Иконка ${SIZE}x${SIZE}${RESET}" || true
    fi
done

# Если нет imagemagick — копируем jpg напрямую
if ! command -v convert &>/dev/null; then
    PIXMAP_DIR="/usr/share/pixmaps"
    mkdir -p "$PIXMAP_DIR"
    cp "$TMPDIR/logo.jpg" "$PIXMAP_DIR/aeternia-dns-switcher.jpg"
    echo -e "  ${GREEN}✓ Логотип установлен в $PIXMAP_DIR${RESET}"
    echo -e "  Совет: установите imagemagick для иконок в HD:"
    echo -e "    sudo apt install imagemagick"
fi

# Обновляем кеш иконок
gtk-update-icon-cache /usr/share/icons/hicolor 2>/dev/null || true

echo
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ✓ Aeternia DNS Switcher успешно установлен!${RESET}"
echo -e "${GREEN}${BOLD}═══════════════════════════════════════════════════════════${RESET}"
echo
echo "  Запуск из терминала:  aeternia-dns-switcher"
echo "  Или найдите в меню приложений: «Aeternia DNS»"
echo
FOOTER_EOF

chmod +x "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "✓ Установщик собран: $OUT ($SIZE)"
echo "  Для установки на Ubuntu:"
echo "    chmod +x aeternia-dns-installer.sh"
echo "    sudo ./aeternia-dns-installer.sh"
