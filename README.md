# 🪐 Aeternia DNS Switcher

<p align="center">
  <img src="logo.png" alt="Aeternia DNS Switcher" width="200">
</p>

<p align="center">
  <b>TUI-утилита для управления Aeternia DoH DNS серверами через dnscrypt-proxy</b>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-2.1.2-blue" alt="Version">
  <img src="https://img.shields.io/badge/platform-Ubuntu%20%7C%20macOS-orange" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
</p>

---

## 📋 Описание

**Aeternia DNS Switcher** — инструмент для быстрого переключения между DNS-серверами [Aeternia](https://aeternia.space) (DNS-over-HTTPS) на Ubuntu.

### Возможности

- 🔍 **Автоматическая проверка и установка** `dnscrypt-proxy`
- 🌍 **8 серверов** в разных странах: Германия, Нидерланды, Финляндия, Франция, Индия, Казахстан, США, Турция
- ⚡ **Быстрая настройка** — достаточно ввести только ваш Aeternia ID
- 📊 **Пинг до серверов** с цветовой индикацией
- 🔄 **Безопасное переключение** с автобэкапом и откатом конфига
- ➕ **Добавление/удаление** серверов прямо из интерфейса
- 🖥️ **TUI-интерфейс** (Terminal User Interface) с навигацией клавиатурой

---

### Что такое Aeternia и где взять ID?

**[Aeternia](https://aeternia.space)** — это приватный DNS-сервис (DNS-over-HTTPS), который позволяет обходить региональные блокировки, блокировать рекламу и защищать ваш трафик от провайдера.

Чтобы пользоваться сервисом и нашей программой, вам понадобится **Aeternia ID**. Это ваш уникальный идентификатор (например, `063eb77c`), который выдается при регистрации в личном кабинете.

Пример вашей персональной ссылки DNS-over-HTTPS:
```text
https://de.aeternia.space:8443/dns-query/063eb77c
```
*Где `de` — это сервер в Германии, а `063eb77c` — ваш личный ID.*

Вам **не нужно** вручную прописывать эти ссылки. Просто скопируйте свой 8-значный ID при первом запуске `Aeternia DNS Switcher`, и программа сама сгенерирует все нужные ссылки (DNS-Stamps) для 8 стран (Нидерланды, США, Франция и др.) и аккуратно добавит их в конфигурацию.

---

## 🚀 Установка

### Ubuntu / Debian

#### Быстрая установка (одна команда)

```bash
curl -fsSL https://raw.githubusercontent.com/SCHR3IN/Aeternia-DNS-Switcher/main/aeternia-dns-installer.sh -o /tmp/aeternia-install.sh && sudo bash /tmp/aeternia-install.sh
```

#### Установка из репозитория

```bash
git clone https://github.com/SCHR3IN/Aeternia-DNS-Switcher.git
cd Aeternia-DNS-Switcher
sudo bash install.sh
```

### macOS

#### Быстрая установка (одна команда)

Предварительно убедитесь, что у вас установлен [Homebrew](https://brew.sh). Затем выполните команду в терминале:

```bash
brew install dnscrypt-proxy && sudo brew services start dnscrypt-proxy && pip3 install certifi --break-system-packages && curl -fsSL https://raw.githubusercontent.com/SCHR3IN/Aeternia-DNS-Switcher/main/aeternia-dns-installer.sh -o /tmp/aeternia-install.sh && sudo bash /tmp/aeternia-install.sh
```

Установщик сам скопирует скрипты, создаст `.app` бандл в `/Applications`, сгенерирует иконку и настроит лаунчер.

#### Установка из репозитория

```bash
brew install dnscrypt-proxy
sudo brew services start dnscrypt-proxy
pip3 install certifi --break-system-packages

git clone https://github.com/SCHR3IN/Aeternia-DNS-Switcher.git
cd Aeternia-DNS-Switcher
sudo bash install.sh
```

### Обновление

```bash
sudo aeternia-dns-switcher --update
```

Программа сама проверит наличие новой версии на GitHub, скачает и установит обновление.

Также можно обновить через клавишу `U` в интерфейсе программы.

### Удаление

#### Ubuntu

```bash
sudo aeternia-dns-switcher --uninstall
```

#### macOS

```bash
sudo aeternia-dns-switcher --uninstall
brew services stop dnscrypt-proxy  # опционально
```

---

## 🔧 Первый запуск

После установки запустите программу:

```bash
sudo aeternia-dns-switcher
```

Или найдите **«Aeternia DNS Switcher»** в меню приложений Ubuntu.

### Шаг 1: Проверка dnscrypt-proxy

При первом запуске программа проверит наличие `dnscrypt-proxy`. Если он не установлен, предложит установить автоматически:

```
╔═══════════════════════════════════════════════╗
║  ⚠  dnscrypt-proxy НЕ установлен!            ║
║                                               ║
║  [1] Установить dnscrypt-proxy                ║
║  [2] Отмена (выход)                           ║
╚═══════════════════════════════════════════════╝
```

### Шаг 2: Ввод Aeternia ID

Программа запросит ваш **Aeternia ID** — уникальный идентификатор из личного кабинета Aeternia:

```
Настройка серверов Aeternia

  Доступные страны:
    de — Германия
    nl — Нидерланды
    fi — Финляндия
    fr — Франция
    in — Индия
    kz — Казахстан
    us — США
    tr — Турция

  Aeternia ID: ________
```

После ввода ID программа автоматически сгенерирует конфигурации для всех 8 серверов.

### Шаг 3: Переключение DNS

Откроется TUI-интерфейс для управления серверами:

```
───────────────────────────────────────────
       Aeternia DNS Switcher v2
───────────────────────────────────────────
  Текущий DNS:    Германия
  Локальный DNS:  127.0.2.1
  dnscrypt-proxy: active
───────────────────────────────────────────
  Выберите сервер:
   > [1] Германия        42ms
     [2] Нидерланды      35ms
     [3] Финляндия       67ms
     [4] Франция         48ms
     [5] Индия          180ms
     [6] Казахстан       92ms
     [7] США            120ms
     [8] Турция          55ms
───────────────────────────────────────────
```

---

## ⌨️ Горячие клавиши

| Клавиша | Действие |
|---------|----------|
| `↑` / `↓` | Навигация по списку серверов |
| `1`–`8` | Быстрый выбор сервера по номеру |
| `Enter` | Применить выбранный сервер |
| `P` | Измерить пинг до всех серверов |
| `A` | Добавить/перенастроить серверы (ввод нового ID) |
| `D` | Удалить выбранный сервер |
| `R` | Перепроверить статус сервисов и DNS |
| `U` | Проверить и установить обновления |
| `B` | Откатить конфигурацию к последнему бэкапу |
| `Q` | Выход |

---

## 📁 Структура файлов

```
/usr/local/bin/
├── aeternia_dns_switcher.py    # Основной TUI-интерфейс
├── dns_utils.py                # Утилиты (stamp, ping, storage)
└── aeternia-dns-switcher       # Лаунчер (открывает терминал)

/etc/aeternia-dns/
└── servers.json                # Конфигурация серверов пользователя

/etc/dnscrypt-proxy/
└── dnscrypt-proxy.toml         # Конфиг dnscrypt-proxy (модифицируется)
```

---

## ⚙️ Требования

- **ОС:** Ubuntu 20.04+ / macOS 12+
- **Python:** 3.8+
- **Права:** root (sudo) для изменения DNS
- **macOS:** [Homebrew](https://brew.sh)
- **Аккаунт:** [Aeternia](https://aeternia.space) (для получения ID)

---

## 🔒 Безопасность

- Перед каждым изменением конфига создаётся **автоматический бэкап**
- При ошибке применения — **автооткат** к предыдущей конфигурации
- Конфиг проверяется через `dnscrypt-proxy -check` перед применением
- DNS-stamp генерируются локально по [официальной спецификации](https://dnscrypt.info/stamps-specifications/)

---

## 🛠️ Разработка

### Пересборка установщика

После внесения изменений в исходный код:

```bash
bash build_installer.sh
```

Создаст обновлённый `aeternia-dns-installer.sh` со всеми файлами внутри.

### Структура проекта

```
ASTRONIA_DNS/
├── aeternia_dns_switcher.py      # Главный файл (TUI + wizard)
├── dns_utils.py                  # Утилиты
├── install.sh                    # Установщик (вызывается из installer)
├── build_installer.sh            # Сборщик единого установщика
├── aeternia-dns-installer.sh     # Готовый self-extracting установщик
├── logo.png                      # Логотип / иконка приложения
├── VERSION                       # Файл версии (для автообновлений)
└── CHANGELOG.md                  # История изменений
```

---

## 📄 Лицензия

MIT License
