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
