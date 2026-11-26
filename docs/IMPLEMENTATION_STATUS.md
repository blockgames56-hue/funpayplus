# Статус реконструкции

Дата: 20 ноября 2026. Завершён первый вертикальный срез ядра; расширенная реконструкция продолжается. Полная Definition of Done master prompt НЕ выполнена.

## Архитектура и сохранение интерфейса

Сохранены React/TypeScript/Vite, CSS и Tauri shell. Backend уже был Python: он модернизирован, второй сервис не создавался. Core путь:

`FunPayAPI Account/Runner → serialized executor → accounts.ingest → SQLite IncomingEvent → event_pipeline → reply_engine → message_service → accounts.send → authenticated runner POST and confirmed response`.

Очередь ответов `ReplyEvent` сохранена. `MessageSend` фиксирует попытку до сети; неоднозначный исход остаётся UNKNOWN. Дедупликация входящих основана на account/chat/server message ID, не на тексте.

## Реализовано и проверяется офлайн

- Backup исходников/настроек/ключей, согласованные снимки обеих SQLite; дерево трёх проектов, Git status.
- FunPayAPI==1.1.0: профиль, чаты, история, текст/изображения, poll через get_updates/parse_updates. Сигнатуры сверены с установленным пакетом.
- Один serialized ThreadPoolExecutor на Account; сеть не блокирует async event loop. Runner имеет request timeout, задержку 4 секунды, backoff+jitter; статусы и last_poll показываются в диагностике.
- IncomingEvent в одной транзакции с ChatMessage. Recovery PROCESSING и явный retry FAILED. История и собственные сообщения сохраняются без ответов.
- Команды: префикс в trigger, exact, aliases, args, регистр, приоритет, cooldown. Общий core matcher; legacy respond перенаправлен на тот же engine.
- Автоответы: существующие приветствия/ключевые слова/scope/blacklist, dry-run без отправки. Настройки переживают повторное открытие БД.
- Ручная отправка: UI guard от двойного клика, idempotency key, сохранение текста при ошибке, подтверждённый SENT. UNKNOWN требует сверки.
- Диагностика API/UI, состояния очереди и повтор FAILED. API miss на Windows исправлен: не возвращает HTML/200.
- Коммерческие plugin routes, навигация и фоновые jobs отключены. Core управление автоответами вынесено в `/workspaces/automation/...`.
- Rate limiter перенесён в `message_service.py`; legacy plugins оставляют compatibility import, не вторую реализацию.
- Windows DPAPI для master key: round-trip, проверка записанного blob, fail-closed при повреждении. Проверено на synthetic data и копии реальных ciphertext из backup без раскрытия секретов. При импорте конфигурации в P1 regression автоматически мигрирован ключ `storage/encryption.key` в DPAPI; после этого отдельно проверено полное совпадение расшифрованного ключа с backup (без вывода значения). Ключ не ротировался, рабочая БД не перешифровывалась. Ключ `backend/storage` остаётся исходным. Backup содержит старую plaintext-копию для восстановления и должен оставаться приватным.

## Миграция / файлы

Добавлены: `event_pipeline.py`, `message_service.py`, `funpay_gateway.py`, `diagnostics.py`, `os_secrets.py`, migration `0015_event_pipeline.py`, `automation-diagnostics.tsx`, новые core/gateway/DPAPI tests, build/smoke scripts и этот набор docs.

Изменены: services/workers/reply_engine/reply_config/models/main/schemas/config/account_workspace, связанные frontend types/pages/navigation и launcher dependencies. Изменения нужны для единого dispatch, подтверждённой отправки, отключения plugin runtime и защиты секретов. Удалены активные маршруты и jobs; исходные файлы коммерческих интеграций физически сохранены. Cardinal/Pulse не менялись.

Миграция 0015 проверена на пустой базе и копии пользовательской базы: revision=0015, integrity_check=ok, counts accounts/chats/chat_messages/settings/reply_events сохранены. Пользовательские рабочие БД вручную не мигрировались.

## Запуск

`start.cmd` использует `deploy/start.ps1` и изолированное `.venv-core`. Старое `.venv` сохранено. Не запускайте два backend на одной БД.

Для воспроизводимого Windows-окружения: `python -m venv .venv-core`, затем `.venv-core\Scripts\python -m pip install -r backend/requirements-core.lock`.

Отдельная сборка: `npm run build`, затем `.venv-core\Scripts\python deploy/build-core.py`. Каталог `artifacts/FunPayPlus-Core` переносится целиком. Это standalone backend с сохранённым web UI, а НЕ Tauri installer. Исполняемый файл принимает `--host 127.0.0.1 --port 8765`; UI доступен по localhost. Для тестов задавайте отдельный STORAGE_DIR и WORKERS_ENABLED=false.

## Проверки

Фактические логи в `.verification`:

| Проверка | Команда / результат |
|---|---|
| Baseline | unittest discover: 285 tests, 1 failure, 14 errors в прежнем проекте; НЕ passed |
| Первичный core E2E | test_core_pipeline.py: 11 passed |
| API отдельным процессом | test_api.py: 9 passed после исправления Windows SPA fallback |
| Windows DPAPI + legacy limiter compatibility | test_os_secrets.py + test_automation_plugins.py: 8 passed |
| Timestamp regression | test_chat_dates.py: 8 passed после устранения лишнего dispatch на дубликате |
| Clean dependencies | `.venv-core ... pip check`: No broken requirements found |
| Frontend | npm run build: passed; tsc + Vite |
| Полный выбранный core regression P0 | 126 passed; `.verification/final-core-tests.log` |
| P1 adapter | 16 passed: заказы, review guards/confirmation, raise category/cooldown/dry-run; `.verification/p1-gateway-tests.log` |
| P1 API | 11 passed: в том числе dry-run без изменения расписания, запрет dry-run для неподдерживаемых мутаций, delivery idempotency key; `.verification/p1-api-tests.log` |
| P1 расширенная регрессия | 167 passed за 163.25 s: core/gateway/replies/protocol/validation/dates/DPAPI/limiter/lots/editor/launcher; `.verification/p1-regression.log` |
| P1 сборка и запуск | TypeScript/Vite passed; standalone rebuilt, packaged smoke passed; `.verification/p1-frontend-build.log`, `p1-build-core.log`, `p1-packaged-smoke.log` |
| Standalone сборка | PASSED: исполняемый файл, миграции, authenticated health, web UI, диагностика и 404 для удалённого plugin catalog; `.verification/packaged-smoke.log` |

Общий mixed-process pytest первоначально дал 401 из-за того, что старый test_api меняет env после импорта app.config. API suite запускается отдельным процессом: это устраняет загрязнение конфигурации, не ослабляя авторизацию.

## Не завершено / ограничения

1. Реальный аккаунт проверен чтением и одной разрешённой тестовой отправкой. Полный live DoD автоматизации, выдачи, отзывов и изменения лотов не подтверждён.
2. P1 orders/details/review replies/raise/чтение и редактирование существующих лотов перенесены на FunPayAPI. Создание, копирование, удаление лотов и расчёт комиссии остаются в legacy adapters. История заказов ограничена тремя страницами за синхронизацию. Относительные даты и переход года исправлены и проверены с фиксированным временем FunPay.
3. Inventory reservation ledger, глобальная проверка дубликатов между пулами/резервами и ручная сверка uncertain реализованы (0017, один процесс). Global/per-user cooldowns, advanced rule conditions/schedules, scoped blacklist и расширенная статистика не завершены. Распределённое резервирование не поддерживается.
4. Остались shared imports и архивные plugin файлы; полное физическое удаление зависимостей потребует дальнейшего отделения. TON dependency удалена из core requirements из-за несовместимого requests; коммерческие интеграции не запускаются.
5. Выполнен браузерный smoke на отдельной тестовой БД: вход, создание !hello с alias, сохранение, dry-run и сохранность после reload. Снимок UI_CORE_VERIFICATION.jpg. Полный обход всех вкладок и pixel comparison не выполнены.
6. Rust 1.100.0 установлен в локальном `.toolchains` без изменения системного PATH; MSVC Build Tools обнаружены. Tauri desktop MSI и NSIS EXE собраны. Приложение запущено на отдельном пустом хранилище: окно отобразило dashboard и подключилось к Python backend. Установку через MSI/EXE на пользовательскую систему не выполняли; проверен запуск release exe.
7. Native Message не гарантирует дату: используем проверяемый HTML timestamp, иначе unknown. Удалённая пагинация истории, durable Runner cursors и расширенная классификация ошибок требуют продолжения.
8. API ошибки частично сохраняют старый detail-format; единый correlation_id contract не завершён. UNKNOWN консервативно используется для неуточнённых сетевых ошибок.
9. DPAPI предназначен для текущей Windows учётной записи. Перенос защищённого хранилища на другой компьютер/пользователя требует отдельного экспортного процесса; нельзя просто заменить ключ новым.

Документы аудита: PROJECT_AUDIT.md, CARDINAL_FEATURE_MAP.md, PULSE_FEATURE_MAP.md, BACKEND_API.md, MIGRATION_PLAN.md. Оставшиеся направления: расширенные правила/scopes/history, полный UI E2E, проверка desktop installer, согласованный live test.

## Второй срез P1

Синхронизация заказов, проверка деталей, ответы продавца на отзывы и адресное поднятие подкатегории выполняются через FunPayAPI. Существующие ответы не заменяются. Публикация сверяется чтением результата, неподтверждённый результат считается UNKNOWN. Ошибки API не приводят к автоматическому повтору публикации отзыва. Полный cooldown FunPay сохраняется без искусственного сокращения до суток.

Кнопка «Проверить поднятие» вызывает dry-run: проверяет принадлежность подкатегории, не вызывает удалённое поднятие, не меняет last_raised_at/next_raise_at и сохраняет выделение. Проверка категории не обещает, что будущий POST будет принят: актуальный серверный cooldown узнаётся при реальной операции. Ключ исходящей автовыдачи теперь стабилен для аккаунта и заказа; существующая защита sending/uncertain остаётся.

Все проверки выполняются с подставными ответами FunPay и временными БД. Ни одной реальной публикации/выдачи/операции с лотом в этом этапе не выполнялось. Новая кнопка проверена сборкой TypeScript/Vite и API integration; отдельный браузерный E2E для неё ещё не выполнен.

## Последние два пункта перед остановкой

- Native чтение обычных лотов: ID и категории FunPayAPI, цена Decimal, состояние виджета; остаток без числового подтверждения хранится NULL и показывается как «Неизвестно». Миграция сохраняет прежние известные остатки.
- Журнал выдачи: один резерв на заказ, товарный ключ зашифрован; резерв, списание пула и sending атомарны. Ошибка/перезапуск оставляет uncertain без повторной отправки и повторного использования ключа. Диагностика показывает состояния без секретов. Старые выдачи не backfill-ятся вымышленными резервами.
- Новые проверки: 17 gateway tests и 13 API tests passed (конкуренция, отмена/перезапуск, откат при ошибке шаблона, отсутствие ключей в диагностике). Миграция 0016 проверена на пустой БД и копии backup; существующие row counts и integrity сохранены. TypeScript/Vite passed. Логи: `.verification/evening-*.log`.
- Рабочая БД вручную до 0016 не обновлялась. Это выполняет launcher при следующем запуске. Отдельный браузерный E2E нового журнала ещё не выполнялся.
- Standalone core пересобран; smoke исполняемого файла подтвердил схему 0016, пустой журнал выдачи, authenticated health, UI и диагностику (`evening-build.log`, `evening-smoke.log`). Это по-прежнему core + web UI, не desktop installer.
- Финальная регрессия текущих изменений: 83 passed за 320.27 s (`evening-regression.log`), включая core pipeline, gateway, reply workspace, lot bulk и offer editor; отдельный API-прогон — 13 passed. На этом работа остановлена по просьбе владельца до следующего захода.

## Продолжение 21 ноября 2026

- Native save существующих лотов сохраняет unchecked active и поля категории без непрошенной нормализации SDK, затем проверяет сохранённые значения. В редакторе добавлена проверка изменений без POST.
- Ручная сверка выдачи реализована в API и UI; оба сценария проверены в браузере на отдельной БД с отключённым тестовым аккаунтом и WORKERS_ENABLED=false. Результаты пережили reload; снимок UI_DELIVERY_RECONCILIATION.jpg. Отправки в FunPay не выполнялись.
- Добавлены межпуловая проверка ключей, зашифрованная заметка сверки (0017), исправление относительных дат заказов на границе суток/года.
- Проверки: 96 regression tests passed, 19 API tests passed отдельным процессом, TypeScript/Vite passed, ruff passed. Миграция 0017 прошла на пустой БД и копии backup с сохранением row counts и integrity. Логи `.verification/nov21-*.log`.
- Пересобраны standalone core и onefile sidecar; оба прошли packaged smoke (миграции, API auth, web UI, диагностика). В smoke исправлена остановка дерева onefile-процессов. Для desktop включены process group/job object и kill-on-drop; версия tauri-plugin-shell закреплена минимум 2.6 с Cargo.lock.
- Новая команда сборки Windows: `powershell -File deploy/build-desktop.ps1`; использует `.venv-core`, локальный Rust если доступен, тестирует sidecar и копирует установщики в `artifacts/desktop`.
- Собраны `artifacts/desktop/FunPay Plus_0.1.0_x64_en-US.msi` и `FunPay Plus_0.1.0_x64-setup.exe` (~47 и ~46 MiB). Release `funpay-plus.exe` открыт в изолированном режиме и показал подключённый dashboard без FunPay аккаунта. Сборка повторена после изменения process group; оба пакета созданы без ошибок (`nov21-desktop-final.log`). SHA256: MSI `80C9399E21115E5305696D64E5B2A67977560CFA37578B5FDB94407A907414D6`, EXE `00C6293276CBD9D8CC0CEC764D02672C887A48AFA6BFAD16C69CE5AC66156D1C`. Сам установщик на рабочем компьютере не запускался.

## Проверка аккаунта 21 ноября 2026

Владелец предоставил golden_key для продолжения. Значение ключа в документации и логах не сохраняется. В рабочей SQLite уже была одна запись этого аккаунта с совпадающим зашифрованным ключом; новая запись не добавлялась, рабочая БД и переключатели автоматизации не менялись. Живой read-only probe подтвердил профиль, 50 чатов, первую страницу из 100 заказов, 374 обычных лота, один цикл Runner и загрузку 100 сообщений одного чата. Содержимое чатов/заказов в логах не выводилось.

На реальном ответе выявлена несовместимость FunPayAPI 1.1.0: FunPay требует дополнительную cookie `golden_seal` для `/runner/`, а SDK её не переносил. Узкий подкласс Account переносит cookie с домашней страницы в последующие запросы. Второй сбой: SDK ожидает `.message-text` у каждого сообщения, текущая разметка может использовать другой блок. История чата, пакетная история Runner для новых событий и ответ отправки теперь разбираются проверенным локальным парсером через аутентифицированный транспорт SDK. После первых исправлений чаты, Runner и история прошли read-only проверку; пакетный путь Runner проверен синтетическим ответом.

После сборки пакетная история Runner дополнительно проверена чтением реального тестового чата: 50 сообщений, у всех есть ID, собственное сообщение присутствует. Записи на FunPay при этой проверке не выполнялись.

По отдельному указанию владельца отправлено одно безвредное тестовое сообщение в предоставленный чат. Ответ SDK упал при разборе новой разметки уже после POST, поэтому повторной отправки не было. История FunPay подтвердила ровно одну доставку и remote ID; в изолированной тестовой БД журнал отправки вручную сверен с этим ID. Рабочая БД не менялась. Адаптер ответа исправлен: он подтверждает ID, автора и точное содержимое, а при несовпадении оставляет исход UNKNOWN без автоматического повтора. Других реальных записей в FunPay не выполнялось. Обновлённые сборки готовятся отдельно; предыдущие установщики следует заменить.

После правки ответа прошли 24 теста gateway и 37 тестов gateway/core вместе. После исправления пакетной истории прошли 26 тестов gateway, 38 тестов gateway/core вместе и `ruff check`. Вторая отправка для проверки правки не выполнялась. Финальные standalone core и onefile sidecar пересобраны и прошли packaged smoke на отдельных хранилищах.

Повторная Tauri-сборка завершилась с финальным sidecar. MSI и NSIS EXE скопированы в `artifacts/desktop`; SHA256 совпадают с bundle-оригиналами: MSI `9F9B9C1F1745568C555DF1BCA02149ADF026FF756D2FCB8B8308F4A9C80438E1`, EXE `EA8F366EFD6F87F3E4FC4C932B91D2845BAD1CF0AD2640A8F7D48097CB61F7C1`. Старые контрольные суммы выше относятся к предыдущей сборке. Сам установщик в пользовательской системе не запускался.

## Документация DPAPI

Использован Windows current-user CryptProtectData/UnprotectData с UI_FORBIDDEN, без LOCAL_MACHINE. Контракт: https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata и https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptunprotectdata.


## Durable Runner cursor — 21 ноября 2026

Исследован установленный FunPayAPI 1.1.0: `Runner` хранит `last_messages_ids` только в памяти; при старте `get_chats_histories` ограничен 50 сообщениями на чат (25 для отдельных категорий), а `NEW_MESSAGE` возникает лишь после изменения краткого текста/времени в списке и скачивания истории. Значит, один Runner не является durable cursor и может пропускать одинаковые последние тексты или события между перезапусками.

Добавлена миграция 0018: per-chat `last_message_cursor`, `runner_baselined`, `runner_checked_at`; имеющиеся локальные сообщения используются как watermark, новые чаты начинают с baseline. Gateway сохраняет Runner-confirmed message IDs, загружает историю изменившихся чатов пакетами по 10 и ограниченно сверяет до 10 давно не проверявшихся чатов за цикл. События Runner, которых ещё нет в UI-истории, продвигаются только по подтверждённым ID. Периодическая сверка может обработать лишь ID выше watermark после baseline; старые сообщения, same-ID content edits и сообщения вне доступного SDK окна не генерируют ответ. Первичный/исторический baseline не отвечает.

Статус проверки (финал 21 ноября): только offline/mock на временной SQLite; не live. Целевой набор после всех правок: 93 passed (gateway, core pipeline, protocol, chat dates); Ruff и `py_compile` прошли. Миграция 0018 проверена `deploy/verify-migrations.py` на пустой БД и копии backup (revision 0018, integrity ok, row counts сохранены) и отдельным seed-скриптом legacy-обновления (watermark 0018, `foreign_key_check` пуст). Логи: `.verification/nov21-durable-cursor-*.log`. Периодическая сверка может стать кандидатом на automation только для ID выше watermark при подтверждённом baseline; повторная обработка уже известного сообщения переоткрывает его IGNORED-журнал вместо дубля. Live NEW_MESSAGE pipeline, долговечность cursor после реального рестарта и доставка единственного ответа не подтверждены. Live POST не выполнялся. Exactly-once при сети не обещается; исходящий UNKNOWN остаётся без автоматического повтора.

## Пересборка артефактов после durable cursor — 21 ноября 2026

`deploy/smoke-core.py` приведён к схеме 0018 (ранее ожидал 0017 и сорвал бы smoke новой сборки). Пересобраны standalone core, onefile sidecar и оба desktop-установщика на коде durable cursor; sidecar и standalone прошли packaged smoke на пустых изолированных хранилищах (revision 0018, authenticated health, web UI, диагностика). Логи: `nov21-durable-cursor-build-core.log`, `-smoke-core.log`, `-desktop-build.log`.

Актуальные SHA256 (заменяют суммы предыдущей сборки выше):

- MSI `FunPay Plus_0.1.0_x64_en-US.msi`: `A09A7A18EC87C0B7E16F587C0E7B8BF233411A5FCA98F39BB7C32CFE97376722`
- NSIS `FunPay Plus_0.1.0_x64-setup.exe`: `56F7E63E4EC32572E908163E5A3E217FA1D70A5BB24A023AA349596A8E65B6B1`
- Standalone `artifacts/FunPayPlus-Core/FunPayPlus-Core.exe`: `96F14F6C51D103D40EEBC47D39363D819E6DE87401E6BF9EC5BE1D91E5B0C8DA`
- Sidecar `apps/desktop/src-tauri/binaries/pulse-backend-x86_64-pc-windows-msvc.exe`: `2340FE94CC225B953CBA08D977FF7C584CF08D2F2C12166F2BCB88E36C1B1B89`

Установщики на рабочей системе по-прежнему не запускались; live NEW_MESSAGE/рестарт/единственный ответ остаются непроверенными до отдельного согласования владельца.

## Единый контракт ошибок API — 21 ноября 2026

Реализован P0-пункт мастер-промпта: все ошибки API теперь `{code, message, details?, correlation_id}` + legacy `detail`, чтобы существующие вызовы фронтенда не ломались. Новые модули/правки: `app/error_contract.py` (типизированная `ApiError`, маппинг кодов, безопасные тексты), middleware correlation-ID (`X-Correlation-ID` на всех ответах, уникальный на запрос), хендлеры в `main.py` заменены на контрактные: StarletteHTTPException → коды по статусу, RequestValidationError → `validation_failed` со списком loc/msg/type без input/context, FunPayError → 502 (`funpay_auth_expired`, `funpay_challenge_required`, `remote_unconfirmed`, `remote_error`; не 401, чтобы истечение сессии FunPay не разлогинивало приложение), InventoryConflict/IntegrityError → 409 `conflict`, необработанные исключения → 500 `internal` с безопасным сообщением; в лог пишутся только имя класса исключения и correlation_id, тексты исключений с секретами не выводятся в ответ.

Фронтенд `apps/web/src/api.ts` читает новый контракт (`message`, `details`), затем legacy `detail`; correlation_id добавляется в текст ошибки (`... · ID <id>`), чтобы оператор мог привести его из логов. TypeScript/Vite сборка прошла.

Проверки: `tests/test_error_contract.py` — 9 passed (коды по статусу, 401, validation legacy-список, маппинг FunPay-ошибок, типизированная ApiError, 500 без утечки текста исключения, заголовок на успехах и уникальность ID); регрессия 102 теста (gateway/core/protocol/chat dates/контракт); 19 существующих API-тестов; HTTP-проверка на живом изолированном сервере (`404 · ID`, `401`, `422 · поле: причина`, `500 internal`) и повторение логики парсинга `api.ts` против реальных ответов. Ограничение: браузерный E2E-рендер тоста ошибки в этой сессии не выполнялся — preview-автоматизация стабильно недоступна; контракт и фронтенд-путь проверены на уровне HTTP + точной реплики парсера. Логи: `.verification/nov21-error-contract-*.log`.

Артефакты пересобраны на коде контракта; sidecar и standalone прошли packaged smoke (schema 0018). Актуальные SHA256 (заменяют суммы durable-cursor-сборки):

- MSI `FunPay Plus_0.1.0_x64_en-US.msi`: `145AB8BE66830A8E7A759D286AD2683BCA79A9E4A4DA7008E40F3D5501BEF45E`
- NSIS `FunPay Plus_0.1.0_x64-setup.exe`: `FD87677E84929B1588766A888AA1D5449342080028F945E8FF3FFA6B3F1D1C3A`
- Standalone `artifacts/FunPayPlus-Core/FunPayPlus-Core.exe`: `50E6D97ED958E3DE74DE35308FF698271412E91445585A6CC9766BAE7AC1FD90`
- Sidecar `apps/desktop/src-tauri/binaries/pulse-backend-x86_64-pc-windows-msvc.exe`: `829B8FA980C0094BAA0AA33D2472322F25DC04A04B968453EA03827873DDDA45`

## Cooldown правил между чатами — 21 ноября 2026

К каждому правилу команд и ключевых фраз добавлены два необязательных ограничения от 0 до 86400 секунд: для аккаунта и для покупателя. Старый cooldown на один чат сохранён. Аккаунтное ограничение блокирует то же правило в других чатах выбранного аккаунта; покупательское блокирует повтор для того же FunPay ID в другом диалоге этого аккаунта, но не для другого покупателя. Ограничения опираются на durable ReplyEvent, поэтому отменённая после постановки очередь тоже не превращает входящее обратно в немедленный повтор. Настройки хранятся в существующем JSON responder:v2 и совместимы со старыми правилами: отсутствующие поля означают 0. В UI правила появились оба поля. Проверки: 52 passed (test_reply_workspace.py + test_core_pipeline.py), Ruff и TypeScript typecheck passed. Реальных отправок и изменений рабочего аккаунта не было.

Desktop-артефакты пересобраны на этом коде (`deploy/build-desktop.ps1` завершился успешно; Vite + Rust/Tauri + MSI/NSIS). Новые контрольные суммы:

- MSI `FunPay Plus_0.1.0_x64_en-US.msi`: `C385FF647A34627CB897028C43C76AF3A31D8D24B0E993DA74D57B709717993C`
- NSIS `FunPay Plus_0.1.0_x64-setup.exe`: `8157C48C94B2AABD507ADA1582CBFD1F46DE9D533D19AE0152D650A60DC6C065`
- Standalone `artifacts/FunPayPlus-Core/FunPayPlus-Core.exe`: `50E6D97ED958E3DE74DE35308FF698271412E91445585A6CC9766BAE7AC1FD90`
- Sidecar `apps/desktop/src-tauri/binaries/pulse-backend-x86_64-pc-windows-msvc.exe`: `346909D858153392E7853937C211AB09645757995CEB3C4C8F2EB312C20A0370`

## Расписание правил автоответчика — 21 ноября 2026

У каждого правила появились необязательные дни недели и период работы `HH:MM–HH:MM` в часовом поясе приложения. Старые JSON-правила остаются активными все дни и всё время. Поддержан ночной интервал через полночь: выбор понедельника и `22:00–06:00` действует до 06:00 вторника. Неполная пара времени, дубли дней и дни вне диапазона 0–6 отклоняются при сохранении. Поля доступны в редакторе команд и ключевых фраз. Это локальная фильтрация до постановки ReplyEvent; реальных отправок не было.

## P0: единая offline-цепочка нового входящего — 21 ноября 2026

Добавлен сквозной acceptance-тест на фактической форме `FunPayGateway.poll()`: mock Runner `NEW_MESSAGE` и mock истории проходят через native gateway, `accounts.ingest`, durable `IncomingEvent`/`ChatMessage`, dispatcher и правило команды к одной подтверждённой mock-отправке. Повтор того же Runner payload с сохранённым cursor не создаёт второй `IncomingEvent`, `ReplyEvent` или send. Отдельно уже проверяются initial/history, own message, missing identity, blacklist, restart до dispatch, crash после commit, `UNKNOWN` после сетевой ошибки и явный retry только для `FAILED`. Проверка этого среза: `tests/test_core_pipeline.py tests/test_funpay_gateway.py` — 42 passed, Ruff passed. Это не заменяет live-проверку: настоящее новое входящее и автоматический ответ не запускались без отдельного разрешения владельца.

## Scoped blacklist core — 21 ноября 2026

Migration 0019 заменяет единственный global blacklist на совместимую область `global` / `account` / `chat`: существующие записи переносятся как global, а downgrade сохраняет только global, не расширяя scoped-блокировку. Проверка объединяет global, текущий account и текущий chat во всех активных core-путях: ingestion, reply engine, ручная/автоматическая отправка и выдача. Поэтому блокировка покупателя в одном аккаунте или чате не влияет на другой аккаунт. UI умеет создать chat-блокировку из Inbox, выбрать global/account в форме и показать область в таблице. Проверки: 60 passed для scope/core/reply, Ruff и Vite build прошли; migration 0019 успешно проверена на пустой SQLite и копии backup. Реальных POST, workers и изменений рабочего аккаунта не было.

## Изоляция выбранных аккаунтов в автоответчике — 21 ноября 2026

Сохранение `account_scope:"selected"` теперь отклоняет пустой список, нулевые и повторяющиеся ID на уровне модели, а также ID несуществующих аккаунтов при записи в БД. Это предотвращает ложное впечатление, что автоответчик действует для удалённого или ошибочно выбранного аккаунта. Удаление аккаунта продолжает очищать его per-account настройки и убирает его ID из selected scope. Проверка `tests/test_reply_workspace.py`: 41 passed; Ruff прошёл. Это offline-проверка на изолированной SQLite, без workers и живых отправок.

## Двухаккаунтные automation-проверки — 21 ноября 2026

Расширены offline сценарии двух аккаунтов с одинаковым FunPay buyer ID. Account-scoped blacklist блокирует автоответ и автовыдачу только в выбранном аккаунте; второй аккаунт ставит событие и отправляет ответ/выдачу только в свой чат. Selected responder scope также исключает отправку из невыбранного аккаунта. Ответ на отзыв выбранного аккаунта в chat-mode направляется только в его чат; публичная публикация не вызывается. Планировщик автоподнятия объединяет due-лоты одной категории в один запрос, не выбирает неактивный аккаунт и пропускает лот с будущим `next_raise_at`. Проверки: `test_reply_workspace.py` — 45 passed, `test_api.py` — 20 passed, Ruff прошёл. Все удалённые вызовы замоканы; реальное поднятие, выдача, автоответ и публикация ответа на отзыв не запускались.

## Desktop UI и упаковка — 21 ноября 2026

В отдельном `STORAGE_DIR` с `WORKERS_ENABLED=false` вручную проверены dashboard, автоответчик, настройки ответов на отзывы и blacklist. Новая desktop-сборка показывает scope selector blacklist (`Все аккаунты` / `Только выбранный аккаунт`); chat-scope корректно оставлен только кнопке из Inbox. Первая проверка выявила отставший release exe: исходники уже содержали scope, старый binary — нет. Desktop пересобран; packaged backend smoke прошёл. В `deploy/build-desktop.ps1` добавлен preflight: запущенный `pulse-backend` с тем же sidecar-путём теперь даёт понятную ошибку с PID до тяжёлой Tauri-сборки вместо позднего `PermissionDenied`. Тестовые окна и sidecar-процессы остановлены; реальный аккаунт, рабочая БД и FunPay POST не использовались.

## Review scope и актуальные desktop-артефакты — 21 ноября 2026

Исправлен обход `account_scope:"selected"` в review reply: сохранённая настройка конкретного аккаунта больше не может поставить ответ на отзыв из исключённого аккаунта. Тест теперь включает `review_reply=True` именно в исключённом account policy и подтверждает отсутствие события/отправки; выбранный аккаунт продолжает отправлять только в свой чат. Проверки: `test_reply_workspace.py` — 45 passed, `test_api.py` — 20 passed, Ruff/compileall прошли. Desktop пересобран после исправления; packaged smoke снова прошёл. SHA256: MSI `624F9F40987C6830A011A9187F81284713EF04C00EA53DBE8E8D12F1B7C60349`, NSIS `FA34996AA8EE0A909A43C9F040EFE3F6636C4514AD73657E6A90B22A8086BF57`, sidecar `AD1D8C7904379ECEDF1ABEED8A83821866544949FB3366FABEB936809BFF943A`. Реальные ответы на отзывы, поднятия, выдачи и автоответы не запускались.

## Диагностика остановленного автоответчика — 21 ноября 2026

На рабочем AppData-хранилище read-only проверка показала: большинство `IGNORED` — собственные сообщения или initial/history baseline, а не ошибка доставки; единственное новое событие было обработано при выключенном master responder. Pipeline теперь фиксирует такое событие как `IGNORED / responder_disabled` и не ставит запоздалый ответ после последующего включения. UI diagnostics показывает явное предупреждение «Автоответчик остановлен», test mode и до трёх последних безопасных причин пропуска; больше не создаётся впечатление, что `PROCESSED` означает отправку ответа. Проверки: 16 core pipeline tests, 10 error/diagnostics tests, Ruff и Vite build прошли; desktop packages и packaged smoke пересобраны. Рабочая БД и переключатели не менялись.

