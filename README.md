# Telegram-инвентаризация с A-Tracker

Проект содержит заготовку телеграм-бота на `aiogram`, который:

1. Просит у пользователя ФИО.
2. Через API A-Tracker получает активы, закреплённые за этим ФИО.
3. Показывает список активов и предлагает начать инвентаризацию.
4. В режиме инвентаризации принимает текст из QR-кода и, если он соответствует активу,
   вызывает сервис A-Tracker для отметки инвентаризации.

## Быстрый старт

1. Установите зависимости:

   ```bash
   pip install -r requirements.txt
   ```

2. В файле `config.py` пропишите:

   - `ATRACKER_BASE_URL`, `ATRACKER_USERNAME`, `ATRACKER_PASSWORD`;
   - реальные ID сервисов A-Tracker: `ATRACKER_ASSETS_SERVICE_ID`, `ATRACKER_MARK_SERVICE_ID`;
   - `TELEGRAM_BOT_TOKEN` — токен вашего бота.

3. Запустите бота:

   ```bash
   python bot.py
   ```

4. Дальше можно дорабатывать логику и формат QR-кодов под реальные требования.

## Проверка реестра увольнений/перемещений

Скрипт `run_registry_check.py` читает Excel-реестр (колонки: Дата, Сотрудник, Документ, Должность, Примечание), по каждому сотруднику проверяет в A-Tracker наличие техники и при наличии шлёт оповещение в группу.

- **Куда класть файл:** папка `data/` в проекте или любой путь в `config.py` → `REGISTRY_FILE_PATH`.
- **Запуск:** `python run_registry_check.py` (или с путём к файлу аргументом).
- **Exe для Windows (без Python на сервере):** на Windows запустите `build_all.bat` — соберутся **invent_bot.exe** (бот) и **registry_check.exe** (реестр). На сервер копируете exe + config.py + data/. Подробно: [BUILD_EXE.md](BUILD_EXE.md).
- Подробности по реестру: см. [REGISTRY_README.md](REGISTRY_README.md).