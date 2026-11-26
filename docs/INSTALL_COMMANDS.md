# Установка FunPay Plus на VPS

Репозиторий: https://github.com/blockgames56-hue/funpayplus

## Самый простой способ: VPS + Telegram + Mini App без домена

В SSH-консоли Ubuntu/Debian VPS под root выполните одну команду:

```bash
curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --easy
```

Дальше нужно только:

1. Ввести токен бота от @BotFather. Ввод скрыт, токен не передаётся в аргументах команд.
2. При желании ввести golden_key FunPay; Enter оставляет текущие аккаунты и позволяет
   подключить аккаунт позже в панели.
3. Открыть выданную ссылку Telegram и нажать «Запустить».

Установщик сам ставит зависимости, создаёт ключи и базу, определяет публичный IPv4,
настраивает бесплатный HTTPS прямо на IP, запускает сервисы и готовит Mini App.
Домен, DNS-записи, Chat ID и ручное заполнение HTTPS-адреса не нужны.
На VPS бот продолжает работать после закрытия компьютера и SSH.

Для Mini App входящие TCP 80 и 443 должны быть открыты в панели провайдера VPS.
Let’s Encrypt выпускает сертификат для IP, Caddy автоматически его продлевает.
Если IPv4/сертификат недоступен либо web-порты заняты, установка сохраняет рабочие
backend и бот с кнопками. Открыть порты у провайдера установщик без доступа к его
панели не может. После исправления достаточно повторить `bash deploy/server.sh easy`.
Токены, аккаунты и существующие настройки при повторной настройке сохраняются.
Публичный вход с самого начала защищён подтверждением через Telegram.

Если нужен только бот, вообще без входящих web-портов:

```bash
curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --bot-only
```

В существующей обновлённой установке:

```bash
cd /opt/funpay-plus
bash deploy/server.sh easy
```

Только повторная привязка Telegram: `bash deploy/server.sh telegram`.
Можно явно указать адрес: `--easy --ip ВАШ_ПУБЛИЧНЫЙ_IPV4`. Существующий собственный
домен сохраняется. Вход в EXE по выданному HTTPS-адресу подтверждается через бота.
Если SSH-пользователь имеет sudo вместо root, замените `| bash` на `| sudo bash`.

Новая установка не переносит автоматически данные с другого компьютера.
[Перенос существующей базы и файлов](SERVER.md#перенос-текущих-данных-с-windows-одной-командой).

## Режим с SSH-туннелем без публичного HTTPS

В SSH-консоли Ubuntu/Debian VPS, под root:

```bash
curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --repo https://github.com/blockgames56-hue/funpayplus.git
```

Если вход выполнен пользователем с правами sudo, замените `| bash` на `| sudo bash`.
Установка создаёт `/opt/funpay-plus`, устанавливает Docker/Compose, собирает проект,
применяет миграции и запускает сервисы с автоматическим запуском после перезагрузки.

Доступ к панели: на компьютере замените `SERVER_IP` своим IP и выполните:

```powershell
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
```

Откройте http://localhost:8080 и выберите текущую установку. Ключ входа не требуется. Туннель нужен только для доступа к
панели; фоновые задачи продолжают работать после его закрытия и выключения ПК.

## Установка с HTTPS-доменом

Направьте домен на VPS и откройте входящие TCP 80/443. Под root:

```bash
curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --repo https://github.com/blockgames56-hue/funpayplus.git --domain panel.example.com
```

Замените `panel.example.com` своим доменом. Панель будет доступна по HTTPS.

## Управление существующей установкой

В каталоге `/opt/funpay-plus`:

```bash
bash deploy/server.sh status
bash deploy/server.sh logs
bash deploy/server.sh backup
bash deploy/server.sh update
```

Новая установка пустая. Существующий магазин переносится отдельно с Windows:
[перенос аккаунтов, настроек и данных](SERVER.md#перенос-текущих-данных-с-windows-одной-командой).
