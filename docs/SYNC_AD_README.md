# Сверка AD → A-Tracker

## Как работает

- **Источник AD:** файл `ad_export.json` рядом с exe (выгрузка через PowerShell с машины в домене).
- **Расписание:** каждый день в **01:00** запускается сверка (в том же процессе, что и бот).
- **Сопоставление:** 1) по табельному номеру (**sPersNo** = objectSid из AD); 2) при отсутствии — по почте; 3) при отсутствии — по ФИО.
- **Обновление в A-Tracker:** для найденных записей обновляются sFullName, sLoginName, sEmail, sPersNo (ID не меняется — техника не теряется).
- **Создание:** если сотрудник из AD не найден в A-Tracker — создаётся новый (только если почта @asg.ru). Нужен сервис 7 в A-Tracker (см. `integrations/atracker/services/ATRACKER_SERVICE_7_EmployeeAdd.cs`).

## Выгрузка AD (PowerShell)

Скрипт `scripts/export_ad_to_json.ps1`. Запускать на ПК в домене:

```powershell
cd scripts
.\export_ad_to_json.ps1
```

Скопируйте полученный `ad_export.json` в папку с **tg_scan.exe** (рядом с config.py).

## Конфиг

В `config.py`: **AD_EXPORT_PATH = "ad_export.json"** (путь относительно папки exe).

## Один exe

- **tg_scan.exe** (без аргументов) — бот + планировщик (01:00 сверка, 07:00 реестр).
- **tg_scan.exe --registry** — только проверка реестра, затем выход.
