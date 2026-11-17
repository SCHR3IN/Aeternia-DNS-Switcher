#!/bin/bash
# Run as the Mac user, not through the legacy Aeternia launcher.
set -euo pipefail

if [[ "$(/usr/bin/uname -s)" != "Darwin" ]]; then
    echo 'Этот установщик предназначен для macOS.' >&2
    exit 1
fi
if [[ "$(/usr/bin/id -u)" == 0 ]]; then
    echo 'Запустите команду установки из обычного Терминала, без sudo перед /bin/bash.' >&2
    exit 1
fi

AETERNIA_USER=$(/usr/bin/id -un)
AETERNIA_VERSION='2.6.3'
AETERNIA_STAGE=$(/usr/bin/mktemp -d /tmp/aeternia-bootstrap.XXXXXX)
trap '/bin/rm -rf -- "$AETERNIA_STAGE"' EXIT

echo '=== Aeternia DNS Switcher: установка и восстановление запуска на macOS ==='

# Download the full installer first. A failed download must not change the installation.
/usr/bin/curl --fail --show-error --silent --location --retry 3 \
    --connect-timeout 20 --max-time 180 \
    "https://raw.githubusercontent.com/SCHR3IN/Aeternia-DNS-Switcher/main/aeternia-dns-installer.sh?v=$AETERNIA_VERSION" \
    -o "$AETERNIA_STAGE/installer.sh"
/bin/bash -n "$AETERNIA_STAGE/installer.sh"
if ! /usr/bin/grep -Fqx "AETERNIA_INSTALLER_VERSION='$AETERNIA_VERSION'" "$AETERNIA_STAGE/installer.sh"; then
    echo 'Скачан устаревший установщик. Повторите команду через минуту.' >&2
    exit 1
fi

AETERNIA_BREW=''
for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    if [[ -x "$candidate" ]]; then
        AETERNIA_BREW="$candidate"
        break
    fi
done
if ! /usr/bin/python3 -I -S -c 'import sys,curses; assert sys.version_info >= (3,9)' 2>/dev/null; then
    echo 'Нужны Apple Command Line Tools с Python 3.9+. Завершите установку и повторите команду.' >&2
    /usr/bin/xcode-select --install || true
    exit 1
fi
AETERNIA_DNSCRYPT_ARGS=()
if [[ -n "$AETERNIA_BREW" ]]; then
    export PATH="$(/usr/bin/dirname "$AETERNIA_BREW"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    if ! "$AETERNIA_BREW" list --versions dnscrypt-proxy >/dev/null 2>&1; then
        echo 'Устанавливаю DNS-движок через Homebrew от текущего пользователя...'
        "$AETERNIA_BREW" install dnscrypt-proxy
    fi
else
    # Без Homebrew (на Intel он больше не ставится): официальная сборка dnscrypt-proxy
    # с GitHub, контрольная сумма зафиксирована здесь.
    AETERNIA_DNSCRYPT_VERSION='2.1.20'
    case "$(/usr/bin/uname -m)" in
        x86_64) AETERNIA_DNSCRYPT_ARCH='x86_64'; AETERNIA_DNSCRYPT_SHA='0a93a115c3cb2b0d2ec2d1c8ce6e9005d6a842710a6a1972c2ebe39afd272f1f' ;;
        arm64)  AETERNIA_DNSCRYPT_ARCH='arm64';  AETERNIA_DNSCRYPT_SHA='5fddb05d3920efce121ca70f3f7e4f952f9572dd4c9c699ee28722e82ede9434' ;;
        *) echo 'Неподдерживаемый процессор.' >&2; exit 1 ;;
    esac
    echo 'Homebrew не найден: скачиваю официальную сборку DNS-движка dnscrypt-proxy...'
    /usr/bin/curl --fail --show-error --silent --location --retry 3 --connect-timeout 20 --max-time 180 \
        "https://github.com/DNSCrypt/dnscrypt-proxy/releases/download/$AETERNIA_DNSCRYPT_VERSION/dnscrypt-proxy-macos_$AETERNIA_DNSCRYPT_ARCH-$AETERNIA_DNSCRYPT_VERSION.zip" \
        -o "$AETERNIA_STAGE/dnscrypt.zip"
    if [[ "$(/usr/bin/shasum -a 256 "$AETERNIA_STAGE/dnscrypt.zip" | /usr/bin/cut -d' ' -f1)" != "$AETERNIA_DNSCRYPT_SHA" ]]; then
        echo 'Контрольная сумма DNS-движка не совпала. Установка отменена.' >&2
        exit 1
    fi
    /usr/bin/unzip -q -o "$AETERNIA_STAGE/dnscrypt.zip" -d "$AETERNIA_STAGE/dnscrypt"
    AETERNIA_DNSCRYPT_BIN=$(/usr/bin/find "$AETERNIA_STAGE/dnscrypt" -type f -name dnscrypt-proxy | /usr/bin/head -1)
    [[ -n "$AETERNIA_DNSCRYPT_BIN" ]] || { echo 'В архиве нет dnscrypt-proxy.' >&2; exit 1; }
    AETERNIA_DNSCRYPT_ARGS=(--dnscrypt "$AETERNIA_DNSCRYPT_BIN")
fi

# This is the only elevation performed by Aeternia; the target user is explicit.
# It also replaces a broken 2.1 Linux launcher and repairs the macOS .app bundle.
/usr/bin/sudo /bin/bash "$AETERNIA_STAGE/installer.sh" --user "$AETERNIA_USER" ${AETERNIA_DNSCRYPT_ARGS[@]+"${AETERNIA_DNSCRYPT_ARGS[@]}"}
echo 'Готово. Открываю Aeternia DNS; обычный запуск больше не требует sudo.'
/usr/bin/open '/Applications/Aeternia DNS.app'
