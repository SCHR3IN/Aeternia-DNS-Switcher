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
AETERNIA_VERSION='2.3.0'
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
if [[ -z "$AETERNIA_BREW" ]]; then
    echo 'Homebrew не найден. Запускаю его официальный установщик.'
    /usr/bin/curl --fail --show-error --silent --location --retry 3 \
        'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh' \
        -o "$AETERNIA_STAGE/homebrew.sh"
    /bin/bash "$AETERNIA_STAGE/homebrew.sh"
    for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -x "$candidate" ]]; then
            AETERNIA_BREW="$candidate"
            break
        fi
    done
fi
if [[ -z "$AETERNIA_BREW" ]]; then
    echo 'Homebrew не установлен. Завершите его установку и повторите эту команду.' >&2
    exit 1
fi
if ! /usr/bin/python3 -I -S -c 'import sys,curses; assert sys.version_info >= (3,9)' 2>/dev/null; then
    echo 'Нужны Apple Command Line Tools с Python 3.9+. Завершите установку и повторите команду.' >&2
    /usr/bin/xcode-select --install || true
    exit 1
fi
export PATH="$(/usr/bin/dirname "$AETERNIA_BREW"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if ! "$AETERNIA_BREW" list --versions dnscrypt-proxy >/dev/null 2>&1; then
    echo 'Устанавливаю DNS-движок через Homebrew от текущего пользователя...'
    "$AETERNIA_BREW" install dnscrypt-proxy
fi

# This is the only elevation performed by Aeternia; the target user is explicit.
# It also replaces a broken 2.1 Linux launcher and repairs the macOS .app bundle.
/usr/bin/sudo /bin/bash "$AETERNIA_STAGE/installer.sh" --user "$AETERNIA_USER"
echo 'Готово. Открываю Aeternia DNS; обычный запуск больше не требует sudo.'
/usr/bin/open '/Applications/Aeternia DNS.app'
