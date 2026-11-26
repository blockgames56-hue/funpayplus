# Cardinal → FunPay Plus

Источник: локальный `../FunPayCardinal-main`; прочитаны структура, участки cardinal.py, handlers.py, сигнатуры встроенной FunPayAPI. Локальная библиотека Cardinal отличается от PyPI 1.1.0: контракты проверяются по установленному пакету. Код Cardinal не копировался.

| Функция | Референс | Наш модуль | Статус / тест |
|---|---|---|---|
| Account / Runner | cardinal.py init/run, FunPayAPI/updater/runner.py | funpay_gateway.py / workers.py | Реализован адаптер; офлайн проверки, не live |
| Event routing | cardinal.py: event → handler list | event_pipeline.py | Durable dispatch, core tests |
| Команды | handlers.py response handler, AR_CFG | reply_config / reply_engine | Exact, aliases, args, priority; tests |
| Шаблоны | Utils/cardinal_tools.py format_msg_text | message_templates / reply_engine.context | whitelist; existing validation tests |
| Blacklist | cardinal.blacklist + handlers | BlacklistEntry / event_pipeline | Глобальный user_id; scope расширение не завершено |
| Заказы / выдача | handlers.py new order / delivery | workers.sync_orders / services.automation | Старое ядро сохранено, API migration частична |
| Восстановление / деактивация | handlers.py / cardinal lot routines | lot_editor / services | Нужна отдельная проверка контрактов и dry-run |
| Конфигурация | Utils/config_loader.py / configs | Settings / reply_config | SQLite — источник правил; legacy settings namespace сохранён |
| Retry / ошибки | Runner.listen(ignore_exceptions) | controlled get_updates + parse_updates | Ошибки не скрываются; backoff в worker |
| Telegram / плагины | tg_bot / plugin loader | не переносились | Не нужны для desktop ядра |

Полный аудит каждой функции Cardinal, лицензий всех транзитивных библиотек и всех storage paths ещё не завершён. Эта карта фиксирует исследованные связи, а не обещание полного переноса.
