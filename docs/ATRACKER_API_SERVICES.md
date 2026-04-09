# A-Tracker API Services (рабочие заметки)

В этом файле собраны известные кастомные сервисы A-Tracker, которые используются в проекте.

## ID1 `Asset_User` (GET)

- Назначение: получить активы пользователя по ФИО.
- Вход: `fio`.
- Выход: `ID`, `sFullName`, `sSerialNo`, **`sInventNumber`** (каноническое имя в `itamPortfolio`, см. `assets/reference/artibut_model`), при необходимости дублируйте старое `sInventoryNo` для совместимости, `lUserId.sFullName`, `bInventoried`, `dtInvent`.
- **Важно:** если в выборке сервиса нет поля **`sInventNumber`**, в вебе на странице «Мои активы» инвентарный номер будет «—», пока не подтянется карточка через сервис ID4 (дополнительный запрос на каждый актив). Надёжнее сразу отдавать **`sInventNumber`** из ID1.

## ID2 `InventAct` (GET)

- Назначение: отметить инвентаризацию по активу.
- Вход: `AssetId`, опционально `Fio`, `TelegramUserId`, `TelegramUsername`.
- Поведение: ставит `bInventoried=true`, `dtInvent=now`, дописывает строку в `sComment`.

## ID3 `Asset_AddFile` (POST)

- Назначение: прикрепить файл к карточке актива.
- Вход JSON: `AssetId`, `FileName`, `ContentBase64`, `ContentType`.
- Поведение: декодирует base64, сохраняет через `HelperMethods.SaveFile(..., "itamPortfolio", AssetId, 3, 7)`.

## ID4 `asset_find` (GET)

- Назначение: найти актив по `AssetId` и вернуть карточку `itamPortfolio` для веба/бота (`atracker_client.get_asset_info`).
- Вход: `AssetId` или `assetid`.
- Реализация в репозитории: **`integrations/atracker/services/ATRACKER_SERVICE_4_AssetFind.cs`** — скопируйте код в карточку сервиса с ID **4** (или тем, что указан в `config` как сервис карточки актива).
- Запрашиваемые поля (развёртка FK через точку, как в модели A-Tracker):  
  `sFullName`, серийный/инвентарный номера, `iQty`, `lUserId` + `lUserId.sFullName`, **`lt_lLocationId`** (+ `sFullName`/`ID`), **`lt_lCategoryId`** (+ `sFullName`/`ID`), при необходимости **`lModelId`** и цепочка **`lModelId.lCategoryId.sFullName` / `sName`** (если категория идёт через модель).
- Выход: **вся строка портфеля** (все запрошенные ключи) + **`OwnerFio`** (ФИО владельца, как раньше).  
  Сайт разбирает `lt_lCategoryId` / `lt_lLocationId` в Python (`_category_name_from_asset_raw`, `_location_name_from_asset_raw`) — для колонки «Категория» / «Тип» и местоположения.
- Если при сохранении сервиса в A-Tracker ошибка по «неизвестному полю» — удалите из списка `fields` проблемные пути (часто отличаются имена у `lModelId` / категории в вашей схеме).

## ID5 `ReturnEmpl` (GET)

- Назначение: выгрузка сотрудников из `itamEmplDept`.
- Выход: `ID`, `sFullName`, `sLoginName`, `sEmail`, `sPersNo`.

## Справочник местоположений (GET, кастомный)

- Назначение: подстановка **наименований** местоположений в форме «Передача техники» (живой поиск).
- Настройка: `config.ini` → `[atracker] locations_list_service_id` (аналогично `employees_list_service_id`). Если `0`, список строится по уникальным `lt_lLocationId` / `lLocationId` **только у выбранных для передачи активов** текущего пользователя.
- Разбор полей строки на стороне сайта: см. `front_site.app._parse_location_service_row`.

## ID6 `UpdateEmpl` (POST)

- Назначение: обновить карточку сотрудника.
- Вход: `ID` + поля `sFullName`, `sLoginName`, `sEmail`, `sPersNo` (из args или `__json`).
- Поведение: ищет по ID и выполняет `Update()`.

## ID7 `CreateUser` (POST)

- Назначение: создать нового сотрудника в `itamEmplDept`.
- Вход: `sFullName`, `sLoginName`, `sEmail`, `sPersNo` (из args или `__json`).
- Поведение: создаёт запись и возвращает новый `ID`.

## TransferPosting — утверждение перемещения (POST, кастомный)

- Назначение: одна операция «как мастер OneLineTransit2»: создать документ/операцию и зафиксировать перемещение активов в `itamPortfolio` (и связанных таблицах — по вашей схеме).
- Настройка: `config.ini` → `[atracker] transfer_posting_service_id`. Пока `0`, веб не вызывает сервис.
- Реализация в репозитории: `integrations/atracker/services/ATRACKER_SERVICE_TransferPosting.cs` — логика как в `transitionScript` мастера OneLineTransit2. **Утверждение перемещения по одному** `itamPortfolio` с вызовом `Update()` на каждую строку (так надёжнее сохраняются `lUserId` и `lt_lLocationId`, чем один пакетный `Update` по нескольким строкам). Поле «Откуда» в Word заполняется из снимка локаций **до** смены. После правок скопируйте файл в карточку сервиса в A-Tracker.

### Запрос (JSON, `Content-Type: application/json`, тело в `__json` на стороне платформы)

| Поле | Тип | Смысл |
|------|-----|--------|
| `lUserIdFrom` | int | ID отправителя (`itamEmplDept`) |
| `lUserIdTo` | int | ID получателя |
| `portfolioIds` | int[] | ID строк `itamPortfolio` (активы из заявки) |
| `seOrganization` | string | Наименование организации (как в заявке: «ООО АСГ» и т.д.) |
| `lReceiverLocationId` | int | Локация получателя; `0`, если не задана |

Тот же JSON формирует `front_site.app` в `admin_transfer_complete` (объект `body`).

### Ответ при успехе

- `returnCode`: `"Success"`
- `data`: массив из одного объекта, в нём **`operationId`** (int/long) — номер операции для отображения и учёта. Веб читает его в `_parse_operation_id_from_posting_response`.

### Ответ при ошибке

- `returnCode`: `"Error"`
- `message`: текст для администратора (веб показывает во flash).

## Уведомление по почте после загрузки скана

- После успешной проверки подписанного акта на `POST /transfers/{id}/upload-scan` на адрес из `config.ini` → `[email] transfer_notification_to` отправляется письмо с вложением (тот же файл).
- Статус отправки: поля `notification_sent_at` и `notification_last_error` в записи заявки (`front_site/data/transfers.json`).

## Применение в процессе передачи

- Поиск и валидация получателя: `ReturnEmpl`.
- Проверка принадлежности актива: `asset_find`.
- Инвентаризация/файлы: `InventAct`, `Asset_AddFile`.
- Синхронизация справочника сотрудников: `UpdateEmpl`, `CreateUser`.
- Утверждение перемещения и прикрепление скана к портфельным активам: кастомный сервис перемещения + `Asset_AddFile` по каждому ID из заявки.

## Шаблон Word «Акт передачи оборудования»

- В A-Tracker акт формируется из шаблона `itamWordTemplate("Акт передачи оборудования")` (см. мастер перемещения).
- В репозитории лежит эталон для сверки: `front_site/Акт передачи оборудования.docx` — **подложите сюда настоящий файл из A-Tracker** (если файл окажется «пустым» или не открывается как ZIP, скопируйте шаблон заново).
- На сайте черновик с той же структурой полей и таблицей активов: страница печати `GET /transfers/{id}/act` (шаблон `transfer_act_print.html`).

## Новый поток: «Добавить технику» (минимум API)

Для согласованного процесса (веб создаёт заявку -> админ обрабатывает в A-Tracker -> веб закрывает и уведомляет) достаточно 3 сервисов:

1. `CreateAssetAddRequest` (POST)  
   Код-шаблон: `integrations/atracker/services/ATRACKER_SERVICE_CreateAssetAddRequest.cs`

2. `AttachRequestDocument` (POST)  
   Код-шаблон: `integrations/atracker/services/ATRACKER_SERVICE_AttachRequestDocument.cs`

3. `GetAssetAddRequestState` (GET)  
   Код-шаблон: `integrations/atracker/services/ATRACKER_SERVICE_GetAssetAddRequestState.cs`

ТЗ на мастер A-Tracker для обработки заявок:  
`docs/ATRACKER_MASTER_AssetAdd_TZ.md`

Готовый transitionScript шага «Завершить» в мастере:
`integrations/atracker/masters/ATRACKER_MASTER_AssetAdd_Finalize.cs`

