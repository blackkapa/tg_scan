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


def _normalize_atracker_url(url: str) -> str:
    """Без завершающего /; www.atrdbapp.ovp.ru в DNS нет — только atrdbapp.ovp.ru."""
    u = (url or "").strip().rstrip("/")
    return u.replace("://www.atrdbapp.ovp.ru", "://atrdbapp.ovp.ru")


# --- A-Tracker (кроме URL, логина, пароля — из INI) ---
ATRACKER_BASE_URL = _normalize_atracker_url(
    _get("atracker", "base_url", "https://atrdbapp.ovp.ru")
)
# false — HTTPS с шифрованием, но без проверки внутреннего сертификата (типично для IIS в домене)
ATRACKER_VERIFY_SSL = _getbool("atracker", "verify_ssl", False)
ATRACKER_CA_BUNDLE = _get("atracker", "ca_bundle", "")
# На сервере A-Tracker: 127.0.0.1 — TCP локально, SNI/Host остаётся из base_url (обход hairpin / 10054)
ATRACKER_CONNECT_VIA = _get("atracker", "connect_via", "")

# --- AD → A-Tracker (sync_ad_atracker.py): готовая JSON-выгрузка пользователей ---
AD_EXPORT_PATH = _get("ad", "export_path", "data/ad_export.json")
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

# «Сообщить о несоответствии»: форма, список в «Заявках», /admin/discrepancies (false — отключить контур)
WEB_DISCREPANCY_ENABLED = _getbool("web", "discrepancy_enabled", True)

# Кнопка «Сообщить о несоответствии» на /assets (false — скрыть кнопку; маршруты /discrepancy/* остаются при discrepancy_enabled)
WEB_DISCREPANCY_BUTTON_ENABLED = _getbool("web", "discrepancy_button_enabled", True)


def _get_session_secret() -> str:
    """Секретный ключ для cookie-сессий.
    Приоритет:
    1. Переменная окружения SESSION_SECRET_KEY
    2. config.ini -> [web] session_secret
    3. Локальный файл data/.session_secret (для устойчивости сессий при рестартах)
    4. Случайный токен secrets.token_hex(32)
    """
    env_secret = os.environ.get("SESSION_SECRET_KEY", "").strip()
    if env_secret:
        return env_secret
    ini_secret = _get("web", "session_secret", "").strip()
    if ini_secret:
        return ini_secret
    data_dir = os.path.join(_config_dir, "data")
    secret_file = os.path.join(data_dir, ".session_secret")
    try:
        if os.path.isfile(secret_file):
            with open(secret_file, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        import secrets
        generated = secrets.token_hex(32)
        os.makedirs(data_dir, exist_ok=True)
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(generated)
        return generated
    except Exception:
        import secrets
        return secrets.token_hex(32)


SESSION_SECRET_KEY = _get_session_secret()


def get_settings_secret_hash() -> str:
    """Хэш SHA256 секрета для страницы /settings."""
    import hashlib
    # 1. Явный хэш из config.ini
    h = _get("web", "settings_secret_hash", "").strip().lower()
    if h:
        return h
    # 2. Plain-секрет из env или config.ini
    env_plain = os.environ.get("SETTINGS_SECRET", "").strip()
    if env_plain:
        return hashlib.sha256(env_plain.encode("utf-8")).hexdigest()
    ini_plain = _get("web", "settings_secret", "").strip()
    if ini_plain:
        return hashlib.sha256(ini_plain.encode("utf-8")).hexdigest()
    # 3. Persistent файл data/.settings_secret
    try:
        data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        secret_file = os.path.join(data_dir, ".settings_secret")
        if os.path.isfile(secret_file):
            with open(secret_file, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return hashlib.sha256(val.encode("utf-8")).hexdigest()
        import secrets
        generated = secrets.token_urlsafe(24)
        os.makedirs(data_dir, exist_ok=True)
        with open(secret_file, "w", encoding="utf-8") as f:
            f.write(generated)
        return hashlib.sha256(generated.encode("utf-8")).hexdigest()
    except Exception:
        return hashlib.sha256(SESSION_SECRET_KEY.encode("utf-8")).hexdigest()


def reload_web_flags_from_disk() -> None:
    """Перечитать config.ini и обновить runtime-настройки [web]/[email]/[atracker] в памяти."""
    global _cfg
    global WEB_PUBLIC_BASE_URL, WEB_ASSET_ADD_BUTTON_ENABLED, WEB_TRANSFER_ENABLED
    global WEB_DISCREPANCY_ENABLED, WEB_DISCREPANCY_BUTTON_ENABLED
    global ADMIN_EMAILS, BYPASS_CODE_EMAILS, EMAIL_DOMAIN_ALLOWED
    global TRANSFER_NOTIFICATION_TO, TRANSFER_ADMIN_CONFIRM_EMAIL
    global SESSION_SECRET_KEY, ATRACKER_VERIFY_SSL
    _cfg = ConfigParser()
    if os.path.isfile(_CONFIG_PATH):
        _cfg.read(_CONFIG_PATH, encoding="utf-8")
    # atracker
    ATRACKER_VERIFY_SSL = _getbool("atracker", "verify_ssl", False)
    # email / auth
    EMAIL_DOMAIN_ALLOWED = _get("email", "domain_allowed", "asg.ru")
    ADMIN_EMAILS = _parse_admin_emails()
    BYPASS_CODE_EMAILS = _parse_bypass_code_emails()
    TRANSFER_NOTIFICATION_TO = _get("email", "transfer_notification_to", "")
    TRANSFER_ADMIN_CONFIRM_EMAIL = _get("email", "transfer_admin_confirm_email", "mikhail.melgit@asg.ru")
    # web flags
    WEB_PUBLIC_BASE_URL = _get("web", "public_base_url", "")
    WEB_ASSET_ADD_BUTTON_ENABLED = _getbool("web", "asset_add_button_enabled", True)
    WEB_TRANSFER_ENABLED = _getbool("web", "transfer_enabled", True)
    WEB_DISCREPANCY_ENABLED = _getbool("web", "discrepancy_enabled", True)
    WEB_DISCREPANCY_BUTTON_ENABLED = _getbool("web", "discrepancy_button_enabled", True)
    SESSION_SECRET_KEY = _get_session_secret()



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
# Сервис поиска активов по серийному номеру (GET /Api/Service?id=...&SerialNo=...).
# 0 — проверка дублей по серийнику отключена.
ATRACKER_ASSET_FIND_BY_SERIAL_SERVICE_ID = _getint("atracker", "asset_find_by_serial_service_id", 0)

