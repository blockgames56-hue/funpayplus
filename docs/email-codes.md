# Email Code Fetcher

`backend/app/email_codes.py` содержит класс `EmailCodeFetcher`. Для запуска нужны
зависимости из `backend/requirements.txt`, доступ к собственному почтовому ящику
и пароль приложения, если этого требует провайдер.

```python
from app.email_codes import MailboxConfig, EmailCodeFetcher, save_mailbox, load_mailbox
from app.events import bus

async def on_code(data):
    # Обработать data['code']; не записывать его в журнал.
    # data также содержит mailbox_id, service, uid, uidvalidity.
    pass

config = MailboxConfig(
    ident="sample", host="imap.example.com", username="sample@example.com",
    password="APP_PASSWORD",
    # Необязательно: proxy_host="127.0.0.1", proxy_port=1080,
    # proxy_type="socks5", proxy_username="user", proxy_password="password",
)
await save_mailbox(config)  # Пароли зашифрованы существующим AES vault.
bus.on("new_2fa_code", on_code)
fetcher = EmailCodeFetcher(await load_mailbox("sample"))
await fetcher.start()
# При завершении приложения:
await fetcher.stop()
bus.off("new_2fa_code", on_code)
```

Соединение использует TLS с проверкой сертификата. HTTP-прокси должен поддерживать
CONNECT; SOCKS5 разрешает DNS через прокси. Глобальная подмена socket не применяется.
Сервер обязан поддерживать IDLE. Подключение восстанавливается с экспоненциальной
паузой до 300 секунд и небольшим случайным разбросом. IDLE обновляется через 25 минут;
двухсекундное ожидание читает сокет, не опрашивает почтовый ящик.

При первом запуске старые письма пропускаются. После переподключения проверяются UID
после сохранённого курсора. Смена UIDVALIDITY сбрасывает курсор на текущую границу.
Письма старше десяти минут, крупнее 1 МиБ, вложения и неоднозначные коды пропускаются.
Обрабатывается только Steam: точный From `noreply@steampowered.com` и отдельная строка
из пяти заглавных букв/цифр. Epic и другие провайдеры требуют отдельных парсеров.

From сам по себе не доказывает подлинность отправителя; доверие к фильтрации
поддельной почты остаётся на почтовом сервере. Модуль не проверяет DKIM самостоятельно.
Событие предназначено внутренним обработчикам, коды не публикуются в WebSocket.
Курсор сохраняется после успешного обработчика: при падении процесса в этот момент
возможен повтор события. Получатели должны устранять дубли по mailbox_id, uidvalidity, uid.
Пароли и коды не журналируются; IMAP debug logging включать не следует.

Это программный модуль с явным запуском, без формы подключения почты в интерфейсе.
