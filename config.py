# -*- coding: utf-8 -*-
"""
Загрузка настроек из config.ini (рядом с скриптом или exe).
ID сервисов A-Tracker остаются в коде — зависят от конфигурации админки.
"""
import os
import sys
from configparser import ConfigParser, NoSectionError, NoOptionError

# Путь к config.ini: рядом со скриптом или exe
if getattr(sys, "frozen", False):
    _config_dir = os.path.dirname(sys.executable)
else:
    _config_dir = os.path.dirname(os.path.abspath(__file__))
_CONFIG_PATH = os.path.join(_config_dir, "config.ini")

_cfg = ConfigParser()
if os.path.isfile(_CONFIG_PATH):
    _cfg.read(_CONFIG_PATH, encoding="utf-8")


def _get(section: str, key: str, fallback: str = "") -> str:
    try:
        return _cfg.get(section, key, fallback=fallback).strip()
    except (NoSectionError, NoOptionError, TypeError):
        return fallback


def _getint(section: str, key: str, fallback: int = 0) -> int:
    try:
        return _cfg.getint(section, key)
    except (NoSectionError, NoOptionError, ValueError, TypeError):
        return fallback


def _getbool(section: str, key: str, fallback: bool = False) -> bool:
    v = _get(section, key, "").lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return fallback


# --- A-Tracker (кроме URL, логина, пароля — из INI) ---
ATRACKER_BASE_URL = _get("atracker", "base_url", "http://10.115.21.77")
ATRACKER_USERNAME = _get("atracker", "username", "admin")
ATRACKER_PASSWORD = _get("atracker", "password", "")

# ID сервисов A-Tracker — в коде (зависят от админки)
ATRACKER_ASSETS_SERVICE_ID = 1
ATRACKER_MARK_SERVICE_ID = 2
ATRACKER_UPLOAD_DOC_SERVICE_ID = 3
ATRACKER_ASSET_INFO_SERVICE_ID = 4
ATRACKER_EMPLOYEES_LIST_SERVICE_ID = 5
ATRACKER_EMPLOYEE_UPDATE_SERVICE_ID = 6
ATRACKER_EMPLOYEE_ADD_SERVICE_ID = 7  # Создание нового сотрудника (если нет в A-Tracker)

# --- Пути ---
AD_EXPORT_PATH = _get("paths", "ad_export", "ad_export.json")
REGISTRY_FILE_PATH = _get("paths", "registry_file", "data/")
REGISTRY_STATE_FILE = _get("paths", "registry_state", "data/registry_processed.json")
REGISTRY_PROCESSED_DIR = _get("paths", "registry_processed_dir", "data/processed")

# --- Email / SMTP ---
EMAIL_DOMAIN_ALLOWED = _get("email", "domain_allowed", "asg.ru")
SMTP_HOST = _get("smtp", "host", "smtp.yandex.ru")
SMTP_PORT = _getint("smtp", "port", 465)
SMTP_USE_SSL = _getbool("smtp", "use_ssl", True)
SMTP_USER = _get("smtp", "user", "")
SMTP_PASSWORD = _get("smtp", "password", "")
SMTP_FROM = _get("smtp", "from", "") or SMTP_USER or "noreply@asg.ru"


def _parse_admin_emails() -> frozenset[str]:
    """Список почт, которым разрешён админский режим в веб-интерфейсе."""
    raw = _get("email", "admin_emails", "")
    if not raw:
        return frozenset()
    items: list[str] = []
    for part in raw.replace(",", " ").split():
        part = part.strip().lower()
        if part:
            items.append(part)
    return frozenset(items)


ADMIN_EMAILS = _parse_admin_emails()


def _parse_bypass_code_emails() -> frozenset[str]:
    """Почты, для которых в вебе не требуется код из письма (вход сразу после проверки в A‑Tracker)."""
    raw = _get("email", "bypass_code_emails", "")
    if not raw:
        return frozenset()
    items: list[str] = []
    for part in raw.replace(",", " ").split():
        part = part.strip().lower()
        if part:
            items.append(part)
    return frozenset(items)


BYPASS_CODE_EMAILS = _parse_bypass_code_emails()

# Устарело: раньше общий ящик для уведомления о скане; см. transfer_admin_confirm_email
TRANSFER_NOTIFICATION_TO = _get("email", "transfer_notification_to", "")
# Письмо «Подтвердить перемещение №…» с вложением скана (шаг после загрузки акта)
TRANSFER_ADMIN_CONFIRM_EMAIL = _get("email", "transfer_admin_confirm_email", "mikhail.melgit@asg.ru")

# Публичный URL сайта для ссылок в письмах (без завершающего /), например https://inventory.example.com
WEB_PUBLIC_BASE_URL = _get("web", "public_base_url", "")

# Кнопка «Добавить технику» на /assets и форма /asset-add/start (false — скрыть и закрыть подачу новых заявок)
WEB_ASSET_ADD_BUTTON_ENABLED = _getbool("web", "asset_add_button_enabled", True)

# Заявки на перемещение техники: чекбоксы на /assets, /transfer/start, список transfer в «Заявках» (false — отключить контур)
WEB_TRANSFER_ENABLED = _getbool("web", "transfer_enabled", True)


def reload_web_flags_from_disk() -> None:
    """Перечитать config.ini и обновить переменные [web] в памяти (после /settings/save без обязательного рестарта)."""
    global _cfg, WEB_PUBLIC_BASE_URL, WEB_ASSET_ADD_BUTTON_ENABLED, WEB_TRANSFER_ENABLED
    _cfg = ConfigParser()
    if os.path.isfile(_CONFIG_PATH):
        _cfg.read(_CONFIG_PATH, encoding="utf-8")
    WEB_PUBLIC_BASE_URL = _get("web", "public_base_url", "")
    WEB_ASSET_ADD_BUTTON_ENABLED = _getbool("web", "asset_add_button_enabled", True)
    WEB_TRANSFER_ENABLED = _getbool("web", "transfer_enabled", True)


# ID кастомного сервиса A-Tracker: утверждение перемещения (как мастер OneLineTransit2). 0 — не вызывать.
ATRACKER_TRANSFER_POSTING_SERVICE_ID = _getint("atracker", "transfer_posting_service_id", 0)

# Справочник местоположений (GET), как ReturnEmpl. 0 — в форме передачи подставляются только места с выбранных активов.
ATRACKER_LOCATIONS_LIST_SERVICE_ID = _getint("atracker", "locations_list_service_id", 0)

# Справочник категорий активов (GET, itamCategory), по аналогии с locations_list. 0 — имя категории только если пришло в карточке актива.
ATRACKER_CATEGORIES_LIST_SERVICE_ID = _getint("atracker", "categories_list_service_id", 0)

# Сервисы потока «Добавить технику» (0 — пока не настроено, используется только локальный контур веба).
ATRACKER_ASSET_ADD_REQUEST_CREATE_SERVICE_ID = _getint("atracker", "asset_add_request_create_service_id", 0)
ATRACKER_ASSET_ADD_REQUEST_GET_SERVICE_ID = _getint("atracker", "asset_add_request_get_service_id", 0)
ATRACKER_PORTFOLIO_CREATE_SERVICE_ID = _getint("atracker", "portfolio_create_service_id", 0)
ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID = _getint("atracker", "portfolio_update_service_id", 0)
ATRACKER_REQUEST_ATTACH_SERVICE_ID = _getint("atracker", "request_attach_service_id", 0)

# --- Telegram ---
TELEGRAM_BOT_TOKEN = _get("telegram", "bot_token", "")

def _parse_admin_ids() -> frozenset:
    s = _get("telegram", "admin_ids", "")
    if not s:
        return frozenset()
    ids = []
    for x in s.replace(",", " ").split():
        try:
            ids.append(int(x.strip()))
        except ValueError:
            pass
    return frozenset(ids)

ADMIN_TELEGRAM_IDS = _parse_admin_ids()

REPORT_GROUP_ID = _getint("telegram", "report_group_id", -1003761721933)

def _get_registry_notify_group_id():
    s = _get("telegram", "registry_notify_group_id", "")
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None

REGISTRY_NOTIFY_GROUP_ID = _get_registry_notify_group_id()
