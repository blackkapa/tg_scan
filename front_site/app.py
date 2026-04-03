from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from jinja2 import Environment, FileSystemLoader, select_autoescape
import qrcode
from io import BytesIO
import base64
from PIL import Image, ImageDraw, ImageFont
import os
import sys
import subprocess
import hashlib
from configparser import ConfigParser

# Берём настройки и клиент A-Tracker из существующего кода, но здесь пока только инициализируем.
from config import (
    ATRACKER_BASE_URL,
    ATRACKER_USERNAME,
    ATRACKER_PASSWORD,
    ATRACKER_ASSETS_SERVICE_ID,
    ATRACKER_MARK_SERVICE_ID,
    ATRACKER_UPLOAD_DOC_SERVICE_ID,
    ATRACKER_ASSET_INFO_SERVICE_ID,
    ATRACKER_EMPLOYEES_LIST_SERVICE_ID,
    ADMIN_EMAILS,
    EMAIL_DOMAIN_ALLOWED,
    _CONFIG_PATH as CONFIG_PATH,
)
from atracker_client import ATrackerClient

from .auth_web import find_employee_by_input, create_code, check_code, send_code_email
from .qr_utils import decode_qr_from_bytes, extract_asset_id_from_qr_text

BASE_DIR = Path(__file__).resolve().parent
AUDIT_LOG_PATH = BASE_DIR / "logs" / "audit.log"

app = FastAPI(title="Инвентаризация техники")

# Простой middleware для сессий на куках. Ключ пока захардкожен, при выкатывании на сервер
# его лучше вынести в переменную окружения или config.ini.
app.add_middleware(
    SessionMiddleware,
    secret_key="change-me-to-random-secret-key",
    max_age=60 * 60 * 2,  # примерно 2 часа
)

static_dir = BASE_DIR / "static"
templates_dir = BASE_DIR / "templates"

app.mount("/static", StaticFiles(directory=static_dir), name="static")

# На Debian с Python 3.11/стеком FastAPI попадаем на баг Jinja2 LRUCache
# ("unhashable type: 'dict'" при работе с cache_key). Выключаем кэш шаблонов,
# чтобы обойти эту проблему — для нашего объёма шаблонов это некритично.
jinja_env = Environment(
    loader=FileSystemLoader(str(templates_dir)),
    autoescape=select_autoescape(["html", "xml"]),
    cache_size=0,
)


def render_template(name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    template = jinja_env.get_template(name)
    ctx = dict(context or {})
    request = ctx.get("request")
    if request is not None:
        # Даём шаблонам привычную функцию url_for, как у StarletteTemplates.
        ctx["url_for"] = request.url_for
    content = template.render(ctx)
    return HTMLResponse(content=content, status_code=status_code)


templates = Jinja2Templates(directory=str(templates_dir))


def _write_audit(request: Request, action: str, details: str = "") -> None:
    """Пишем простую строку аудита в файл logs/audit.log."""
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        email = (request.session.get("user_email") or "-").strip()
        ip = getattr(request.client, "host", "-") if request.client else "-"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts}\t{email}\t{ip}\t{action}\t{details}\n"
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Аудит не должен ломать основной поток.
        pass


def _build_atracker_client() -> ATrackerClient:
    """Создаём клиента A-Tracker, чтобы потом переиспользовать его в обработчиках."""
    return ATrackerClient(
        base_url=ATRACKER_BASE_URL,
        username=ATRACKER_USERNAME,
        password=ATRACKER_PASSWORD,
        assets_service_id=ATRACKER_ASSETS_SERVICE_ID,
        mark_service_id=ATRACKER_MARK_SERVICE_ID,
        upload_doc_service_id=ATRACKER_UPLOAD_DOC_SERVICE_ID,
        asset_info_service_id=ATRACKER_ASSET_INFO_SERVICE_ID,
        employees_list_service_id=ATRACKER_EMPLOYEES_LIST_SERVICE_ID,
        employee_update_service_id=None,
        employee_add_service_id=None,
    )


def _is_asset_inventoried(asset: dict) -> bool:
    """Смотрим на возможные поля статуса инвентаризации и решаем, проведён актив или нет."""
    return (
        asset.get("IsInventoried") is True
        or asset.get("IsInventoried") == "True"
        or asset.get("InventoryStatus") in ("Completed", "Проведена")
        or asset.get("bInventoried") is True
        or asset.get("bInventoried") == 1
    )


def _norm_fio(value: str) -> str:
    return " ".join((value or "").split()).lower()


# --- Настройки скрытой страницы /settings ---
_SETTINGS_SECRET_PLAIN = "whorebear"
_SETTINGS_SECRET_HASH = hashlib.sha256(_SETTINGS_SECRET_PLAIN.encode("utf-8")).hexdigest()


def _check_settings_secret(secret: str) -> bool:
    if not secret:
        return False
    candidate = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return candidate == _SETTINGS_SECRET_HASH


def _load_settings_config() -> ConfigParser:
    cfg = ConfigParser()
    if os.path.isfile(CONFIG_PATH):
        cfg.read(CONFIG_PATH, encoding="utf-8")
    return cfg


def _ensure_section(cfg: ConfigParser, name: str) -> None:
    if not cfg.has_section(name):
        cfg.add_section(name)


def _save_settings_config(
    atracker_base_url: str,
    atracker_username: str,
    atracker_password: str,
    email_domain_allowed: str,
    email_admin_emails: str,
    smtp_host: str,
    smtp_port: str,
    smtp_use_ssl: str,
    smtp_user: str,
    smtp_password: str,
    smtp_from: str,
) -> None:
    cfg = _load_settings_config()

    _ensure_section(cfg, "atracker")
    cfg.set("atracker", "base_url", (atracker_base_url or "").strip())
    cfg.set("atracker", "username", (atracker_username or "").strip())
    cfg.set("atracker", "password", (atracker_password or "").strip())

    _ensure_section(cfg, "email")
    if email_domain_allowed:
        cfg.set("email", "domain_allowed", email_domain_allowed.strip())
    if email_admin_emails is not None:
        cfg.set("email", "admin_emails", email_admin_emails.strip())

    _ensure_section(cfg, "smtp")
    if smtp_host:
        cfg.set("smtp", "host", smtp_host.strip())
    if smtp_port:
        cfg.set("smtp", "port", smtp_port.strip())
    if smtp_use_ssl:
        cfg.set("smtp", "use_ssl", smtp_use_ssl.strip())
    if smtp_user:
        cfg.set("smtp", "user", smtp_user.strip())
    if smtp_password is not None:
        cfg.set("smtp", "password", smtp_password.strip())
    if smtp_from:
        cfg.set("smtp", "from", smtp_from.strip())

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        cfg.write(f)


def _restart_front_site_service() -> bool:
    """
    Пробуем перезапустить systemd-сервис front_site.service.
    Возвращаем True при успехе/отсутствии systemd, False при явной ошибке.
    """
    # Только на Linux есть смысл пробовать systemctl.
    if not sys.platform.startswith("linux"):
        return True
    try:
        result = subprocess.run(
            ["systemctl", "restart", "front_site.service"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.returncode == 0
    except Exception:
        return False


def _build_qr_png_base64(url: str) -> str:
    """Строим QR-код в PNG и возвращаем base64-строку для встраивания в <img>."""
    qr = qrcode.QRCode(border=1, box_size=6)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _build_qr_label_png(asset_name: str, serial: str, invent: str, asset_id: int, uuid: str, qr_url: str) -> bytes:
    """
    Строим PNG-ярлык 580x293 с QR слева и текстом справа,
    примерно по тем же параметрам, что и в A-Tracker.
    """
    # Общий холст
    width, height = 580, 293
    img = Image.new("RGB", (width, height), color="white")
    draw = ImageDraw.Draw(img)

    # QR-код
    qr = qrcode.QRCode(border=1, box_size=5)
    qr.add_data(qr_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    # Размеры QR из настроек (примерно)
    qr_target_w, qr_target_h = 223, 215
    qr_img = qr_img.resize((qr_target_w, qr_target_h), Image.LANCZOS)
    qr_x, qr_y = 6, 35
    img.paste(qr_img, (qr_x, qr_y))

    # Шрифты с поддержкой кириллицы (DejaVuSans есть почти на всех Debian)
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)
    except Exception:
        title_font = ImageFont.load_default()
    try:
        desc_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
    except Exception:
        desc_font = ImageFont.load_default()

    # Текст справа
    # Отступ от QR-кода чуть больше, чем впритык, чтобы текст не прилипал.
    start_x = qr_x + qr_target_w + 24
    start_y = 55
    line_height = 27

    lines = [
        asset_name or "",
        f"Серийный номер: {serial or '-'}",
        f"Инв. номер: {invent or '-'}",
        f"ID в системе: {asset_id}",
        f"UUID: {uuid or '-'}",
    ]
    for idx, line in enumerate(lines):
        y = start_y + idx * line_height
        font = title_font if idx == 0 else desc_font
        draw.text((start_x, y), line, font=font, fill="black")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Стартовая страница: форма для ввода ФИО, логина или почты."""
    message = request.session.pop("flash_message", None)
    context = {
        "request": request,
        "title": "Инвентаризация техники",
        "message": message,
    }
    return render_template("index.html", context)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Скрытая страница настроек: сначала вводим секрет, затем показываем дашборд."""
    if not request.session.get("settings_ok"):
        context = {
            "request": request,
            "title": "Настройки",
            "message": request.session.pop("flash_message", None),
        }
        return render_template("settings_lock.html", context)

    # Загрузка конфигурации для формы
    cfg = _load_settings_config()
    atracker_base_url = cfg.get("atracker", "base_url", fallback="")
    atracker_username = cfg.get("atracker", "username", fallback="")
    atracker_password = cfg.get("atracker", "password", fallback="")

    email_domain_allowed = cfg.get("email", "domain_allowed", fallback="")
    email_admin_emails = cfg.get("email", "admin_emails", fallback="")

    smtp_host = cfg.get("smtp", "host", fallback="")
    smtp_port = cfg.get("smtp", "port", fallback="")
    smtp_use_ssl = cfg.get("smtp", "use_ssl", fallback="")
    smtp_user = cfg.get("smtp", "user", fallback="")
    smtp_password = cfg.get("smtp", "password", fallback="")
    smtp_from = cfg.get("smtp", "from", fallback="")

    # Фильтры для читаемости аудита (по query-параметрам)
    try:
        limit = int(request.query_params.get("limit", "60"))
    except ValueError:
        limit = 60
    limit = max(10, min(limit, 200))
    filter_email = (request.query_params.get("filter_email", "") or "").strip().lower()
    filter_action = (request.query_params.get("filter_action", "") or "").strip().lower()

    # Сразу пишем текущий визит, чтобы в таблице/группах он отображался уже на этом же открытии.
    _write_audit(request, action="settings_open")

    # Читаем audit.log (может отсутствовать)
    audit_rows = []
    if AUDIT_LOG_PATH.is_file():
        try:
            with AUDIT_LOG_PATH.open("r", encoding="utf-8") as f:
                # Берём запас, чтобы фильтры не "пустили" таблицу.
                window = max(200, limit * 5)
                lines = f.readlines()[-window:]
            for line in reversed(lines):
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 5:
                    continue
                ts, email, ip, action, details = parts[:5]
                row = {
                    "ts": ts,
                    "email": email,
                    "ip": ip,
                    "action": action,
                    "details": details,
                }
                if filter_email and filter_email not in (email or "").lower():
                    continue
                if filter_action and filter_action not in (action or "").lower():
                    continue
                audit_rows.append(
                    {
                        "ts": row["ts"],
                        "email": row["email"],
                        "ip": row["ip"],
                        "action": row["action"],
                        "details": row["details"],
                    }
                )
        except Exception:
            audit_rows = []

    # Ограничиваем количество строк сверху (audit_rows уже в порядке \"самое свежее первым\")
    audit_rows = audit_rows[:limit]

    context = {
        "request": request,
        "title": "Настройки",
        "message": request.session.pop("flash_message", None),
        "config": {
            "atracker_base_url": atracker_base_url,
            "atracker_username": atracker_username,
            "atracker_password": atracker_password,
            "email_domain_allowed": email_domain_allowed,
            "email_admin_emails": email_admin_emails,
            "smtp_host": smtp_host,
            "smtp_port": smtp_port,
            "smtp_use_ssl": smtp_use_ssl,
            "smtp_user": smtp_user,
            "smtp_password": smtp_password,
            "smtp_from": smtp_from,
        },
        "audit_rows": audit_rows,
    }
    return render_template("settings_dashboard.html", context)


@app.post("/settings", response_class=HTMLResponse)
async def settings_unlock(request: Request, secret: str = Form(...)):
    """Проверка секрета для доступа к настройкам."""
    if not _check_settings_secret(secret or ""):
        request.session["flash_message"] = "Доступ запрещён."
        return RedirectResponse(url="/settings", status_code=302)

    request.session["settings_ok"] = True
    return RedirectResponse(url="/settings", status_code=302)


@app.post("/settings/save", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    atracker_base_url: str = Form(""),
    atracker_username: str = Form(""),
    atracker_password: str = Form(""),
    email_domain_allowed: str = Form(""),
    email_admin_emails: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form(""),
    smtp_use_ssl: str = Form("true"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
):
    """Сохранение настроек в config.ini и попытка перезапуска сервиса."""
    if not request.session.get("settings_ok"):
        request.session["flash_message"] = "Доступ запрещён."
        return RedirectResponse(url="/settings", status_code=302)

    try:
        _save_settings_config(
            atracker_base_url=atracker_base_url,
            atracker_username=atracker_username,
            atracker_password=atracker_password,
            email_domain_allowed=email_domain_allowed,
            email_admin_emails=email_admin_emails,
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_use_ssl=smtp_use_ssl,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            smtp_from=smtp_from,
        )
        restarted = _restart_front_site_service()
        if restarted:
            msg = "Настройки сохранены и сервис перезапущен."
        else:
            msg = (
                "Настройки сохранены, но не удалось автоматически перезапустить сервис. "
                "Проверьте front_site.service вручную."
            )
        request.session["flash_message"] = msg
        _write_audit(
            request,
            action="settings_save",
            details=f"restarted={restarted}",
        )
    except Exception as exc:
        request.session["flash_message"] = (
            "Не удалось сохранить настройки. Проверьте значения и повторите попытку."
        )
        _write_audit(
            request,
            action="settings_save_error",
            details=str(exc),
        )

    return RedirectResponse(url="/settings", status_code=302)


@app.post("/start-auth")
async def start_auth(request: Request, identifier: str = Form(...)):
    """Получаем ФИО/логин/почту, ищем сотрудника и отправляем код на корпоративную почту."""
    identifier = (identifier or "").strip()
    if not identifier:
        context = {
            "request": request,
            "title": "Инвентаризация техники",
            "message": "Введите ФИО, логин или почту.",
        }
        return render_template("index.html", context, status_code=400)

    try:
        client = _build_atracker_client()
        employees = await client.get_employees()
    except Exception:
        context = {
            "request": request,
            "title": "Инвентаризация техники",
            "message": "Не удалось загрузить список сотрудников из A‑Tracker. Попробуйте позже.",
        }
        return render_template("index.html", context, status_code=502)

    fio, email, error = find_employee_by_input(employees, identifier, EMAIL_DOMAIN_ALLOWED)
    if error:
        context = {
            "request": request,
            "title": "Инвентаризация техники",
            "message": error,
        }
        return render_template("index.html", context, status_code=400)

    code = create_code(fio or "", email or "")
    ok, send_error = send_code_email(email, code)
    if not ok:
        context = {
            "request": request,
            "title": "Инвентаризация техники",
            "message": send_error,
        }
        return render_template("index.html", context, status_code=502)

    # Запоминаем, куда отправили код, чтобы потом показать это в шаблоне.
    request.session["pending_fio"] = fio
    request.session["pending_email"] = email

    return RedirectResponse(url="/enter-code", status_code=302)


@app.get("/enter-code", response_class=HTMLResponse)
async def enter_code_form(request: Request):
    """Страница ввода кода из письма."""
    pending_email = request.session.get("pending_email")
    if not pending_email:
        return RedirectResponse(url="/", status_code=302)
    message = request.session.pop("flash_message", None)
    context = {
        "request": request,
        "title": "Ввод кода",
        "email": pending_email,
        "message": message,
    }
    return render_template("enter_code.html", context)


@app.post("/enter-code", response_class=HTMLResponse)
async def submit_code(request: Request, code: str = Form(...)):
    """Проверяем код, при успехе фиксируем пользователя в сессии."""
    pending_email = request.session.get("pending_email")
    if not pending_email:
        return RedirectResponse(url="/", status_code=302)

    code = (code or "").strip()
    if not code:
        request.session["flash_message"] = "Введите код из письма."
        return RedirectResponse(url="/enter-code", status_code=302)

    result = check_code(code)
    if not result:
        request.session["flash_message"] = "Код неверный или истёк. Запросите новый код."
        return RedirectResponse(url="/enter-code", status_code=302)

    fio, email = result
    email_normalized = (email or "").strip().lower()
    request.session.pop("pending_fio", None)
    request.session.pop("pending_email", None)
    request.session["user_fio"] = fio
    request.session["user_email"] = email
    request.session["is_admin"] = email_normalized in ADMIN_EMAILS
    _write_audit(
        request,
        action="login_success",
        details=f"user_fio={fio}",
    )
    # После успешного входа сразу ведём на список активов.
    return RedirectResponse(url="/assets", status_code=302)


@app.get("/assets", response_class=HTMLResponse)
async def assets_page(request: Request):
    """Список активов текущего пользователя."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    _write_audit(request, action="view_assets", details=f"user_fio={fio}")

    try:
        client = _build_atracker_client()
        assets = await client.get_assets_by_fio(fio)
    except Exception:
        context = {
            "request": request,
            "title": "Мои активы",
            "error": "Не удалось загрузить список активов из A‑Tracker. Попробуйте позже.",
        }
        return render_template("assets.html", context, status_code=502)

    if not assets:
        context = {
            "request": request,
            "title": "Мои активы",
            "fio": fio,
        }
        return render_template("no_assets.html", context)

    items = []
    for a in assets:
        asset_id = int(a.get("ID"))
        name = a.get("sFullName") or a.get("Name") or f"ID {asset_id}"
        serial = a.get("sSerialNo") or "-"
        invent = (
            a.get("sInventoryNo")
            or a.get("sInventNo")
            or a.get("InventoryNo")
            or "-"
        )
        inventoried = _is_asset_inventoried(a)
        items.append(
            {
                "id": asset_id,
                "name": name,
                "serial": serial,
                "invent": invent,
                "inventoried": inventoried,
            }
        )

    context = {
        "request": request,
        "title": "Мои активы",
        "fio": fio,
        "assets": items,
        "is_admin": bool(request.session.get("is_admin")),
        "message": request.session.pop("flash_message", None),
    }
    return render_template("assets.html", context)


@app.get("/logout")
async def logout(request: Request):
    """Выход из веб-приложения: очищаем сессию и возвращаем на экран входа."""
    _write_audit(request, action="logout")
    for key in ("user_fio", "user_email", "is_admin", "pending_fio", "pending_email", "admin_target_fio", "admin_assets"):
        request.session.pop(key, None)
    request.session["flash_message"] = "Вы вышли из системы."
    return RedirectResponse(url="/", status_code=302)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Стартовая страница режима администратора."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    target_fio = request.session.get("admin_target_fio")
    assets = None
    if target_fio:
        try:
            client = _build_atracker_client()
            raw_assets = await client.get_assets_by_fio(target_fio)
            items = []
            for a in raw_assets or []:
                asset_id = int(a.get("ID"))
                name = a.get("sFullName") or a.get("Name") or f"ID {asset_id}"
                serial = a.get("sSerialNo") or "-"
                invent = (
                    a.get("sInventoryNo")
                    or a.get("sInventNo")
                    or a.get("InventoryNo")
                    or "-"
                )
                inventoried = _is_asset_inventoried(a)
                items.append(
                    {
                        "id": asset_id,
                        "name": name,
                        "serial": serial,
                        "invent": invent,
                        "inventoried": inventoried,
                    }
                )
            assets = items
        except Exception:
            request.session["flash_message"] = (
                "Не удалось обновить список активов выбранного сотрудника."
            )

    context = {
        "request": request,
        "title": "Режим администратора",
        "fio": fio,
        "target_fio": target_fio,
        "assets": assets,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("admin.html", context)


@app.post("/admin", response_class=HTMLResponse)
async def admin_search(request: Request, identifier: str = Form(...)):
    """Поиск сотрудника по ФИО/логину/почте и показ его активов (для администратора)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    identifier = (identifier or "").strip()
    if not identifier:
        request.session["flash_message"] = "Введите ФИО, логин или почту сотрудника."
        return RedirectResponse(url="/admin", status_code=302)

    try:
        client = _build_atracker_client()
        employees = await client.get_employees()
    except Exception:
        request.session["flash_message"] = (
            "Не удалось загрузить список сотрудников из A‑Tracker. Попробуйте позже."
        )
        return RedirectResponse(url="/admin", status_code=302)

    target_fio, target_email, error = find_employee_by_input(
        employees, identifier, EMAIL_DOMAIN_ALLOWED
    )
    if error:
        request.session["flash_message"] = error
        return RedirectResponse(url="/admin", status_code=302)

    try:
        assets = await client.get_assets_by_fio(target_fio)
    except Exception:
        request.session["flash_message"] = (
            f"Не удалось загрузить активы сотрудника {target_fio}."
        )
        return RedirectResponse(url="/admin", status_code=302)

    request.session["admin_target_fio"] = target_fio
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/assets/{asset_id}/inventory")
async def mark_inventory_view(request: Request, asset_id: int, file: UploadFile = File(...)):
    """Инвентаризация по фото: проверяем QR с изображения и только потом отмечаем инвентаризацию."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    # Проверяем, что файл пришёл и не пустой.
    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для инвентаризации."
        return RedirectResponse(url="/assets", status_code=302)

    content = await file.read()
    if not content:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/assets", status_code=302)

    # Пытаемся вытащить QR и ID актива.
    qr_text = decode_qr_from_bytes(content)
    if not qr_text:
        request.session["flash_message"] = (
            "На изображении не найден QR-код. Убедитесь, что наклейка попала в кадр полностью."
        )
        return RedirectResponse(url="/assets", status_code=302)

    qr_asset_id = extract_asset_id_from_qr_text(qr_text)
    if qr_asset_id is None:
        request.session["flash_message"] = (
            "QR-код распознан, но не удалось определить ID актива. Попробуйте ещё раз."
        )
        return RedirectResponse(url="/assets", status_code=302)

    # QR должен относиться именно к этому активу.
    if qr_asset_id != asset_id:
        # Попробуем узнать владельца ошибочного актива, чтобы сообщение было понятнее.
        owner_fio = "другим сотрудником"
        try:
            client = _build_atracker_client()
            info, err = await client.get_asset_info(qr_asset_id)
            if not err and info:
                owner_fio = info.get("OwnerFio") or owner_fio
        except Exception:
            pass

        request.session["flash_message"] = (
            f"QR-код на фото относится к другому активу (ID {qr_asset_id}, владелец: {owner_fio}). "
            "Загрузите фото с наклейкой именно от этой техники."
        )
        return RedirectResponse(url="/assets", status_code=302)

    # Теперь можно безопасно отметить инвентаризацию именно по этому активу
    # и прикрепить использованное фото к карточке.
    username_comment = (
        f"Username=photoQR-web-invent by {email} at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    try:
        client = _build_atracker_client()
        await client.mark_inventory(
            asset_id=asset_id,
            fio=fio,
            tg_user_id=0,
            tg_username=username_comment,
        )
        # Пытаемся прикрепить то же фото к активу.
        try:
            await client.upload_asset_file(
                asset_id=asset_id,
                file_name=file.filename,
                content_bytes=content,
                content_type=file.content_type or "image/jpeg",
            )
            request.session["flash_message"] = (
                "Инвентаризация по активу успешно отмечена по фото, снимок сохранён в A‑Tracker."
            )
        except Exception:
            # Инвентаризация прошла, но фото не сохранили.
            request.session["flash_message"] = (
                "Инвентаризация по активу успешно отмечена по фото, "
                "но не удалось сохранить снимок в A‑Tracker."
            )
    except Exception:
        request.session["flash_message"] = (
            "Не удалось отметить инвентаризацию по фото. Попробуйте ещё раз чуть позже."
        )

    return RedirectResponse(url="/assets", status_code=302)


@app.get("/assets/{asset_id}", response_class=HTMLResponse)
async def asset_detail(request: Request, asset_id: int):
    """Карточка одного актива с возможностью прикрепить фото."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    try:
        client = _build_atracker_client()
        info, err = await client.get_asset_info(asset_id)
    except Exception:
        info, err = None, "service_error"

    if err == "not_found":
        context = {
            "request": request,
            "title": "Информация об активе",
            "error": "Актив не найден в A‑Tracker.",
        }
        return render_template("asset_detail.html", context, status_code=404)
    if err == "service_error" or not info:
        context = {
            "request": request,
            "title": "Информация об активе",
            "error": "Не удалось загрузить информацию об активе. Попробуйте позже.",
        }
        return render_template("asset_detail.html", context, status_code=502)

    owner_fio = info.get("OwnerFio") or "—"
    is_admin = bool(request.session.get("is_admin"))
    # Если это не наш актив и мы не админ — показываем только понятную ошибку.
    if not is_admin and _norm_fio(owner_fio) != _norm_fio(fio):
        context = {
            "request": request,
            "title": "Информация об активе",
            "error": (
                f"Этот актив закреплён за другим сотрудником: {owner_fio}. "
                "Просмотр доступен только владельцу или администратору."
            ),
        }
        return render_template("asset_detail.html", context, status_code=403)

    context = {
        "request": request,
        "title": "Информация об активе",
        "asset": info,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("asset_detail.html", context)


@app.get("/assets/{asset_id}/qr-label")
async def asset_qr_label(request: Request, asset_id: int):
    """Генерация PNG-ярлыка с QR-кодом для печати по активу."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    try:
        client = _build_atracker_client()
        info, err = await client.get_asset_info(asset_id)
    except Exception:
        info, err = None, "service_error"

    if err or not info:
        request.session["flash_message"] = "Не удалось загрузить данные актива для печати QR."
        return RedirectResponse(url="/assets", status_code=302)

    # Владелец проверяется так же, как и в карточке, чтобы не печатать ярлык для чужой техники.
    owner_fio = info.get("OwnerFio") or "—"
    is_admin = bool(request.session.get("is_admin"))
    if not is_admin and _norm_fio(owner_fio) != _norm_fio(fio):
        request.session["flash_message"] = (
            f"Этот актив закреплён за другим сотрудником: {owner_fio}. "
            "Печать ярлыка доступна только владельцу или администратору."
        )
        return RedirectResponse(url="/assets", status_code=302)

    asset_name = info.get("sFullName") or info.get("Name") or f"ID {asset_id}"
    serial = info.get("sSerialNo") or ""
    invent = (
        info.get("sInventoryNo")
        or info.get("sInventNo")
        or info.get("InventoryNo")
        or ""
    )
    uuid = info.get("sPartNo") or ""

    # QR-ссылка: ведём на карточку актива в A-Tracker, как в шаблоне ярлыка.
    qr_url = f"https://atrdbapp.ovp.ru/Home/Data?SQLName=itamPortfolio&ID={asset_id}"
    png_bytes = _build_qr_label_png(asset_name, serial, invent, asset_id, uuid, qr_url)
    filename = f"asset_{asset_id}_qr.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/scan-qr", response_class=HTMLResponse)
async def qr_form(request: Request):
    """Форма для загрузки фото с QR-кодом."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    # В текущем UX мы открываем выбор файла напрямую со страницы /assets
    # и всегда возвращаемся обратно на /assets. Эта ручка остаётся только
    # на случай прямого захода по URL.
    return RedirectResponse(url="/assets", status_code=302)


@app.post("/scan-qr")
async def qr_scan(request: Request, file: UploadFile = File(...)):
    """Принимаем фото с QR, распознаём и пытаемся отметить инвентаризацию по найденному активу."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для загрузки."
        return RedirectResponse(url="/assets", status_code=302)

    image_bytes = await file.read()
    if not image_bytes:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/assets", status_code=302)

    qr_text = decode_qr_from_bytes(image_bytes)
    if not qr_text:
        request.session["flash_message"] = (
            "На изображении не найден QR-код. Убедитесь, что наклейка читаема "
            "и попала в кадр полностью."
        )
        return RedirectResponse(url="/assets", status_code=302)

    asset_id = extract_asset_id_from_qr_text(qr_text)
    if asset_id is None:
        request.session["flash_message"] = (
            "QR-код распознан, но не содержит ID актива. "
            "Попробуйте ещё раз или проверьте наклейку."
        )
        return RedirectResponse(url="/assets", status_code=302)

    try:
        client = _build_atracker_client()
        # Загружаем список активов текущего пользователя и проверяем, относится ли QR к одному из них.
        assets = await client.get_assets_by_fio(fio)
        assets_by_id = {int(a.get("ID")): a for a in assets if isinstance(a, dict) and a.get("ID") is not None}
    except Exception:
        request.session["flash_message"] = (
            "Не удалось проверить QR в A‑Tracker. Попробуйте позже."
        )
        return RedirectResponse(url="/assets", status_code=302)

    asset = assets_by_id.get(asset_id)
    if not asset:
        # Актив не найден среди техники пользователя. Попробуем узнать детали и владельца,
        # чтобы показать понятное сообщение.
        owner_fio = "—"
        asset_name = ""
        serial_no = ""
        invent_no = ""
        try:
            info, err = await client.get_asset_info(asset_id)
            if not err and info:
                owner_fio = info.get("OwnerFio") or owner_fio
                asset_name = info.get("sFullName") or info.get("Name") or ""
                serial_no = info.get("sSerialNo") or ""
                invent_no = (
                    info.get("sInventoryNo")
                    or info.get("sInventNo")
                    or info.get("InventoryNo")
                    or ""
                )
        except Exception:
            pass
        # Сообщение для кнопки «Узнать чья техника по QR».
        # Показываем владельца и основные реквизиты актива.
        base = f"Техника закреплена за {owner_fio}."
        details_parts = []
        if asset_name:
            details_parts.append(f"Наименование: {asset_name}")
        if serial_no:
            details_parts.append(f"Серийный номер: {serial_no}")
        if invent_no:
            details_parts.append(f"Инвентарный номер: {invent_no}")
        details = " ".join(details_parts)
        text = base if not details else base + " " + details
        request.session["flash_message"] = text
        return RedirectResponse(url="/assets", status_code=302)

    # Если это техника пользователя — просто объясняем статус, ничего не проводя.
    name = asset.get("sFullName") or asset.get("Name") or ""
    serial = asset.get("sSerialNo") or ""
    invent_no = (
        asset.get("sInventoryNo")
        or asset.get("sInventNo")
        or asset.get("InventoryNo")
        or ""
    )

    if _is_asset_inventoried(asset):
        # Уже проведён — говорим об этом.
        request.session["flash_message"] = (
            f"Актив ID {asset_id} уже учтён, инвентаризация по нему не требуется."
        )
    else:
        # Ещё не проведён — сообщаем, что это твой актив, и отсылаем к кнопке
        # «Инвентаризировать по фото» напротив него в списке.
        parts = [f"Это ваш актив (ID {asset_id})."]
        if name:
            parts.append(f"Наименование: {name}.")
        if serial:
            parts.append(f"Серийный номер: {serial}.")
        if invent_no:
            parts.append(f"Инвентарный номер: {invent_no}.")
        parts.append(
            "Инвентаризацию можно провести через кнопку «Инвентаризировать по фото» напротив этого актива."
        )
        request.session["flash_message"] = " ".join(parts)

    return RedirectResponse(url="/assets", status_code=302)


@app.post("/admin/scan-qr")
async def admin_scan_qr(request: Request, file: UploadFile = File(...)):
    """Админ: по фото с QR узнать, за кем закреплена техника (без ограничений по владельцу)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для загрузки."
        return RedirectResponse(url="/admin", status_code=302)

    image_bytes = await file.read()
    if not image_bytes:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/admin", status_code=302)

    qr_text = decode_qr_from_bytes(image_bytes)
    if not qr_text:
        request.session["flash_message"] = (
            "На изображении не найден QR-код. Убедитесь, что наклейка читаема "
            "и попала в кадр полностью."
        )
        return RedirectResponse(url="/admin", status_code=302)

    asset_id = extract_asset_id_from_qr_text(qr_text)
    if asset_id is None:
        request.session["flash_message"] = (
            "QR-код распознан, но не содержит ID актива. "
            "Попробуйте ещё раз или проверьте наклейку."
        )
        return RedirectResponse(url="/admin", status_code=302)

    try:
        client = _build_atracker_client()
        info, err = await client.get_asset_info(asset_id)
    except Exception:
        info, err = None, "service_error"

    if err or not info:
        request.session["flash_message"] = (
            f"Актив с ID {asset_id} не найден или недоступен в A‑Tracker."
        )
        return RedirectResponse(url="/admin", status_code=302)

    owner_fio = info.get("OwnerFio") or "—"
    name = info.get("sFullName") or info.get("Name") or ""
    serial = info.get("sSerialNo") or ""
    invent_no = (
        info.get("sInventoryNo")
        or info.get("sInventNo")
        or info.get("InventoryNo")
        or ""
    )

    parts = [f"Техника с ID {asset_id} закреплена за {owner_fio}."]
    if name:
        parts.append(f"Наименование: {name}.")
    if serial:
        parts.append(f"Серийный номер: {serial}.")
    if invent_no:
        parts.append(f"Инвентарный номер: {invent_no}.")
    request.session["flash_message"] = " ".join(parts)
    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/invent")
async def admin_invent(request: Request, file: UploadFile = File(...)):
    """Админ: по фото с QR найти актив, провести инвентаризацию и прикрепить снимок."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для загрузки."
        return RedirectResponse(url="/admin", status_code=302)

    content = await file.read()
    if not content:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/admin", status_code=302)

    qr_text = decode_qr_from_bytes(content)
    if not qr_text:
        request.session["flash_message"] = (
            "На изображении не найден QR-код. Убедитесь, что наклейка читаема "
            "и попала в кадр полностью."
        )
        return RedirectResponse(url="/admin", status_code=302)

    asset_id = extract_asset_id_from_qr_text(qr_text)
    if asset_id is None:
        request.session["flash_message"] = (
            "QR-код распознан, но не содержит ID актива. "
            "Попробуйте ещё раз или проверьте наклейку."
        )
        return RedirectResponse(url="/admin", status_code=302)

    try:
        client = _build_atracker_client()
        info, err = await client.get_asset_info(asset_id)
    except Exception:
        info, err = None, "service_error"

    if err or not info:
        request.session["flash_message"] = (
            f"Актив с ID {asset_id} не найден или недоступен в A‑Tracker."
        )
        return RedirectResponse(url="/admin", status_code=302)

    username_comment = (
        f"Username=photoQR-web-invent by {email} at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Пытаемся провести инвентаризацию и прикрепить фото.
    try:
        await client.mark_inventory(
            asset_id=asset_id,
            fio=fio,
            tg_user_id=0,
            tg_username=username_comment,
        )
        try:
            await client.upload_asset_file(
                asset_id=asset_id,
                file_name=file.filename,
                content_bytes=content,
                content_type=file.content_type or "image/jpeg",
            )
            request.session["flash_message"] = (
                f"Инвентаризация по активу ID {asset_id} проведена, снимок сохранён в A‑Tracker."
            )
        except Exception:
            request.session["flash_message"] = (
                f"Инвентаризация по активу ID {asset_id} проведена, "
                "но не удалось сохранить снимок в A‑Tracker."
            )
    except Exception:
        request.session["flash_message"] = (
            f"Не удалось провести инвентаризацию по активу ID {asset_id}. Попробуйте позже."
        )

    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/assets/{asset_id}/inventory-qr")
async def admin_asset_inventory_qr(request: Request, asset_id: int, file: UploadFile = File(...)):
    """Админ: инвентаризация конкретного актива по QR (с проверкой соответствия AssetId)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для инвентаризации."
        return RedirectResponse(url="/admin", status_code=302)

    content = await file.read()
    if not content:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/admin", status_code=302)

    qr_text = decode_qr_from_bytes(content)
    if not qr_text:
        request.session["flash_message"] = (
            "На изображении не найден QR-код. Убедитесь, что наклейка попала в кадр полностью."
        )
        return RedirectResponse(url="/admin", status_code=302)

    qr_asset_id = extract_asset_id_from_qr_text(qr_text)
    if qr_asset_id is None:
        request.session["flash_message"] = (
            "QR-код распознан, но не удалось определить ID актива. Попробуйте ещё раз."
        )
        return RedirectResponse(url="/admin", status_code=302)

    if qr_asset_id != asset_id:
        request.session["flash_message"] = (
            f"QR-код на фото относится к другому активу (ID {qr_asset_id}). "
            "Загрузите QR именно от выбранной техники."
        )
        return RedirectResponse(url="/admin", status_code=302)

    username_comment = (
        f"Username=photoQR-web-invent by {email} at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # Всё сошлось — проводим инвентаризацию и прикрепляем фото.
    try:
        client = _build_atracker_client()
        await client.mark_inventory(
            asset_id=asset_id,
            fio=fio,
            tg_user_id=0,
            tg_username=username_comment,
        )
        try:
            await client.upload_asset_file(
                asset_id=asset_id,
                file_name=file.filename,
                content_bytes=content,
                content_type=file.content_type or "image/jpeg",
            )
            request.session["flash_message"] = (
                f"Инвентаризация по активу ID {asset_id} проведена по QR, снимок сохранён в A‑Tracker."
            )
        except Exception:
            request.session["flash_message"] = (
                f"Инвентаризация по активу ID {asset_id} проведена по QR, "
                "но не удалось сохранить снимок в A‑Tracker."
            )
    except Exception:
        request.session["flash_message"] = (
            f"Не удалось провести инвентаризацию по активу ID {asset_id} через QR. Попробуйте позже."
        )

    return RedirectResponse(url="/admin", status_code=302)


@app.post("/admin/assets/{asset_id}/inventory-manual")
async def admin_asset_inventory_manual(request: Request, asset_id: int):
    """Админ: инвентаризация конкретного актива без QR, с явным комментарием."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Доступ в режим администратора ограничен."
        return RedirectResponse(url="/assets", status_code=302)

    comment = (
        f"Username=manual-web-invent by {email} at "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    try:
        client = _build_atracker_client()
        await client.mark_inventory(
            asset_id=asset_id,
            fio=fio,
            tg_user_id=0,
            tg_username=comment,
        )
        request.session["flash_message"] = (
            f"Инвентаризация по активу ID {asset_id} отмечена вручную (без QR)."
        )
    except Exception:
        request.session["flash_message"] = (
            f"Не удалось вручную отметить инвентаризацию по активу ID {asset_id}. Попробуйте позже."
        )

    return RedirectResponse(url="/admin", status_code=302)
@app.post("/assets/{asset_id}/photo")
async def upload_asset_photo(request: Request, asset_id: int, file: UploadFile = File(...)):
    """Прикрепляем дополнительные фото к активу (без проверок QR)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)

    # Перед загрузкой ещё раз убеждаемся, что актив принадлежит пользователю (или он админ).
    is_admin = bool(request.session.get("is_admin"))
    if not is_admin:
        try:
            client = _build_atracker_client()
            info, err = await client.get_asset_info(asset_id)
        except Exception:
            info, err = None, "service_error"
        if err or not info or _norm_fio(info.get("OwnerFio") or "") != _norm_fio(fio):
            request.session["flash_message"] = (
                "Нельзя прикрепить фото к чужому активу. "
                "Загрузите фото для техники, которая закреплена за вами."
            )
            return RedirectResponse(url="/assets", status_code=302)
    else:
        client = _build_atracker_client()

    if not file.filename:
        request.session["flash_message"] = "Не выбрано фото для загрузки."
        return RedirectResponse(url="/assets", status_code=302)

    content = await file.read()
    if not content:
        request.session["flash_message"] = "Файл пустой, попробуйте ещё раз."
        return RedirectResponse(url="/assets", status_code=302)

    try:
        await client.upload_asset_file(
            asset_id=asset_id,
            file_name=file.filename,
            content_bytes=content,
            content_type=file.content_type or "image/jpeg",
        )
        request.session["flash_message"] = "Фото отправлено в A‑Tracker."
    except Exception:
        request.session["flash_message"] = "Не удалось загрузить фото. Попробуйте позже."

    return RedirectResponse(url="/assets", status_code=302)


def create_app() -> FastAPI:
    """Фабрика приложения на случай, если потом будем собирать exe или подключать к IIS."""
    return app


if __name__ == "__main__":
    # Локальный запуск через python front_site/app.py
    import uvicorn

    uvicorn.run("front_site.app:app", host="127.0.0.1", port=8000, reload=True)

