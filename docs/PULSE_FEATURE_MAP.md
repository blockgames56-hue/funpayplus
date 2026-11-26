# Pulse: функциональная карта

Референс — публичная документация, без переноса закрытого кода:

- https://www.funpaypulse.com/docs
- https://www.funpaypulse.com/docs/plugins/autoresponder

| Раздел | Поведение / данные | FunPayAPI | Наша реализация | Ограничение / тест / статус |
|---|---|---|---|---|
| Аккаунты | golden_key, профиль, выбранный продавец | Account.get | accounts + gateway | Mock only, реальный вход не разрешён |
| Входящие | чаты, история, новые сообщения | get_chats/get_chat_history/Runner | существующий Inbox + native adapter | Не все даты есть в Message; HTML parsing, иначе unknown |
| Автоответы | команды, фразы, приветствие, account scope | NEW_MESSAGE + send_message | reply_engine | Core offline tests; расширенные условия ещё не реализованы |
| Заказы | статус, покупатель, сумма | get_sells/get_order | прежний sync_orders + Runner trigger | Детали пока legacy protocol |
| Статистика | агрегация заказов | локальная БД | existing financials | Требует сверки истории и валют |
| Автоподнятие | группа лотов, интервалы | raise_lots | existing scheduler | API adapter migration и dry-run не завершены |
| Автовыдача | правила, inventory, защита дублей | send_message | existing core delivery | Commercial providers отключены; inventory audit не завершён |
| Worker/VPS | отдельный сервис | не функция библиотеки | прежний deploy сохранён | Deployment не выполнялся |
| Коммерческие интеграции | Steam/Roblox/TON/поставщики | не универсальная функция FunPayAPI | исключены из runtime schedules/navigation | По scope пользователя; настройки сохранены |

Распакованный Pulse не является рабочим исходным проектом. Все функции платного Pulse не заявляются как реализованные.
