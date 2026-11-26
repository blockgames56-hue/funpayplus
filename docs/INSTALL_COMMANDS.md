# Установка FunPay Plus на VPS

Репозиторий: https://github.com/blockgames56-hue/funpayplus

## Установка одной командой без домена

В SSH-консоли Ubuntu/Debian VPS, под root:

```bash
curl -fsSL https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh | bash -s -- --repo https://github.com/blockgames56-hue/funpayplus.git
```

Если вход выполнен пользователем с правами sudo, замените `| bash` на `| sudo bash`.
Установка создаёт `/opt/funpay-plus`, устанавливает Docker/Compose, собирает проект,
применяет миграции и запускает сервисы с автоматическим запуском после перезагрузки.

Ключ входа, на VPS под root:

```bash
cd /opt/funpay-plus && bash deploy/server.sh token
```

Доступ к панели: на компьютере замените `SERVER_IP` своим IP и выполните:

```powershell
ssh -L 8080:127.0.0.1:8080 root@SERVER_IP
```

Откройте http://localhost:8080 и введите ключ. Туннель нужен только для доступа к
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
