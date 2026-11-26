# Аудит реконструкции — 20 ноября 2026

Статус: первичный аудит и реализация первого вертикального среза. Это не заключение о готовности всего приложения.

## Проекты и резервная копия

Рабочий корень: `C:\Users\User\Desktop\FunPay Plus\FunPay Plus`. React 19 / TypeScript / Vite; Tauri 2 shell; FastAPI / SQLAlchemy / Alembic / SQLite. Python backend уже существовал — второй backend не создавался.

Git: `git -C "FunPay Plus" status --short` вернул `not a git repository`. Изменения нельзя представить как Git diff без отдельной инициализации репозитория.

Backup: `C:\Users\User\Desktop\FunPay Plus\reconstruction-backup-20261120-221608`. Сохранены исходники, настройки, ключевые файлы и обе базы (`storage/pulse.db`, `backend/storage/pulse.db`). SQLite скопирован через backup API с проверкой `integrity_check=ok`, включая зафиксированные данные WAL. Исключены установленные зависимости, кэши, build artifacts и вложенные старые backups. В backup лежат деревья трёх проектов и результат Git status. Backup содержит секреты и не предназначен для публикации.

Cardinal и Pulse присутствуют. Референсные каталоги не изменялись. Закрытый код Pulse не переносился; использованы публичные описания функций.

## Карта текущего приложения

| Функция / UI | Файлы / вызов | Хранилище | Наблюдение и изменение | Проверка |
|---|---|---|---|---|
| Аккаунты | account-workspace.tsx → account_workspace.py / main.py → services.accounts | accounts, proxies; зашифрованный golden_key | Уже Python; профиль теперь через FunPayAPI.get; реальный вход не проверялся | test_api, gateway contract tests |
| Входящие | pages.tsx / inbox-workspace.tsx → /api/chats → accounts.ingest | chats / chat_messages | История раньше через собственный runner JSON; добавлен адаптер FunPayAPI и журнал в одной транзакции | test_core_pipeline, test_chat_dates |
| Ручная отправка | pages.tsx → POST /chats/{id}/messages → accounts.send | message_sends + chat_messages | Добавлены durable idempotency key, UNKNOWN до сетевого запроса, блокировка двойного клика | test_core_pipeline |
| Команды | responder-workspace.tsx → PUT /workspaces/responder → reply_engine | settings.responder:v2 / reply_events | Полный префикс в triggers не совпадал с очищенным текстом. contains принимал похожие команды. Исправлено | matcher + command/restart tests |
| Автоответчик | тот же workspace / reply_engine | settings / reply_events | Команды зависели от hooks коммерческих интеграций; core dispatcher теперь вызывает engine напрямую | keyword/cooldown/blacklist tests |
| Отзывы | account policy / reply_engine.observe_orders, flush | reply_events | Существующая реализация сохранена; переход send_review и проверка seller-reply контракта ещё не завершены | существующие mock tests, не live |
| Blacklist | pages.tsx → /blacklist | blacklist | Проверяется перед постановкой ответа и перед автоматической отправкой; пока глобальный user_id scope | core blacklist test |
| Заказы / статистика | financials + workers.sync_orders | orders | Runner сообщает об изменении; загрузка деталей пока legacy gateway. Нельзя считать миграцию завершённой | существующие тесты + будущий API contract test |
| Автовыдача | /auto-delivery → services.automation.deliver | rules / orders | Core оставлен, поставщики отключены; отдельный inventory ledger требует дальнейшей проверки | test_api и специализированные tests |
| Лоты / поднятие | lots-page.tsx / lot_editor / workers.auto_raise | lots | Пока legacy adapter. Массовые изменения не выполнялись | отдельные mock tests ещё требуется повторить |
| Плагины | plugin-pages / workers / main | plugin settings, jobs | Навигация, dedicated routes и фоновые jobs отключены. Файлы не удалены; часть imports/shared code ещё требует отделения | новый runtime smoke; old tests отдельно |
| Настройки / секреты | config.py, security.py, crypto.py | SQLite + encryption.key | AES-GCM есть, но ключ рядом с БД. DPAPI migration реализована и проверена на копии; штатное хранилище преобразуется при запуске | будущая migration/restore проверка |
| Запуск / сборка | launcher.py, deploy, Tauri | Alembic | Единственный ASGI process. Rust отсутствует; Tauri installer не готов | clean install + launcher smoke |

## Причины поломок / риски

1. Message commit происходил до вызова event listeners. Исключение после commit приводило к пропуску при следующем poll как duplicate. Теперь `incoming_events` коммитится с сообщением и имеет состояния RECEIVED/PROCESSING/PROCESSED/FAILED/IGNORED. FAILED повторяется явно, PROCESSING восстанавливается при запуске.
2. Команды шли через kosell/integrated_jobs/automation_plugins. Промежуточный handler мог прервать обработку. Теперь core pipeline вызывает reply_engine без этой цепочки.
3. Префикс удалялся из входного текста, но не из сохранённого trigger. Исправлена нормализация; совпадение команды учитывает границу токена.
4. `reply_engine` и legacy AutomationService содержали две реализации. Входящие теперь используют только reply_engine; legacy respond теперь делегирует тому же engine, compatibility command_reply использует общий matcher.
5. Нет общей durable idempotency для ручных сообщений. Добавлен журнал UNKNOWN/SENT и проверка совпадения payload по SHA-256.
6. Перезапуск не означает exactly-once. Неопределённые отправки не повторяются автоматически.
7. Первоначальный общий запуск unittest: 285 tests, 1 failure, 14 errors. Среди ошибок — KeyError('auto-smm') в старом каталоге интеграций. Этот результат НЕ считается успешной проверкой реконструкции; подробности в `.verification/reconstruction-baseline-tests.log`.

## UI inventory / ограничения аудита

Сохранены React-компоненты, CSS, темы, размеры shell, основные страницы: обзор, аккаунты, входящие, лоты, автовыдача, прокси, blacklist, финансы, автоответы, бонусы, настройки, логи. Убраны только ссылки на плагины. Добавлены диагностика и dry-run в существующем workspace. Скриншоты «до» не сняты; выполнен browser smoke создания/сохранения команды и dry-run после reload, снимок UI_CORE_VERIFICATION.jpg. Пиксельное сравнение и полный обход всех кнопок ещё не выполнены. Нельзя заявлять полную визуальную эквивалентность.
