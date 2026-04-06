from pathlib import Path
from datetime import datetime
from uuid import uuid4
import logging
import re
import zipfile
import xml.etree.ElementTree as ET

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
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
import errno
import shutil
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
    ATRACKER_LOCATIONS_LIST_SERVICE_ID,
    ATRACKER_CATEGORIES_LIST_SERVICE_ID,
    ATRACKER_TRANSFER_POSTING_SERVICE_ID,
    ATRACKER_ASSET_ADD_REQUEST_CREATE_SERVICE_ID,
    ATRACKER_ASSET_ADD_REQUEST_GET_SERVICE_ID,
    ATRACKER_PORTFOLIO_CREATE_SERVICE_ID,
    ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID,
    ATRACKER_REQUEST_ATTACH_SERVICE_ID,
    ADMIN_EMAILS,
    BYPASS_CODE_EMAILS,
    EMAIL_DOMAIN_ALLOWED,
    TRANSFER_NOTIFICATION_TO,
    TRANSFER_ADMIN_CONFIRM_EMAIL,
    WEB_PUBLIC_BASE_URL,
    WEB_ASSET_ADD_BUTTON_ENABLED,
    WEB_TRANSFER_ENABLED,
    reload_web_flags_from_disk,
    _CONFIG_PATH as CONFIG_PATH,
)
import config as _config_runtime
from atracker_client import ATrackerClient, inventory_number_from_atracker_dict

from .auth_web import (
    find_employee_by_input,
    create_code,
    check_code,
    send_code_email,
    employee_id_by_email,
)
from .mail_utils import send_email_with_attachment, send_email_with_attachments, send_plain_text_email
from .qr_utils import decode_qr_from_bytes, extract_asset_id_from_qr_text
from .transfers import create_transfer, get_transfer, list_transfers, update_transfer
from .asset_add_requests import (
    create_asset_add_request,
    get_asset_add_request,
    list_asset_add_requests,
    update_asset_add_request,
)

BASE_DIR = Path(__file__).resolve().parent
AUDIT_LOG_PATH = BASE_DIR / "logs" / "audit.log"
TRANSFER_UPLOADS_DIR = BASE_DIR / "uploads" / "transfers"
ASSET_ADD_UPLOADS_DIR = BASE_DIR / "uploads" / "asset_add_requests"
logger = logging.getLogger(__name__)

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

TRANSFER_STATUS_RU: dict[str, str] = {
    "pending_receiver": "Ожидает подтверждения получателя",
    "pending_scan": "Ожидает подписанного акта",
    "ready_for_admin": "Ожидает утверждения администратором",
    "completed": "Завершена",
    "cancelled": "Отменена",
}

ASSET_ADD_STATUS_RU: dict[str, str] = {
    "pending_review": "На проверке",
    "approved": "Подтверждена",
    "rejected": "Отклонена",
}


def _transfer_status_ru(status: str | None) -> str:
    if not status:
        return "—"
    return TRANSFER_STATUS_RU.get(str(status), str(status))


def _asset_add_status_ru(status: str | None) -> str:
    if not status:
        return "—"
    return ASSET_ADD_STATUS_RU.get(str(status), str(status))


def _operation_transfer_label(stored: object) -> str:
    """Текст про операцию для UI (из сохранённого operation_number / ответа API)."""
    if stored is None:
        return ""
    s = str(stored).strip()
    if not s:
        return ""
    m = re.search(r"operationId[\"']?\s*[:=]\s*(\d+)", s, re.I)
    if m:
        return f"Перемещение между пользователями ID {int(m.group(1))}"
    m2 = re.search(r"\b(\d{1,12})\b", s)
    if m2 and s.startswith("["):
        return f"Перемещение между пользователями ID {int(m2.group(1))}"
    if s.isdigit():
        return f"Перемещение между пользователями ID {int(s)}"
    return s


jinja_env.filters["transfer_status_ru"] = _transfer_status_ru
jinja_env.filters["asset_add_status_ru"] = _asset_add_status_ru
jinja_env.filters["operation_transfer_label"] = _operation_transfer_label


def render_template(name: str, context: dict, status_code: int = 200) -> HTMLResponse:
    template = jinja_env.get_template(name)
    ctx = dict(context or {})
    request = ctx.get("request")
    if request is not None:
        # Даём шаблонам привычную функцию url_for, как у StarletteTemplates.
        ctx["url_for"] = request.url_for
    # Шапка: «Заявки» видны, если включён хотя бы один контур (перемещение или добавление техники).
    ctx.setdefault(
        "show_requests_in_nav",
        WEB_TRANSFER_ENABLED or WEB_ASSET_ADD_BUTTON_ENABLED,
    )
    ctx.setdefault("transfer_requests_enabled", WEB_TRANSFER_ENABLED)
    ctx.setdefault("asset_add_button_enabled", WEB_ASSET_ADD_BUTTON_ENABLED)
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
        transfer_posting_service_id=ATRACKER_TRANSFER_POSTING_SERVICE_ID,
        locations_list_service_id=ATRACKER_LOCATIONS_LIST_SERVICE_ID,
        categories_list_service_id=ATRACKER_CATEGORIES_LIST_SERVICE_ID,
        asset_add_request_create_service_id=ATRACKER_ASSET_ADD_REQUEST_CREATE_SERVICE_ID,
        asset_add_request_get_service_id=ATRACKER_ASSET_ADD_REQUEST_GET_SERVICE_ID,
        portfolio_create_service_id=ATRACKER_PORTFOLIO_CREATE_SERVICE_ID,
        portfolio_update_service_id=ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID,
        request_attach_service_id=ATRACKER_REQUEST_ATTACH_SERVICE_ID,
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


def _extract_status_code(asset: dict) -> int | None:
    for key in ("seStatus", "Status", "status"):
        value = asset.get(key)
        if value is None:
            continue
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if text.isdigit():
            return int(text)
        m = re.search(r"\d+", text)
        if m:
            return int(m.group(0))
    return None


def _asset_allowed_for_transfer(asset: dict) -> bool:
    code = _extract_status_code(asset)
    if code is None:
        # Если сервис не вернул статус, не блокируем жёстко.
        return True
    return code == 2 and code not in {6, 11, 12}


def _str_or_dash(val: object) -> str:
    if val is None:
        return "—"
    s = str(val).strip()
    return s if s else "—"


def _asset_location_display(src: dict) -> str:
    """Человекочитаемое местоположение с карточки актива (поля в разных внедрениях различаются)."""
    if not isinstance(src, dict):
        return "—"
    nested = src.get("lt_lLocationId")
    if isinstance(nested, dict):
        for k in ("sFullName", "Name", "sName", "Title"):
            v = nested.get(k)
            if v is not None:
                s = str(v).strip()
                if s:
                    return s
    for key in (
        "sLocationName",
        "sLocation",
        "sFullNameLocation",
        "LocationName",
        "Location",
    ):
        v = src.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            fn = v.get("sFullName") or v.get("Name") or v.get("sName")
            if fn:
                s = str(fn).strip()
                if s:
                    return s
            continue
        s = str(v).strip()
        if s and s != "—":
            return s
    if nested is not None and not isinstance(nested, dict):
        s = str(nested).strip()
        if s and s != "—":
            return s
    return "—"


def _portfolio_location_id(src: dict) -> int | None:
    """ID локации на строке портфеля (список по ФИО часто даёт только число, без названия)."""
    if not isinstance(src, dict):
        return None
    nested = src.get("lt_lLocationId")
    if isinstance(nested, dict):
        for k in ("ID", "Id", "id"):
            v = nested.get(k)
            if v is None:
                continue
            try:
                i = int(v)
                if i > 0:
                    return i
            except (TypeError, ValueError):
                pass
    for k in ("lLocationId", "L_LocationId", "lLocationID"):
        v = src.get(k)
        if v is None or isinstance(v, dict):
            continue
        try:
            i = int(v)
            if i > 0:
                return i
        except (TypeError, ValueError):
            pass
    if nested is not None and not isinstance(nested, dict):
        s = str(nested).strip()
        if s.isdigit():
            try:
                i = int(s)
                if i > 0:
                    return i
            except ValueError:
                pass
    return None


def _asset_category_display(src: dict) -> str:
    """Человекочитаемая категория с карточки актива (lt_lCategoryId часто объект или ID)."""
    if not isinstance(src, dict):
        return "—"
    nested = src.get("lt_lCategoryId")
    if isinstance(nested, dict):
        for k in ("sFullName", "Name", "sName", "Title", "sCategoryName", "CategoryName"):
            v = nested.get(k)
            if v is not None:
                s = str(v).strip()
                if s:
                    return s
    for key in (
        "sCategoryName",
        "sCategory",
        "CategoryName",
        "Category",
        "sFullNameCategory",
        "lt_CategoryId",
    ):
        v = src.get(key)
        if v is None:
            continue
        if isinstance(v, dict):
            fn = v.get("sFullName") or v.get("Name") or v.get("sName") or v.get("sCategoryName")
            if fn:
                s = str(fn).strip()
                if s:
                    return s
            continue
        s = str(v).strip()
        if s and s not in ("—", "0"):
            return s
    if nested is not None and not isinstance(nested, dict):
        s = str(nested).strip()
        if s and s not in ("—", "0"):
            return s
    for k in ("lCategoryId", "LCategoryId", "l_lCategoryId"):
        v = src.get(k)
        if v is None or isinstance(v, dict):
            continue
        s = str(v).strip()
        if s and s != "0":
            return s
    return "—"


def _asset_row_for_transfer(src: dict, aid: int) -> dict:
    """Поля строки актива в соответствии с мастером A-Tracker (itamPortfolio)."""
    qty_raw = src.get("iQty")
    try:
        qty_i = int(qty_raw) if qty_raw is not None else 1
    except (TypeError, ValueError):
        qty_i = 1
    loc_disp = _asset_location_display(src)
    cat_disp = _asset_category_display(src)
    return {
        "id": aid,
        "name": src.get("sFullName") or src.get("Name") or f"ID {aid}",
        "serial": (src.get("sSerialNo") or "").strip(),
        "invent": inventory_number_from_atracker_dict(src),
        "category": cat_disp,
        "qty": qty_i,
        "location": loc_disp if loc_disp != "—" else "—",
    }


def _from_city_from_asset_rows(rows: list) -> str:
    """Как в мастере: список уникальных lt_lLocationId через запятую."""
    seen: list[str] = []
    for r in rows:
        loc = (r.get("location") or "").strip()
        if loc and loc != "—" and loc not in seen:
            seen.append(loc)
    return ", ".join(seen) if seen else "—"


def _employee_suggest_row(emp: dict) -> dict | None:
    if not isinstance(emp, dict):
        return None
    eid = emp.get("ID")
    fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
    login = (emp.get("sLoginName") or emp.get("sloginname") or "").strip()
    em = (emp.get("sEmail") or emp.get("semail") or "").strip()
    if not em:
        return None
    try:
        eid_i = int(eid) if eid is not None else None
    except (TypeError, ValueError):
        eid_i = None
    label = f"{fio or '—'} · {em}" if fio else em
    return {"id": eid_i, "fio": fio or "—", "email": em, "login": login, "label": label}


def _employee_suggest_matches(q: str, row: dict) -> bool:
    if not q:
        return True
    blob = " ".join(
        [
            row.get("fio") or "",
            row.get("email") or "",
            row.get("login") or "",
        ]
    ).lower()
    return q in blob


def _parse_location_service_row(raw: dict) -> tuple[int | None, str]:
    """Строка справочника локаций из A-Tracker (поля могут отличаться по внедрению)."""
    if not isinstance(raw, dict):
        return None, "—"
    lid = raw.get("ID") or raw.get("Id") or raw.get("id") or raw.get("lLocationId")
    for key in (
        "sName",
        "Name",
        "sFullName",
        "LocationName",
        "Title",
        "lt_lLocationId",
    ):
        v = raw.get(key)
        if v is None:
            continue
        s = str(v).strip()
        if s and s != "—":
            try:
                int_id = int(lid) if lid is not None and str(lid).strip() != "" else None
            except (TypeError, ValueError):
                int_id = None
            return int_id, s
    return None, "—"


def _location_row_numeric_id(raw: dict) -> int | None:
    v = raw.get("ID")
    if v is None:
        v = raw.get("Id") or raw.get("id")
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parent_id_from_location_directory_row(raw: dict) -> int | None:
    for k in (
        "lParentId",
        "ParentId",
        "lParentLocationId",
        "ParentID",
        "parentId",
        "lParent",
        "ID_Parent",
        "lParentID",
    ):
        v = raw.get(k)
        if v is None:
            continue
        try:
            pid = int(v)
            if pid > 0:
                return pid
        except (TypeError, ValueError):
            continue
    return None


def _location_directory_items_flat(raw_list: list) -> list[dict]:
    """Справочник локаций: цепочка «родитель / … / узел» по полю родителя (как в UI A-Tracker)."""
    rows = [r for r in (raw_list or []) if isinstance(r, dict)]
    by_id: dict[int, dict] = {}
    for r in rows:
        rid = _location_row_numeric_id(r)
        if rid is not None:
            by_id[rid] = r
    out: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for r in rows:
        rid = _location_row_numeric_id(r)
        if rid is None:
            continue
        parts: list[str] = []
        cur: dict | None = r
        for _ in range(64):
            if cur is None:
                break
            _, nm = _parse_location_service_row(cur)
            if nm and nm != "—":
                parts.append(nm)
            pid = _parent_id_from_location_directory_row(cur)
            if not pid:
                break
            cur = by_id.get(pid)
        parts.reverse()
        path = " / ".join(parts) if parts else ""
        if not path or path == "—":
            _, path = _parse_location_service_row(r)
        if path == "—":
            continue
        key = (rid, path)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": rid, "name": path})
    return out


async def _enrich_transfer_rows_from_asset_info(client: ATrackerClient, rows: list[dict]) -> None:
    """Дополняем строки категорией и номерами из сервиса «карточка актива» (полная запись)."""
    if not ATRACKER_ASSET_INFO_SERVICE_ID:
        return
    cat_id_to_name: dict[int, str] = {}
    if ATRACKER_CATEGORIES_LIST_SERVICE_ID:
        try:
            raw_cat = await client.get_categories()
            for item in _location_directory_items_flat(raw_cat or []):
                iid = item.get("id")
                nm = (item.get("name") or "").strip()
                try:
                    iid_i = int(iid) if iid is not None else None
                except (TypeError, ValueError):
                    iid_i = None
                if iid_i is not None and iid_i > 0 and nm:
                    cat_id_to_name[iid_i] = nm
        except Exception:
            cat_id_to_name = {}
    for row in rows:
        aid = row.get("id")
        if aid is None:
            continue
        try:
            info, err = await client.get_asset_info(int(aid))
        except Exception:
            continue
        if err or not isinstance(info, dict):
            continue
        cat = (info.get("category") or "").strip()
        cid = info.get("category_id")
        if (not cat or cat == "—") and cid and cat_id_to_name:
            try:
                cidi = int(cid)
            except (TypeError, ValueError):
                cidi = 0
            if cidi > 0:
                cat = cat_id_to_name.get(cidi) or cat
        if cat and cat != "—":
            row["category"] = cat
        inv = inventory_number_from_atracker_dict(info)
        if inv:
            row["invent"] = inv
        ser = (info.get("sSerialNo") or "").strip()
        if ser:
            row["serial"] = ser
        loc = (info.get("location") or "").strip()
        if loc:
            row["location"] = loc


def _act_groups_by_location(act_assets: list[dict]) -> list[dict]:
    """Несколько накладных: группы активов с одним местоположением у отправителя (порядок — как в заявке)."""
    from collections import OrderedDict

    buckets: OrderedDict[str, list] = OrderedDict()
    keys_order: list[str] = []
    for a in act_assets or []:
        raw = (a.get("location") or "").strip()
        key = raw if raw else "—"
        if key not in buckets:
            buckets[key] = []
            keys_order.append(key)
        buckets[key].append(a)
    groups: list[dict] = []
    for key in keys_order:
        assets = buckets[key]
        label = "" if key == "—" else key
        groups.append({"location_label": label, "assets": assets})
    return groups


def _location_from_asset_row(asset: dict) -> tuple[int | None, str]:
    """Место на карточке актива: имя + ID локации, если есть отдельное числовое поле."""
    name = _asset_location_display(asset)
    if name == "—":
        return None, "—"
    nested = asset.get("lt_lLocationId")
    if isinstance(nested, dict):
        vid = nested.get("ID") or nested.get("Id") or nested.get("id")
        if vid is not None and str(vid).strip().isdigit():
            try:
                return int(vid), name
            except ValueError:
                pass
    for k in ("lLocationId", "L_LocationId", "lLocationID"):
        v = asset.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s.isdigit():
            try:
                return int(s), name
            except ValueError:
                pass
    return None, name


def _location_suggestions_from_assets(
    raw_assets: list,
    asset_ids: list[int],
) -> list[dict]:
    by_id: dict[int, dict] = {}
    for a in raw_assets or []:
        if not isinstance(a, dict) or a.get("ID") is None:
            continue
        try:
            by_id[int(a["ID"])] = a
        except (TypeError, ValueError):
            continue
    out: list[dict] = []
    seen: set[tuple[int | None, str]] = set()
    for aid in asset_ids:
        a = by_id.get(aid)
        if not a:
            continue
        lid, name = _location_from_asset_row(a)
        if name == "—":
            continue
        key = (lid, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": lid, "name": name})
    return out


def _location_suggestions_from_all_assets(raw_assets: list) -> list[dict]:
    """Все уникальные локации с любых активов пользователя (если по выбранным ID пусто)."""
    seen: set[tuple[int | None, str]] = set()
    out: list[dict] = []
    for a in raw_assets or []:
        if not isinstance(a, dict) or a.get("ID") is None:
            continue
        lid, name = _location_from_asset_row(a)
        if name == "—":
            continue
        key = (lid, name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"id": lid, "name": name})
        if len(out) >= 100:
            break
    return out


def _extract_text_from_docx(content: bytes) -> str:
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            xml_data = zf.read("word/document.xml")
        root = ET.fromstring(xml_data)
        chunks = []
        for node in root.iter():
            if node.tag.endswith("}t") and node.text:
                chunks.append(node.text)
        return "\n".join(chunks)
    except Exception:
        return ""


def _extract_text_from_pdf(content: bytes) -> str:
    """Текст из PDF (слой текста). Скан без OCR сюда не попадёт — см. сообщение об ошибке ниже."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content))
        parts: list[str] = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                parts.append(t)
        return "\n".join(parts)
    except Exception:
        return ""


def _scan_text_contains(haystack: str, needle: str) -> bool:
    """Подстрока или все значимые слова (для ФИО с разными пробелами/переносами)."""
    needle = (needle or "").strip()
    if not needle:
        return True
    h = re.sub(r"\s+", " ", haystack.lower()).strip()
    n = re.sub(r"\s+", " ", needle.lower()).strip()
    if n in h:
        return True
    words = [w for w in n.split() if len(w) >= 2]
    if len(words) >= 2:
        return all(w in h for w in words)
    return False


def _verify_transfer_scan_content(transfer_obj: dict, filename: str, content: bytes) -> tuple[bool, str]:
    low_name = (filename or "").lower()
    if low_name.endswith(".docx"):
        text = _extract_text_from_docx(content)
    elif low_name.endswith(".pdf"):
        text = _extract_text_from_pdf(content)
    else:
        return False, "Поддерживаются только форматы PDF или DOCX для проверяемого акта."
    if not text or not text.strip():
        return False, (
            "Не удалось извлечь текст из файла. Если это скан без текстового слоя, "
            "сохраните накладную из Word как PDF «с текстом» или приложите DOCX."
        )
    checks = []
    from_fio = (transfer_obj.get("from_fio") or "").strip()
    to_fio = (transfer_obj.get("to_fio") or "").strip()
    org = (transfer_obj.get("organization_name") or "").strip()
    from_city = (transfer_obj.get("from_city") or "").strip()
    to_city = (transfer_obj.get("to_city") or "").strip()
    if from_fio:
        checks.append(from_fio)
    if to_fio:
        checks.append(to_fio)
    if org:
        checks.append(org)
    if from_city and from_city != "—":
        checks.append(from_city)
    if to_city:
        checks.append(to_city)
    for item in checks:
        if not _scan_text_contains(text, item):
            return False, f"В акте не найден обязательный реквизит: {item}"
    for asset in transfer_obj.get("assets", []):
        inv = (asset.get("invent") or "").strip()
        serial = (asset.get("serial") or "").strip()
        name = (asset.get("name") or "").strip()
        matched = False
        for key in (inv, serial, name):
            if key and _scan_text_contains(text, key):
                matched = True
                break
        if not matched:
            return False, (
                f"В акте не найден актив: {name or 'без названия'} "
                f"(инв. {inv or '-'}, серийный {serial or '-'})"
            )
    return True, ""


def _transfer_public_link(transfer_id: str) -> str:
    base = (WEB_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if base:
        return f"{base}/transfers/{transfer_id}"
    return ""


def _transfer_assets_lines(tr: dict, limit: int = 25) -> list[str]:
    lines: list[str] = []
    assets = tr.get("assets") or []
    for a in assets[:limit]:
        if isinstance(a, dict):
            lines.append(
                f"  — {a.get('name') or '—'} (инв. {a.get('invent') or '—'}, с/н {a.get('serial') or '—'})"
            )
    n = len(assets)
    if n > limit:
        lines.append(f"  … и ещё позиций: {n - limit}")
    return lines


def _transfer_brief_text(tr: dict) -> str:
    wb = (tr.get("waybill_number") or "").strip() or "—"
    lines = [
        f"Накладная: {wb}",
        f"Отправитель: {tr.get('from_fio') or '—'} <{tr.get('from_email') or ''}>",
        f"Получатель: {tr.get('to_fio') or '—'} <{tr.get('to_email') or ''}>",
        f"Организация: {tr.get('organization_name') or '—'}",
        f"Куда (место): {tr.get('receiver_location_name') or tr.get('to_city') or '—'}",
        "",
        "Позиции:",
        *_transfer_assets_lines(tr),
    ]
    return "\n".join(lines)


def _notify_transfer_new_to_recipient(transfer_id: str, tr: dict) -> None:
    """(1) Письмо получателю: назначена заявка на перемещение техники — зайдите на сайт."""
    to_email = (tr.get("to_email") or "").strip()
    if not to_email:
        return
    to_fio = (tr.get("to_fio") or "коллега").strip()
    from_fio = (tr.get("from_fio") or "Сотрудник").strip()
    from_email = (tr.get("from_email") or "").strip()
    wb = (tr.get("waybill_number") or "").strip()
    link = _transfer_public_link(transfer_id)
    subj = "Заявка на перемещение техники: вам назначена роль получателя"
    parts = [
        f"Здравствуйте, {to_fio}.",
        "",
        f"{from_fio} ({from_email}) инициировал перемещение техники вам.",
    ]
    if wb:
        parts.append(f"Номер накладной: {wb}.")
    parts.extend(
        [
            "",
            "Зайдите на сайт сервиса инвентаризации, откройте раздел «Заявки» "
            "и подтвердите или отклоните получение.",
        ]
    )
    if link:
        parts.extend(["", f"Ссылка на заявку: {link}"])
    body = "\n".join(parts)
    ok, err = send_plain_text_email([to_email], subj, body)
    if not ok:
        logger.warning("Письмо получателю о новой заявке на перемещение не отправлено: %s", err)


def _notify_transfer_scan_uploaded(transfer_id: str, tr: dict) -> None:
    """(3) Письмо администратору с вложением: тема «Подтвердить перемещение №…»."""
    to_raw = (TRANSFER_ADMIN_CONFIRM_EMAIL or "").strip()
    if not to_raw:
        to_raw = (TRANSFER_NOTIFICATION_TO or "").strip()
    path_str = tr.get("scan_file_path") or ""
    path = Path(path_str)
    if not path.is_file():
        update_transfer(
            transfer_id,
            {
                "notification_sent_at": "",
                "notification_last_error": "файл скана не найден для отправки",
            },
        )
        return
    if not to_raw:
        update_transfer(
            transfer_id,
            {
                "notification_sent_at": "",
                "notification_last_error": "не задан адрес (email.transfer_admin_confirm_email)",
            },
        )
        return
    addrs = [x.strip() for x in to_raw.replace(",", " ").split() if x.strip()]
    fn = tr.get("scan_original_name") or path.name
    wb = (tr.get("waybill_number") or "").strip() or transfer_id[:8]
    subj = f"Подтвердить перемещение №{wb}"
    body = (
        _transfer_brief_text(tr)
        + "\n\nПроверьте вложение и подтвердите в разделе «Заявки (админ)» на сайте.\n"
    )
    ok, err = send_email_with_attachment(addrs, subj, body, path, fn)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if ok:
        update_transfer(
            transfer_id,
            {"notification_sent_at": ts, "notification_last_error": ""},
        )
    else:
        update_transfer(
            transfer_id,
            {"notification_sent_at": "", "notification_last_error": err or "ошибка отправки почты"},
        )


def _notify_transfer_completed_both(transfer_id: str, tr: dict) -> None:
    """(4) Письма отправителю и получателю: перемещение выполнено."""
    wb = (tr.get("waybill_number") or "").strip()
    op = (tr.get("operation_number") or "").strip()
    subj = f"Заявка выполнена: №{wb}" if wb else "Заявка на перемещение выполнена"
    link = _transfer_public_link(transfer_id)
    extra = "\n\nЗаявка на перемещение техники выполнена."
    if op:
        extra += f"\nОперация: {op}"
    body = _transfer_brief_text(tr) + extra + (f"\n\n{link}" if link else "")
    addrs: list[str] = []
    for e in (tr.get("from_email"), tr.get("to_email")):
        e = (e or "").strip()
        if e:
            addrs.append(e)
    addrs = list(dict.fromkeys(addrs))
    if not addrs:
        return
    ok, err = send_plain_text_email(addrs, subj, body)
    if not ok:
        logger.warning("Письмо о завершении перемещения не отправлено: %s", err)


def _notify_sender_recipient_rejected(transfer_id: str, tr: dict) -> None:
    """(5) Уведомление отправителя: получатель отклонил перемещение техники."""
    to_email = (tr.get("from_email") or "").strip()
    if not to_email:
        return
    wb = (tr.get("waybill_number") or "").strip()
    subj = (
        f"Получатель отклонил заявку на перемещение: №{wb}"
        if wb
        else "Получатель отклонил заявку на перемещение техники"
    )
    link = _transfer_public_link(transfer_id)
    body = (
        f"Здравствуйте, {tr.get('from_fio') or 'коллега'}.\n\n"
        f"Получатель {tr.get('to_fio') or '—'} ({tr.get('to_email') or ''}) отклонил получение оборудования "
        f"по накладной {wb or '—'}.\n\n"
        + _transfer_brief_text(tr)
        + (f"\n\n{link}" if link else "")
    )
    ok, err = send_plain_text_email([to_email], subj, body)
    if not ok:
        logger.warning("Письмо отправителю об отклонении не отправлено: %s", err)


def _safe_upload_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        return "upload.bin"
    base = Path(raw).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base) or "upload.bin"


def _notify_asset_add_created(req: dict) -> tuple[bool, str]:
    to_raw = (TRANSFER_ADMIN_CONFIRM_EMAIL or "").strip() or (TRANSFER_NOTIFICATION_TO or "").strip()
    if not to_raw:
        return False, "не задан email администратора для уведомлений"
    addrs = [x.strip() for x in to_raw.replace(",", " ").split() if x.strip()]
    req_num = (req.get("request_number") or "").strip() or str(req.get("id") or "")
    subj = f"Заявка {req_num}: добавление техники"
    lines = [
        "Новая заявка на добавление техники.",
        "",
        f"Номер заявки: {req_num}",
        f"Создал: {req.get('requester_fio') or '—'} <{req.get('requester_email') or ''}>",
        f"Категория: {req.get('category_name') or '—'}",
        f"Наименование: {req.get('asset_name') or '—'}",
        f"Серийный номер: {req.get('serial_number') or '—'}",
        f"Инвентарный номер: {req.get('inventory_number') or '—'}",
        f"Комментарий: {req.get('comment') or '—'}",
        f"Фото: {len(req.get('photos') or [])} шт.",
        "",
        "Откройте раздел «Заявки (админ)» в вебе и подтвердите заявку.",
    ]
    photos = req.get("photos") or []
    attachments: list[tuple[Path, str]] = []
    for ph in photos:
        p = Path(ph.get("path") or "")
        if p.is_file():
            attachments.append((p, ph.get("name") or p.name))
    if attachments:
        return send_email_with_attachments(addrs, subj, "\n".join(lines), attachments)
    return send_plain_text_email(addrs, subj, "\n".join(lines))


def _notify_asset_add_approved(req: dict) -> None:
    to_email = (req.get("requester_email") or "").strip()
    if not to_email:
        return
    req_num = (req.get("request_number") or "").strip() or str(req.get("id") or "")
    subj = f"Заявка {req_num} подтверждена"
    body = "\n".join(
        [
            f"Здравствуйте, {req.get('requester_fio') or 'коллега'}.",
            "",
            "Ваша заявка на добавление техники подтверждена администратором.",
            f"Номер заявки: {req_num}",
            f"Номер в A-Tracker: {req.get('atracker_req_number') or '—'}",
            f"Статус: {_asset_add_status_ru(req.get('status'))}",
        ]
    )
    ok, err = send_plain_text_email([to_email], subj, body)
    if not ok:
        logger.warning("Письмо пользователю о подтверждении заявки на добавление не отправлено: %s", err)


def _notify_asset_add_rejected(req: dict) -> None:
    to_email = (req.get("requester_email") or "").strip()
    if not to_email:
        return
    req_num = (req.get("request_number") or "").strip() or str(req.get("id") or "")
    reject_comment = (req.get("reject_comment") or "").strip() or "Комментарий не указан."
    subj = f"Заявка {req_num} отклонена"
    body = "\n".join(
        [
            f"Здравствуйте, {req.get('requester_fio') or 'коллега'}.",
            "",
            "Ваша заявка на добавление техники отклонена администратором.",
            f"Номер заявки: {req_num}",
            "",
            "Причина отклонения:",
            reject_comment,
        ]
    )
    ok, err = send_plain_text_email([to_email], subj, body)
    if not ok:
        logger.warning("Письмо пользователю об отклонении заявки на добавление не отправлено: %s", err)


def _first_service_row(resp: dict | None) -> dict:
    if not isinstance(resp, dict):
        return {}
    rows = resp.get("data")
    if isinstance(rows, list) and rows:
        first = rows[0]
        if isinstance(first, dict):
            return first
    return {}


def _asset_row_match_score(
    row: dict,
    asset_name: str,
    serial_no: str,
    invent_no: str,
) -> int:
    def _norm(v: object) -> str:
        return str(v or "").strip().lower()

    rn = _norm(row.get("sFullName") or row.get("Name") or row.get("AssetName"))
    rs = _norm(
        row.get("sSerialNo")
        or row.get("SerialNo")
        or row.get("serialNo")
    )
    ri = _norm(
        row.get("sInventNumber")
        or row.get("sInventoryNo")
        or row.get("InventoryNo")
        or row.get("InventNumber")
        or row.get("inventNumber")
    )
    an = _norm(asset_name)
    sn = _norm(serial_no)
    inv = _norm(invent_no)

    score = 0
    if sn and rs == sn:
        score += 5
    if inv and ri == inv:
        score += 5
    if an and rn == an:
        score += 3
    return score


async def _find_created_asset_id_by_request_data(client: ATrackerClient, req: dict) -> int:
    """
    Фолбэк: если create вернул ошибку, но актив мог создаться,
    ищем наиболее подходящий актив у инициатора по данным заявки.
    """
    fio = (req.get("requester_fio") or "").strip()
    if not fio:
        return 0
    try:
        rows = await client.get_assets_by_fio(fio)
    except Exception:
        return 0
    if not isinstance(rows, list) or not rows:
        return 0

    asset_name = (req.get("asset_name") or "").strip()
    serial_no = (req.get("serial_number") or "").strip()
    invent_no = (req.get("inventory_number") or "").strip()

    best_id = 0
    best_score = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = int(row.get("ID") or row.get("Id") or row.get("assetId") or 0)
        if rid <= 0:
            continue
        score = _asset_row_match_score(row, asset_name, serial_no, invent_no)
        # При равном score берём запись с большим ID (обычно более свежая).
        if score > best_score or (score == best_score and rid > best_id):
            best_score = score
            best_id = rid
    # Порог 5: хотя бы serial или invent должен совпасть.
    return best_id if best_score >= 5 else 0


async def _finalize_asset_add_in_atracker(req: dict) -> tuple[dict | None, str]:
    """
    Финализация заявки в A-Tracker только через веб-админку:
    - создание/обновление актива;
    - загрузка фото в карточку актива.
    Возвращает (updated_req, error_message).
    """
    req_id = str(req.get("id") or "")
    if not req_id:
        return None, "Не передан локальный ID заявки."

    client = _build_atracker_client()

    # Нужны сервисы создания/обновления актива.
    if int(ATRACKER_PORTFOLIO_CREATE_SERVICE_ID or 0) <= 0 and int(ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID or 0) <= 0:
        return None, (
            "В config.ini не настроены сервисы создания/обновления актива "
            "(portfolio_create_service_id / portfolio_update_service_id)."
        )

    # Определяем пользователя актива: сначала requester email, потом пусто => ошибка.
    requester_email = (req.get("requester_email") or "").strip().lower()
    user_id = 0
    if requester_email:
        try:
            employees = await client.get_employees()
            user_id = int(employee_id_by_email(employees or [], requester_email) or 0)
        except Exception:
            user_id = 0
    if user_id <= 0:
        return None, "Не удалось определить пользователя актива по email инициатора."

    # Категория по названию (если справочник доступен).
    category_name = (req.get("category_name") or "").strip()
    category_id = int(req.get("atracker_category_id") or 0)
    if category_name and int(ATRACKER_CATEGORIES_LIST_SERVICE_ID or 0) > 0:
        try:
            cats = await client.get_categories()
            cat_name_norm = category_name.lower().strip()
            rows = [c for c in (cats or []) if isinstance(c, dict)]

            # 1) Точное совпадение
            for c in rows:
                full = str(c.get("sFullName") or c.get("Name") or c.get("sName") or "").strip()
                if full.lower() == cat_name_norm:
                    category_id = int(c.get("ID") or c.get("Id") or 0)
                    if category_id > 0:
                        break

            # 2) Мягкое совпадение (contains) — полезно при отличиях вроде "Ноутбук / ..."
            if category_id <= 0 and cat_name_norm:
                for c in rows:
                    full = str(c.get("sFullName") or c.get("Name") or c.get("sName") or "").strip()
                    full_norm = full.lower()
                    if full_norm and (cat_name_norm in full_norm or full_norm in cat_name_norm):
                        category_id = int(c.get("ID") or c.get("Id") or 0)
                        if category_id > 0:
                            break
        except Exception:
            category_id = 0

    # Локация: используем явный ID из заявки/админки.
    # Если ID не указан (или не найден), просто не заполняем lLocationId.
    location_id = int(req.get("atracker_location_id") or req.get("requester_location_id") or 0)

    asset_name = (req.get("asset_name") or "").strip()
    serial_no = (req.get("serial_number") or "").strip()
    invent_no = (req.get("inventory_number") or "").strip()
    comment = (req.get("comment") or "").strip()
    sd_number = (req.get("sd_request_number") or "").strip()
    req_num = (req.get("request_number") or "").strip()
    if req_num:
        req_tag = f"[REQ] {req_num}"
        if req_tag.lower() not in comment.lower():
            comment = (comment + "\n" + req_tag).strip() if comment else req_tag
    if sd_number:
        sd_tag = f"[SD] {sd_number}"
        if sd_tag.lower() not in comment.lower():
            comment = (comment + "\n" + sd_tag).strip() if comment else sd_tag

    chosen_portfolio_id = int(req.get("atracker_chosen_portfolio_id") or 0)
    final_asset_id = 0

    create_warning = ""
    try:
        if chosen_portfolio_id > 0 and int(ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID or 0) > 0:
            upd_payload = {
                "portfolioId": chosen_portfolio_id,
                "PortfolioId": chosen_portfolio_id,
                "lUserId": user_id,
                "LUserId": user_id,
                "sComment": comment,
                "SComment": comment,
            }
            if category_id > 0:
                upd_payload.update(
                    {
                        "categoryId": category_id,
                        "CategoryId": category_id,
                        "lCategoryId": category_id,
                        "LCategoryId": category_id,
                    }
                )
            if location_id > 0:
                upd_payload.update(
                    {
                        "locationId": location_id,
                        "LocationId": location_id,
                        "lLocationId": location_id,
                        "LLocationId": location_id,
                    }
                )
            try:
                await client.update_portfolio_asset(upd_payload)
            except Exception:
                # Fallback: минимальный update без комментария
                await client.update_portfolio_asset(
                    {
                        "portfolioId": chosen_portfolio_id,
                        "PortfolioId": chosen_portfolio_id,
                        "lUserId": user_id,
                        "LUserId": user_id,
                    }
                )
            final_asset_id = chosen_portfolio_id
        else:
            create_payload = {
                "assetName": asset_name,
                "AssetName": asset_name,
                "sFullName": asset_name,
                "SFullName": asset_name,
                "lUserId": user_id,
                "LUserId": user_id,
                "serialNo": serial_no,
                "SerialNo": serial_no,
                "sSerialNo": serial_no,
                "SSerialNo": serial_no,
                "inventNumber": invent_no,
                "InventNumber": invent_no,
                "sInventNumber": invent_no,
                "SInventNumber": invent_no,
                "categoryId": category_id,
                "CategoryId": category_id,
                "lCategoryId": category_id,
                "LCategoryId": category_id,
                "sComment": comment,
                "SComment": comment,
            }
            if location_id > 0:
                create_payload.update(
                    {
                        "locationId": location_id,
                        "LocationId": location_id,
                        "lLocationId": location_id,
                        "LLocationId": location_id,
                    }
                )
            # ВАЖНО: create вызываем строго один раз, иначе при частично-успешном
            # ответе A-Tracker можно получить дубль актива.
            create_resp = await client.create_portfolio_asset(create_payload)
            row = _first_service_row(create_resp)
            final_asset_id = int(
                row.get("portfolioId")
                or row.get("PortfolioId")
                or row.get("assetId")
                or row.get("AssetId")
                or row.get("ID")
                or 0
            )
            if final_asset_id <= 0:
                return None, "A-Tracker не вернул ID созданного актива."
    except Exception as ex:
        recovered_id = await _find_created_asset_id_by_request_data(client, req)
        if recovered_id > 0:
            final_asset_id = recovered_id
            create_warning = (
                "A-Tracker вернул ошибку на create/update, но актив найден по данным заявки: "
                f"ID={recovered_id}."
            )
        else:
            return None, f"Ошибка создания/обновления актива в A-Tracker: {ex}"

    # Если актив восстановили по recovery, пробуем дожать поля отдельным update
    # (в первую очередь категорию, которая может не проставиться при частичном create).
    recover_update_warning = ""
    if create_warning and final_asset_id > 0 and int(ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID or 0) > 0:
        patch_payload = {
            "portfolioId": final_asset_id,
            "PortfolioId": final_asset_id,
            "lUserId": user_id,
            "LUserId": user_id,
            "sComment": comment,
            "SComment": comment,
        }
        if category_id > 0:
            patch_payload.update(
                {
                    "categoryId": category_id,
                    "CategoryId": category_id,
                    "lCategoryId": category_id,
                    "LCategoryId": category_id,
                }
            )
        if serial_no:
            patch_payload.update(
                {
                    "serialNo": serial_no,
                    "SerialNo": serial_no,
                    "sSerialNo": serial_no,
                    "SSerialNo": serial_no,
                }
            )
        if invent_no:
            patch_payload.update(
                {
                    "inventNumber": invent_no,
                    "InventNumber": invent_no,
                    "sInventNumber": invent_no,
                    "SInventNumber": invent_no,
                }
            )
        try:
            await client.update_portfolio_asset(patch_payload)
        except Exception as ex:
            recover_update_warning = f"Не удалось дообновить поля актива после recovery: {ex}"

    # Независимо от сценария создания/поиска — отдельный "узкий" дожим категории.
    # По схеме itamPortfolio из xlsx категория хранится в lCategoryId (Ссылка).
    category_patch_warning = ""
    if final_asset_id > 0 and category_id > 0 and int(ATRACKER_PORTFOLIO_UPDATE_SERVICE_ID or 0) > 0:
        try:
            await client.update_portfolio_asset(
                {
                    "portfolioId": final_asset_id,
                    "PortfolioId": final_asset_id,
                    "lCategoryId": category_id,
                    "LCategoryId": category_id,
                    "categoryId": category_id,
                    "CategoryId": category_id,
                }
            )
        except Exception as ex:
            category_patch_warning = f"Не удалось проставить категорию отдельным update: {ex}"

    # Загружаем фото в карточку актива через существующий upload_doc сервис.
    upload_errors: list[str] = []
    for ph in (req.get("photos") or []):
        p = Path(ph.get("path") or "")
        if not p.is_file():
            upload_errors.append(f"Файл не найден: {ph.get('name') or p.name}")
            continue
        try:
            await client.upload_asset_file(
                asset_id=final_asset_id,
                file_name=ph.get("name") or p.name,
                content_bytes=p.read_bytes(),
                content_type="application/octet-stream",
            )
        except Exception as ex:
            upload_errors.append(f"{ph.get('name') or p.name}: {ex}")

    patch = {
        "final_asset_id": final_asset_id,
        # Статусы заявки в A-Tracker для финализации не используем:
        # фиксируем только результат обработки актива.
        "atracker_status_text": "Актив обработан (статус заявки A-Tracker не учитывается)",
        "atracker_attach_errors": upload_errors,
    }
    updated_req = update_asset_add_request(req_id, patch)
    if not updated_req:
        return None, "Не удалось обновить заявку после финализации."
    warnings: list[str] = []
    if create_warning:
        warnings.append(create_warning)
    if recover_update_warning:
        warnings.append(recover_update_warning)
    if category_patch_warning:
        warnings.append(category_patch_warning)
    if upload_errors:
        warnings.append("Часть фото не загрузилась: " + "; ".join(upload_errors[:3]))
    return updated_req, "; ".join(warnings)


def _build_mixed_transfer_rows(
    email: str,
    *,
    include_transfers: bool = True,
    include_asset_add: bool = True,
) -> list[dict]:
    email_low = (email or "").lower().strip()
    rows: list[dict] = []
    if include_transfers:
        for tr in reversed(list_transfers()):
            if (tr.get("from_email") or "").lower() == email_low or (tr.get("to_email") or "").lower() == email_low:
                row = dict(tr)
                row["kind"] = "transfer"
                rows.append(row)
    if include_asset_add:
        for req in reversed(list_asset_add_requests()):
            if (req.get("requester_email") or "").lower() != email_low:
                continue
            rows.append(
                {
                    "kind": "asset_add",
                    "id": req.get("id"),
                    "request_number": req.get("request_number") or "",
                    "status": req.get("status"),
                    "title": req.get("asset_name") or "Заявка на добавление техники",
                    "category_name": req.get("category_name") or "",
                    "created_at": req.get("created_at") or "",
                    "photos_count": len(req.get("photos") or []),
                    "atracker_req_number": req.get("atracker_req_number") or "",
                }
            )
    return rows


def _guess_mime_scan(file_name: str) -> str:
    fn = (file_name or "").lower()
    if fn.endswith(".pdf"):
        return "application/pdf"
    if fn.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


def _parse_operation_id_from_posting_response(resp: dict) -> tuple[int | None, str]:
    """Ответ сервиса: data может быть dict или list[dict] с ключом operationId (см. TransferPosting.cs)."""
    raw = resp.get("data")

    def _from_obj(obj: dict) -> tuple[int | None, str] | None:
        for k in ("operationId", "OperationId", "lOperationId", "ID"):
            v = obj.get(k)
            if v is not None:
                try:
                    oid = int(v)
                    return oid, str(oid)
                except (TypeError, ValueError):
                    pass
        return None

    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                got = _from_obj(item)
                if got:
                    return got
    if isinstance(raw, dict):
        got = _from_obj(raw)
        if got:
            return got
    if isinstance(raw, (int, float)):
        oid = int(raw)
        return oid, str(oid)
    if isinstance(raw, str) and raw.strip().isdigit():
        oid = int(raw.strip())
        return oid, str(oid)
    return None, ""


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
    email_bypass_code_emails: str = "",
    email_transfer_notification_to: str = "",
    email_transfer_admin_confirm_email: str = "",
    web_public_base_url: str = "",
    web_asset_add_button_enabled: bool = True,
    web_transfer_enabled: bool = True,
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
    if email_bypass_code_emails is not None:
        cfg.set("email", "bypass_code_emails", (email_bypass_code_emails or "").strip())
    if email_transfer_notification_to is not None:
        cfg.set("email", "transfer_notification_to", (email_transfer_notification_to or "").strip())
    if email_transfer_admin_confirm_email is not None:
        cfg.set(
            "email",
            "transfer_admin_confirm_email",
            (email_transfer_admin_confirm_email or "").strip(),
        )

    _ensure_section(cfg, "web")
    cfg.set("web", "public_base_url", (web_public_base_url or "").strip())
    cfg.set("web", "asset_add_button_enabled", "true" if web_asset_add_button_enabled else "false")
    cfg.set("web", "transfer_enabled", "true" if web_transfer_enabled else "false")

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


def _systemctl_bin() -> str:
    """Полный путь к systemctl; в sudoers NOPASSWD должен быть тот же путь (см. command -v systemctl)."""
    for candidate in ("/usr/bin/systemctl", "/bin/systemctl"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("systemctl")
    return found or "/usr/bin/systemctl"


def _sudo_bin() -> str:
    """Полный путь к sudo: в unit часто PATH только из venv — «sudo» из PATH не находится."""
    for candidate in ("/usr/bin/sudo", "/bin/sudo"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("sudo")
    return found or "/usr/bin/sudo"


def _restart_front_site_service() -> bool:
    """
    Пробуем перезапустить systemd-сервис front_site.service.
    На проде процесс обычно идёт от www-data: нужен sudoers NOPASSWD для
    «sudo -n systemctl restart front_site.service», иначе fallback на
    прямой systemctl (dev / запуск от root).
    В unit не полагаться на PATH: вызываем /usr/bin/sudo и полный путь к systemctl.
    Возвращаем True при успехе, False при явной ошибке.
    """
    if not sys.platform.startswith("linux"):
        return True
    ctl = _systemctl_bin()
    cmd_restart = [ctl, "restart", "front_site.service"]
    try:
        # 1) sudo без пароля (sudoers: www-data NOPASSWD: /usr/bin/systemctl restart front_site.service)
        r1 = subprocess.run(
            [_sudo_bin(), "-n", *cmd_restart],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if r1.returncode == 0:
            return True
        # 2) без sudo — сработает при запуске от root или политике systemd
        r2 = subprocess.run(
            cmd_restart,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return r2.returncode == 0
    except FileNotFoundError:
        return False
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

    email_bypass_code_emails = cfg.get("email", "bypass_code_emails", fallback="")
    email_transfer_notification_to = cfg.get("email", "transfer_notification_to", fallback="")
    email_transfer_admin_confirm_email = cfg.get("email", "transfer_admin_confirm_email", fallback="")
    web_public_base_url = ""
    web_asset_add_button_enabled = True
    web_transfer_enabled = True
    if cfg.has_section("web"):
        web_public_base_url = cfg.get("web", "public_base_url", fallback="")
        _aae = (cfg.get("web", "asset_add_button_enabled", fallback="true") or "true").strip().lower()
        web_asset_add_button_enabled = _aae not in ("0", "false", "no", "off")
        _te = (cfg.get("web", "transfer_enabled", fallback="true") or "true").strip().lower()
        web_transfer_enabled = _te not in ("0", "false", "no", "off")

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
            "email_bypass_code_emails": email_bypass_code_emails,
            "email_transfer_notification_to": email_transfer_notification_to,
            "email_transfer_admin_confirm_email": email_transfer_admin_confirm_email,
            "web_public_base_url": web_public_base_url,
            "web_asset_add_button_enabled": web_asset_add_button_enabled,
            "web_transfer_enabled": web_transfer_enabled,
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
    email_bypass_code_emails: str = Form(""),
    email_transfer_notification_to: str = Form(""),
    email_transfer_admin_confirm_email: str = Form(""),
    web_public_base_url: str = Form(""),
    web_asset_add_button_enabled: str = Form("0"),
    web_transfer_enabled: str = Form("0"),
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
            email_bypass_code_emails=email_bypass_code_emails,
            email_transfer_notification_to=email_transfer_notification_to,
            email_transfer_admin_confirm_email=email_transfer_admin_confirm_email,
            web_public_base_url=web_public_base_url,
            web_asset_add_button_enabled=(str(web_asset_add_button_enabled).strip() == "1"),
            web_transfer_enabled=(str(web_transfer_enabled).strip() == "1"),
        )
        reload_web_flags_from_disk()
        globals()["WEB_PUBLIC_BASE_URL"] = _config_runtime.WEB_PUBLIC_BASE_URL
        globals()["WEB_ASSET_ADD_BUTTON_ENABLED"] = _config_runtime.WEB_ASSET_ADD_BUTTON_ENABLED
        globals()["WEB_TRANSFER_ENABLED"] = _config_runtime.WEB_TRANSFER_ENABLED
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
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            request.session["flash_message"] = (
                f"Нет прав на запись в файл конфигурации ({CONFIG_PATH}). "
                "На сервере от root: "
                f"chown www-data:www-data {CONFIG_PATH}"
            )
        else:
            request.session["flash_message"] = (
                f"Не удалось сохранить настройки: {exc}"
            )
        _write_audit(
            request,
            action="settings_save_error",
            details=str(exc),
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

    email_norm = (email or "").strip().lower()
    if email_norm and email_norm in BYPASS_CODE_EMAILS:
        request.session["user_fio"] = fio
        request.session["user_email"] = email
        request.session["is_admin"] = email_norm in ADMIN_EMAILS
        _write_audit(
            request,
            action="login_success",
            details=f"user_fio={fio}\tbypass_code_email",
        )
        return RedirectResponse(url="/assets", status_code=302)

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
        invent = inventory_number_from_atracker_dict(a) or "-"
        inventoried = _is_asset_inventoried(a)
        if invent == "-" and int(ATRACKER_ASSET_INFO_SERVICE_ID or 0) > 0:
            try:
                info, err = await client.get_asset_info(asset_id)
                if not err and info:
                    inv2 = inventory_number_from_atracker_dict(info)
                    if inv2:
                        invent = inv2
            except Exception:
                pass
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
        "asset_add_button_enabled": WEB_ASSET_ADD_BUTTON_ENABLED,
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
                invent = inventory_number_from_atracker_dict(a) or "-"
                inventoried = _is_asset_inventoried(a)
                if invent == "-" and int(ATRACKER_ASSET_INFO_SERVICE_ID or 0) > 0:
                    try:
                        info, err = await client.get_asset_info(asset_id)
                        if not err and info:
                            inv2 = inventory_number_from_atracker_dict(info)
                            if inv2:
                                invent = inv2
                    except Exception:
                        pass
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
    invent = inventory_number_from_atracker_dict(info)
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
                invent_no = inventory_number_from_atracker_dict(info)
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
    invent_no = inventory_number_from_atracker_dict(asset)

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
    invent_no = inventory_number_from_atracker_dict(info)

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


@app.get("/transfer/start", response_class=HTMLResponse)
async def transfer_start_page(request: Request, asset_ids: str = ""):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)

    ids = []
    for chunk in (asset_ids or "").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            ids.append(int(chunk))
    ids = list(dict.fromkeys(ids))
    if not ids:
        request.session["flash_message"] = "Выберите хотя бы один актив для перемещения техники."
        return RedirectResponse(url="/assets", status_code=302)

    client = _build_atracker_client()
    try:
        raw_assets = await client.get_assets_by_fio(fio)
    except Exception:
        request.session["flash_message"] = "Не удалось загрузить ваши активы для оформления перемещения."
        return RedirectResponse(url="/assets", status_code=302)

    by_id = {}
    for a in raw_assets or []:
        if not isinstance(a, dict) or a.get("ID") is None:
            continue
        by_id[int(a["ID"])] = a

    selected_assets = []
    for aid in ids:
        src = by_id.get(aid)
        if not src or not _asset_allowed_for_transfer(src):
            continue
        selected_assets.append(_asset_row_for_transfer(src, aid))
    if not selected_assets:
        request.session["flash_message"] = (
            "Невозможно оформить перемещение для выбранных активов. Проверьте статус активов."
        )
        return RedirectResponse(url="/assets", status_code=302)

    await _enrich_transfer_rows_from_asset_info(client, selected_assets)

    context = {
        "request": request,
        "title": "Оформление перемещения техники",
        "assets": selected_assets,
        "asset_ids_value": ",".join(str(x["id"]) for x in selected_assets),
        "locations_directory_enabled": ATRACKER_LOCATIONS_LIST_SERVICE_ID > 0,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("transfer_start.html", context)


@app.get("/asset-add/start", response_class=HTMLResponse)
async def asset_add_start_page(request: Request):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = (
            "Подача заявки на добавление техники отключена в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    category_options: list[dict] = []
    location_options: list[dict] = []
    client = _build_atracker_client()
    try:
        raw_categories = await client.get_categories()
        seen_cat: set[int] = set()
        for c in raw_categories or []:
            cid = int(c.get("ID") or c.get("Id") or 0)
            name = str(c.get("sFullName") or c.get("Name") or c.get("sName") or c.get("CategoryName") or "").strip()
            if cid <= 0 or not name or cid in seen_cat:
                continue
            seen_cat.add(cid)
            category_options.append({"id": cid, "name": name})
        category_options.sort(key=lambda x: str(x.get("name") or "").lower())
    except Exception:
        category_options = []
    try:
        raw_loc = await client.get_locations()
        location_options = _location_directory_items_flat(raw_loc or [])
        for opt in location_options:
            nm = str(opt.get("name") or "").strip()
            if " / " in nm:
                opt["display_name"] = nm.split(" / ", 1)[1].strip() or nm
            else:
                opt["display_name"] = nm
        location_options.sort(key=lambda x: str(x.get("display_name") or x.get("name") or "").lower())
    except Exception:
        location_options = []
    context = {
        "request": request,
        "title": "Добавить технику",
        "message": request.session.pop("flash_message", None),
        "category_options": category_options or [],
        "location_options": location_options or [],
    }
    return render_template("asset_add_start.html", context)


@app.post("/asset-add/start")
async def asset_add_start_submit(
    request: Request,
    category_name: str = Form(""),
    requester_location_id: str = Form(""),
    asset_name: str = Form(""),
    serial_number: str = Form(""),
    inventory_number: str = Form(""),
    comment: str = Form(""),
    files: list[UploadFile] = File(...),
):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = (
            "Подача заявки на добавление техники отключена в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)

    category_name = (category_name or "").strip()
    requester_location_id_int = int(str(requester_location_id or "0").strip() or 0)
    requester_location_name = ""
    asset_name = (asset_name or "").strip()
    serial_number = (serial_number or "").strip()
    inventory_number = (inventory_number or "").strip()
    comment = (comment or "").strip()
    if not category_name or not asset_name:
        request.session["flash_message"] = "Заполните обязательные поля: категория и наименование."
        return RedirectResponse(url="/asset-add/start", status_code=302)

    if not files:
        request.session["flash_message"] = "Добавьте хотя бы одно фото техники."
        return RedirectResponse(url="/asset-add/start", status_code=302)
    if len(files) > 10:
        request.session["flash_message"] = "Можно загрузить не более 10 фото."
        return RedirectResponse(url="/asset-add/start", status_code=302)

    req_id = str(uuid4())
    req_dir = ASSET_ADD_UPLOADS_DIR / req_id
    req_dir.mkdir(parents=True, exist_ok=True)
    photos: list[dict] = []
    allowed = {".jpg", ".jpeg", ".png", ".webp"}
    for idx, f in enumerate(files, start=1):
        orig = _safe_upload_name(f.filename or f"photo_{idx}.jpg")
        ext = Path(orig).suffix.lower()
        if ext not in allowed:
            request.session["flash_message"] = "Допустимы только изображения: JPG, PNG, WEBP."
            return RedirectResponse(url="/asset-add/start", status_code=302)
        content = await f.read()
        if not content:
            request.session["flash_message"] = "Один из файлов пустой. Загрузите фото заново."
            return RedirectResponse(url="/asset-add/start", status_code=302)
        if len(content) > 10 * 1024 * 1024:
            request.session["flash_message"] = "Размер одного фото не должен превышать 10 МБ."
            return RedirectResponse(url="/asset-add/start", status_code=302)
        local_name = f"{idx:02d}_{orig}"
        path = req_dir / local_name
        path.write_bytes(content)
        photos.append({"name": orig, "path": str(path), "size": len(content)})

    def _first_service_row(resp: dict | None) -> dict:
        if not isinstance(resp, dict):
            return {}
        rows = resp.get("data")
        if isinstance(rows, list) and rows:
            first = rows[0]
            if isinstance(first, dict):
                return first
        return {}

    atracker_request_id: int | None = None
    atracker_req_number = ""
    atracker_status = 0
    atracker_status_text = ""
    atracker_integration_error = ""
    atracker_attach_errors: list[str] = []

    client = _build_atracker_client()
    if requester_location_id_int > 0:
        try:
            raw_loc = await client.get_locations()
            for item in _location_directory_items_flat(raw_loc or []):
                iid = int(item.get("id") or 0)
                if iid == requester_location_id_int:
                    requester_location_name = str(item.get("name") or "").strip()
                    break
        except Exception:
            requester_location_name = ""

    if int(ATRACKER_ASSET_ADD_REQUEST_CREATE_SERVICE_ID or 0) > 0:
        try:
            requester_employee_id = 0
            try:
                employees = await client.get_employees()
                requester_employee_id = int(employee_id_by_email(employees or [], email) or 0)
            except Exception:
                requester_employee_id = 0

            create_payload = {
                "requesterEmployeeId": requester_employee_id,
                "requesterFio": fio,
                "requesterEmail": email,
                "requesterLogin": "",
                "assetName": asset_name,
                "categoryName": category_name,
                "serialNo": serial_number,
                "inventoryNo": inventory_number,
                "comment": comment,
                "locationId": requester_location_id_int,
                "locationName": requester_location_name,
                "seType": 1,
                "statusOnCreate": 7,
                # Дублируем ключи в PascalCase для совместимости с разными обработчиками A-Tracker.
                "RequesterEmployeeId": requester_employee_id,
                "RequesterFio": fio,
                "RequesterEmail": email,
                "RequesterLogin": "",
                "AssetName": asset_name,
                "CategoryName": category_name,
                "SerialNo": serial_number,
                "InventoryNo": inventory_number,
                "Comment": comment,
                "LocationId": requester_location_id_int,
                "LocationName": requester_location_name,
                "SeType": 1,
                "StatusOnCreate": 7,
            }
            create_resp = await client.create_asset_add_request(create_payload)
            row = _first_service_row(create_resp)
            atracker_request_id = int(row.get("requestId") or row.get("ID") or 0) or None
            atracker_req_number = str(row.get("reqNumber") or row.get("sReqNumber") or "").strip()
            atracker_status = int(row.get("status") or 7)
            atracker_status_text = "Зарегистрирована" if atracker_status == 7 else str(atracker_status)

            if atracker_request_id and int(ATRACKER_REQUEST_ATTACH_SERVICE_ID or 0) > 0:
                for ph in photos:
                    p = Path(ph.get("path") or "")
                    if not p.is_file():
                        atracker_attach_errors.append(f"Файл не найден: {ph.get('name') or p.name}")
                        continue
                    try:
                        content_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                        await client.attach_document_to_request(
                            {
                                "requestId": int(atracker_request_id),
                                "fileName": ph.get("name") or p.name,
                                "contentBase64": content_b64,
                                "contentType": "application/octet-stream",
                                # Дубли в PascalCase для совместимости разных парсеров сервиса.
                                "RequestId": int(atracker_request_id),
                                "FileName": ph.get("name") or p.name,
                                "ContentBase64": content_b64,
                                "ContentType": "application/octet-stream",
                            }
                        )
                    except Exception as ex:
                        atracker_attach_errors.append(f"{ph.get('name') or p.name}: {ex}")
            elif atracker_request_id and int(ATRACKER_REQUEST_ATTACH_SERVICE_ID or 0) <= 0:
                atracker_attach_errors.append("request_attach_service_id не задан в config.ini")
        except Exception as ex:
            atracker_integration_error = str(ex)

    req = create_asset_add_request(
        {
            "id": req_id,
            "requester_fio": fio,
            "requester_email": email,
            "category_name": category_name,
            "asset_name": asset_name,
            "serial_number": serial_number,
            "inventory_number": inventory_number,
            "comment": comment,
            "requester_location_id": requester_location_id_int if requester_location_id_int > 0 else 0,
            "requester_location_name": requester_location_name,
            "photos": photos,
            "status": "pending_review",
            "atracker_request_id": atracker_request_id,
            "atracker_req_number": atracker_req_number,
            "atracker_status": atracker_status,
            "atracker_status_text": atracker_status_text,
            "atracker_integration_error": atracker_integration_error,
            "atracker_attach_errors": atracker_attach_errors,
        }
    )
    ok, err = _notify_asset_add_created(req)
    if not ok:
        update_asset_add_request(req_id, {"notify_admin_error": err or "ошибка отправки"})

    _write_audit(request, action="asset_add_create", details=f"request_id={req_id}; photos={len(photos)}")
    if atracker_integration_error:
        request.session["flash_message"] = (
            "Заявка создана в вебе и отправлена администратору, "
            f"но в A-Tracker не отправлена: {atracker_integration_error}"
        )
    elif atracker_attach_errors:
        request.session["flash_message"] = (
            "Заявка отправлена в A-Tracker, но часть вложений не загрузилась: "
            + "; ".join(atracker_attach_errors[:3])
        )
    else:
        request.session["flash_message"] = "Заявка на добавление техники создана и отправлена на проверку администратору."
    return RedirectResponse(url=f"/asset-add/{req_id}", status_code=302)


@app.get("/asset-add/{request_id}", response_class=HTMLResponse)
async def asset_add_detail_page(request: Request, request_id: str):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = (
            "Подача заявки на добавление техники отключена в настройках сайта."
        )
        return RedirectResponse(
            url="/admin/transfers" if is_admin else "/assets",
            status_code=302,
        )
    req = get_asset_add_request(request_id)
    if not req:
        request.session["flash_message"] = "Заявка на добавление техники не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    if not is_admin and (req.get("requester_email") or "").lower() != (email or "").lower():
        request.session["flash_message"] = "Недостаточно прав для просмотра заявки."
        return RedirectResponse(url="/transfers", status_code=302)
    category_options: list[dict] = []
    location_options: list[dict] = []
    if is_admin and req.get("status") == "pending_review":
        try:
            client = _build_atracker_client()
            cats = await client.get_categories()
            seen: set[int] = set()
            for c in cats or []:
                name = str(c.get("sFullName") or c.get("Name") or c.get("sName") or "").strip()
                cid = int(c.get("ID") or c.get("Id") or 0)
                if not name:
                    continue
                if cid <= 0 or cid in seen:
                    continue
                seen.add(cid)
                category_options.append({"id": cid, "name": name})
        except Exception:
            category_options = []
        try:
            raw_loc = await client.get_locations()
            location_options = _location_directory_items_flat(raw_loc or [])
            for opt in location_options:
                nm = str(opt.get("name") or "").strip()
                if " / " in nm:
                    opt["display_name"] = nm.split(" / ", 1)[1].strip() or nm
                else:
                    opt["display_name"] = nm
            location_options.sort(key=lambda x: str(x.get("display_name") or x.get("name") or "").lower())
        except Exception:
            location_options = []

    context = {
        "request": request,
        "title": f"Добавление техники {req.get('request_number') or request_id[:8]}",
        "req": req,
        "is_admin": is_admin,
        "category_options": category_options,
        "location_options": location_options,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("asset_add_detail.html", context)


@app.get("/asset-add/{request_id}/photo/{photo_idx}")
async def asset_add_photo_file(request: Request, request_id: str, photo_idx: int):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not fio or not email:
        return Response(status_code=401)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        return Response(status_code=404)
    req = get_asset_add_request(request_id)
    if not req:
        return Response(status_code=404)
    if not is_admin and (req.get("requester_email") or "").lower() != (email or "").lower():
        return Response(status_code=403)
    photos = req.get("photos") or []
    if photo_idx < 0 or photo_idx >= len(photos):
        return Response(status_code=404)
    ph = photos[photo_idx] if isinstance(photos[photo_idx], dict) else {}
    p = Path(ph.get("path") or "")
    if not p.is_file():
        return Response(status_code=404)
    suffix = p.suffix.lower()
    media = "application/octet-stream"
    if suffix in (".jpg", ".jpeg"):
        media = "image/jpeg"
    elif suffix == ".png":
        media = "image/png"
    elif suffix == ".webp":
        media = "image/webp"
    return FileResponse(
        p,
        media_type=media,
        filename=ph.get("name") or p.name,
        content_disposition_type="inline",
    )


@app.get("/api/transfer/employees")
async def api_transfer_employees(request: Request, q: str = ""):
    if not request.session.get("user_email"):
        return JSONResponse({"items": [], "error": "unauthorized"}, status_code=401)
    if not WEB_TRANSFER_ENABLED:
        return JSONResponse({"items": [], "error": "disabled"}, status_code=403)
    client = _build_atracker_client()
    try:
        employees = await client.get_employees()
    except Exception:
        return JSONResponse({"items": []})
    qn = (q or "").strip().lower()
    items: list[dict] = []
    for emp in employees or []:
        row = _employee_suggest_row(emp)
        if not row:
            continue
        if not _employee_suggest_matches(qn, row):
            continue
        items.append(row)
        if len(items) >= 60:
            break
    return JSONResponse({"items": items})


@app.get("/api/transfer/locations")
async def api_transfer_locations(request: Request, q: str = "", asset_ids: str = ""):
    if not request.session.get("user_email"):
        return JSONResponse({"items": [], "error": "unauthorized"}, status_code=401)
    if not WEB_TRANSFER_ENABLED:
        return JSONResponse({"items": [], "error": "disabled"}, status_code=403)
    fio = request.session.get("user_fio") or ""
    qn = (q or "").strip().lower()
    id_chunks = [x.strip() for x in (asset_ids or "").split(",") if x.strip().isdigit()]
    want_ids = [int(x) for x in id_chunks]

    client = _build_atracker_client()
    raw_assets: list = []
    if fio:
        try:
            raw_assets = await client.get_assets_by_fio(fio)
        except Exception:
            raw_assets = []

    items: list[dict] = []
    if ATRACKER_LOCATIONS_LIST_SERVICE_ID > 0:
        try:
            raw = await client.get_locations()
            items = _location_directory_items_flat(raw or [])
        except Exception:
            items = []

    if not items:
        items = _location_suggestions_from_assets(raw_assets, want_ids)
    if not items:
        items = _location_suggestions_from_all_assets(raw_assets)

    if qn:
        items = [x for x in items if qn in (x.get("name") or "").lower()]

    seen: set[tuple] = set()
    uniq: list[dict] = []
    for it in items:
        key = (it.get("id"), it.get("name"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)
        if len(uniq) >= 80:
            break
    return JSONResponse({"items": uniq})


@app.post("/transfer/start")
async def transfer_start_submit(
    request: Request,
    asset_ids: str = Form(""),
    recipient_input: str = Form(""),
    recipient_email: str = Form(""),
    organization: str = Form(""),
    receiver_location_name: str = Form(""),
    receiver_location_id: str = Form(""),
):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)

    raw_ids = [x.strip() for x in (asset_ids or "").split(",")]
    ids = [int(x) for x in raw_ids if x.isdigit()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        request.session["flash_message"] = "Не выбраны активы для перемещения техники."
        return RedirectResponse(url="/assets", status_code=302)

    recipient_input = (recipient_input or "").strip()
    recipient_email_clean = (recipient_email or "").strip()
    if not organization:
        request.session["flash_message"] = "Выберите организацию для акта."
        return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)

    receiver_location_name = (receiver_location_name or "").strip()
    if not receiver_location_name:
        request.session["flash_message"] = "Укажите местоположение получателя (поиск по справочнику A-Tracker)."
        return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)

    org_map = {"1": "ООО АСГ", "2": "ООО РКЗ", "3": "ООО ОВП"}
    org_name = org_map.get(organization)
    if not org_name:
        request.session["flash_message"] = "Некорректное значение организации."
        return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)

    client = _build_atracker_client()
    try:
        employees = await client.get_employees()
        lookup = recipient_email_clean or recipient_input
        if not lookup:
            request.session["flash_message"] = "Укажите получателя (начните вводить ФИО, логин или почту и выберите из списка)."
            return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)
        to_fio, to_email, error = find_employee_by_input(employees, lookup, EMAIL_DOMAIN_ALLOWED)
        if error:
            request.session["flash_message"] = error
            return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)
        raw_assets = await client.get_assets_by_fio(fio)
    except Exception:
        request.session["flash_message"] = "Не удалось создать заявку на перемещение техники. Попробуйте позже."
        return RedirectResponse(url=f"/transfer/start?asset_ids={','.join(map(str, ids))}", status_code=302)

    by_id = {}
    for a in raw_assets or []:
        if not isinstance(a, dict) or a.get("ID") is None:
            continue
        by_id[int(a["ID"])] = a

    selected_assets = []
    for aid in ids:
        src = by_id.get(aid)
        if not src or not _asset_allowed_for_transfer(src):
            continue
        selected_assets.append(_asset_row_for_transfer(src, aid))
    if not selected_assets:
        request.session["flash_message"] = "Не удалось подтвердить список активов для перемещения техники."
        return RedirectResponse(url="/assets", status_code=302)

    loc_raw = (receiver_location_id or "").strip()
    receiver_loc_id = None
    if loc_raw.isdigit():
        receiver_loc_id = int(loc_raw)

    from_eid = employee_id_by_email(employees, email)
    to_eid = employee_id_by_email(employees, to_email or "")

    transfer_id = str(uuid4())
    from_city_str = _from_city_from_asset_rows(selected_assets)
    tr_new = create_transfer(
        {
            "id": transfer_id,
            "from_fio": fio,
            "from_email": email,
            "to_fio": to_fio or "",
            "to_email": to_email or "",
            "organization_code": organization,
            "organization_name": org_name,
            "from_city": from_city_str,
            "to_city": receiver_location_name,
            "receiver_location_name": receiver_location_name,
            "from_employee_id": from_eid,
            "to_employee_id": to_eid,
            "receiver_location_id": receiver_loc_id,
            "assets": selected_assets,
            "status": "pending_receiver",
        }
    )
    _notify_transfer_new_to_recipient(transfer_id, tr_new)
    _write_audit(
        request,
        action="transfer_start",
        details=f"transfer_id={transfer_id}; assets={','.join(str(a['id']) for a in selected_assets)}",
    )
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


@app.get("/transfers", response_class=HTMLResponse)
async def transfers_list_page(request: Request):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED and not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = "Раздел заявок отключён в настройках сайта."
        return RedirectResponse(url="/assets", status_code=302)
    visible = _build_mixed_transfer_rows(
        email,
        include_transfers=WEB_TRANSFER_ENABLED,
        include_asset_add=WEB_ASSET_ADD_BUTTON_ENABLED,
    )
    context = {
        "request": request,
        "title": "Заявки",
        "items": visible,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("transfers_list.html", context)


async def _compute_from_city_display(tr: dict, client: ATrackerClient) -> str:
    """«Откуда»: актуальные места выбранных активов у отправителя (как в A-Tracker сейчас)."""
    from_fio = (tr.get("from_fio") or "").strip()
    assets = tr.get("assets") or []
    ids: list[int] = []
    for a in assets:
        if isinstance(a, dict) and a.get("id") is not None:
            try:
                ids.append(int(a["id"]))
            except (TypeError, ValueError):
                pass
    if not ids or not from_fio:
        return (tr.get("from_city") or "").strip() or "—"
    try:
        raw_assets = await client.get_assets_by_fio(from_fio)
    except Exception:
        return (tr.get("from_city") or "").strip() or "—"
    by_id: dict[int, dict] = {}
    for a in raw_assets or []:
        if isinstance(a, dict) and a.get("ID") is not None:
            try:
                by_id[int(a["ID"])] = a
            except (TypeError, ValueError):
                continue

    id_to_name: dict[int, str] = {}
    if ATRACKER_LOCATIONS_LIST_SERVICE_ID > 0:
        try:
            raw_loc = await client.get_locations()
            for item in _location_directory_items_flat(raw_loc or []):
                iid = item.get("id")
                nm = (item.get("name") or "").strip()
                try:
                    iid_i = int(iid) if iid is not None else None
                except (TypeError, ValueError):
                    iid_i = None
                if iid_i is not None and iid_i > 0 and nm:
                    id_to_name[iid_i] = nm
        except Exception:
            pass

    rows: list[dict] = []
    for aid in ids:
        src = by_id.get(aid)
        if not src:
            continue
        row = _asset_row_for_transfer(src, aid)
        loc = (row.get("location") or "").strip() or "—"
        pid = _portfolio_location_id(src)
        if pid is not None and pid in id_to_name:
            loc = id_to_name[pid]
        elif loc == "—" and ATRACKER_ASSET_INFO_SERVICE_ID:
            try:
                info, err = await client.get_asset_info(int(aid))
                if not err and isinstance(info, dict):
                    alt = (info.get("location") or "").strip()
                    if alt:
                        loc = alt
            except Exception:
                pass
        row["location"] = loc
        rows.append(row)
    if not rows:
        return (tr.get("from_city") or "").strip() or "—"
    return _from_city_from_asset_rows(rows)


@app.get("/transfers/{transfer_id}", response_class=HTMLResponse)
async def transfer_detail_page(request: Request, transfer_id: str):
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка на перемещение техники не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    email_low = (email or "").lower()
    is_admin = bool(request.session.get("is_admin"))
    if not is_admin and email_low not in {(tr.get("from_email") or "").lower(), (tr.get("to_email") or "").lower()}:
        request.session["flash_message"] = "Недостаточно прав для просмотра этой заявки."
        return RedirectResponse(url="/transfers", status_code=302)
    client = _build_atracker_client()
    try:
        from_city_display = await _compute_from_city_display(tr, client)
    except Exception:
        from_city_display = (tr.get("from_city") or "").strip() or "—"
    context = {
        "request": request,
        "title": f"Заявка на перемещение {(tr.get('waybill_number') or '').strip() or '#' + transfer_id[:8]}",
        "tr": tr,
        "from_city_display": from_city_display,
        "is_admin": is_admin,
        "is_recipient": email_low == (tr.get("to_email") or "").lower(),
        "is_sender": email_low == (tr.get("from_email") or "").lower(),
        "message": request.session.pop("flash_message", None),
    }
    return render_template("transfer_detail.html", context)


@app.get("/transfers/{transfer_id}/act", response_class=HTMLResponse)
async def transfer_act_print(request: Request, transfer_id: str):
    """Печать накладной на перемещение (форма «Накладная на перемещение»)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка на перемещение техники не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    email_low = (email or "").lower()
    is_admin = bool(request.session.get("is_admin"))
    if not is_admin and email_low not in {(tr.get("from_email") or "").lower(), (tr.get("to_email") or "").lower()}:
        request.session["flash_message"] = "Недостаточно прав для просмотра акта."
        return RedirectResponse(url="/transfers", status_code=302)
    wb = (tr.get("waybill_number") or "").strip()
    op_num = wb if wb else "—"
    act_date = datetime.now().strftime("%d.%m.%Y")
    created = tr.get("created_at") or ""
    if created and len(created) >= 10:
        try:
            parts = created[:10].split("-")
            if len(parts) == 3:
                act_date = f"{parts[2]}.{parts[1]}.{parts[0]}"
        except Exception:
            pass
    act_assets: list[dict] = [dict(x) for x in (tr.get("assets") or [])]
    if act_assets and ATRACKER_ASSET_INFO_SERVICE_ID:
        client = _build_atracker_client()
        try:
            await _enrich_transfer_rows_from_asset_info(client, act_assets)
        except Exception:
            pass
    act_groups = _act_groups_by_location(act_assets)
    if not act_groups:
        act_groups = [{"location_label": "", "assets": act_assets or []}]
    context = {
        "request": request,
        "title": "Накладная на перемещение",
        "tr": tr,
        "act_date": act_date,
        "operation_display": op_num,
        "act_assets": act_assets,
        "act_groups": act_groups,
    }
    return render_template("transfer_act_print.html", context)


@app.get("/transfers/{transfer_id}/signed-scan")
async def transfer_signed_scan_file(request: Request, transfer_id: str):
    """Подписанный акт для просмотра админом (PDF inline; остальное — скачивание)."""
    fio = request.session.get("user_fio")
    email = request.session.get("user_email")
    if not fio or not email:
        return Response(status_code=401)
    if not WEB_TRANSFER_ENABLED:
        return Response(status_code=404)
    tr = get_transfer(transfer_id)
    if not tr:
        return Response(status_code=404)
    email_low = (email or "").lower()
    is_admin = bool(request.session.get("is_admin"))
    if not is_admin and email_low not in {(tr.get("from_email") or "").lower(), (tr.get("to_email") or "").lower()}:
        return Response(status_code=403)
    path = Path(tr.get("scan_file_path") or "")
    if not path.is_file():
        return Response(status_code=404)
    fname = tr.get("scan_original_name") or path.name
    media = _guess_mime_scan(fname)
    disp = "inline" if fname.lower().endswith(".pdf") else "attachment"
    return FileResponse(
        path,
        media_type=media,
        filename=fname,
        content_disposition_type=disp,
    )


@app.post("/transfers/{transfer_id}/confirm")
async def transfer_confirm(request: Request, transfer_id: str):
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    if (tr.get("to_email") or "").lower() != (email or "").lower():
        request.session["flash_message"] = "Подтверждение доступно только получателю."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if tr.get("status") != "pending_receiver":
        request.session["flash_message"] = "Эта заявка уже подтверждена или закрыта."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    update_transfer(transfer_id, {"status": "pending_scan"})
    _write_audit(request, action="transfer_confirmed_by_recipient", details=f"transfer_id={transfer_id}")
    request.session["flash_message"] = (
        "Получение подтверждено. Загрузите подписанный акт."
    )
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


@app.post("/transfers/{transfer_id}/reject")
async def transfer_reject(request: Request, transfer_id: str):
    """Получатель отклоняет перемещение техники (ожидание подтверждения). Уведомление отправителю — (5)."""
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    if (tr.get("to_email") or "").lower() != (email or "").lower():
        request.session["flash_message"] = "Отклонить может только получатель."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if tr.get("status") != "pending_receiver":
        request.session["flash_message"] = "Эта заявка уже обработана или закрыта."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    update_transfer(
        transfer_id,
        {
            "status": "cancelled",
            "cancel_reason": "Отклонено получателем",
        },
    )
    tr_after = get_transfer(transfer_id) or {}
    _notify_sender_recipient_rejected(transfer_id, tr_after)
    _write_audit(request, action="transfer_rejected_by_recipient", details=f"transfer_id={transfer_id}")
    request.session["flash_message"] = "Вы отклонили получение техники. Отправитель уведомлён по почте."
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


@app.post("/transfers/{transfer_id}/upload-scan")
async def transfer_upload_scan(request: Request, transfer_id: str, file: UploadFile = File(...)):
    """Загрузка подписанного акта. Шаг (2) в цепочке оповещений — писем не шлём."""
    email = request.session.get("user_email")
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    email_low = (email or "").lower()
    participants = {(tr.get("from_email") or "").lower(), (tr.get("to_email") or "").lower()}
    if email_low not in participants:
        request.session["flash_message"] = "Загрузка акта доступна только участникам перемещения техники."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if tr.get("status") not in {"pending_scan"}:
        request.session["flash_message"] = "Скан акта можно загрузить только после подтверждения получателем."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if not file.filename:
        request.session["flash_message"] = "Выберите файл акта."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    content = await file.read()
    if not content:
        request.session["flash_message"] = "Файл акта пустой."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    ok, err = _verify_transfer_scan_content(tr, file.filename, content)
    if not ok:
        request.session["flash_message"] = err
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    TRANSFER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{transfer_id}_{ts}_{Path(file.filename).name}"
    path = TRANSFER_UPLOADS_DIR / safe_name
    with path.open("wb") as f:
        f.write(content)
    update_transfer(
        transfer_id,
        {
            "status": "ready_for_admin",
            "scan_file_path": str(path),
            "scan_original_name": file.filename,
            "scan_verified": True,
        },
    )
    tr_after = get_transfer(transfer_id) or {}
    _notify_transfer_scan_uploaded(transfer_id, tr_after)
    _write_audit(request, action="transfer_scan_uploaded", details=f"transfer_id={transfer_id}")
    request.session["flash_message"] = (
        "Скан акта загружен и прошёл проверку. Заявка готова к завершению администратором."
    )
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


@app.post("/transfers/{transfer_id}/cancel")
async def transfer_cancel(request: Request, transfer_id: str):
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/assets", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/transfers", status_code=302)
    if tr.get("status") in {"completed", "cancelled"}:
        request.session["flash_message"] = "Эта заявка уже завершена или отменена."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    is_sender = (tr.get("from_email") or "").lower() == (email or "").lower()
    is_recipient = (tr.get("to_email") or "").lower() == (email or "").lower()
    if not is_admin and is_recipient:
        request.session["flash_message"] = "Отменить перемещение может отправитель или администратор."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if not is_admin and not is_sender:
        request.session["flash_message"] = "Недостаточно прав для отмены перемещения техники."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    if not is_admin and is_sender and tr.get("status") != "pending_receiver":
        request.session["flash_message"] = (
            "Отмена доступна только до ответа получателя. Дальше — через администратора."
        )
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)
    update_transfer(transfer_id, {"status": "cancelled"})
    _write_audit(request, action="transfer_cancelled", details=f"transfer_id={transfer_id}")
    request.session["flash_message"] = "Заявка на перемещение техники отменена."
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


@app.get("/admin/transfers", response_class=HTMLResponse)
async def admin_transfers_page(request: Request):
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Раздел доступен только администраторам."
        return RedirectResponse(url="/assets", status_code=302)
    if not WEB_TRANSFER_ENABLED and not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = "Раздел заявок отключён в настройках сайта."
        return RedirectResponse(url="/admin", status_code=302)
    context = {
        "request": request,
        "title": "Заявки (админ)",
        "transfers": list(reversed(list_transfers())) if WEB_TRANSFER_ENABLED else [],
        "asset_add_requests": (
            list(reversed(list_asset_add_requests())) if WEB_ASSET_ADD_BUTTON_ENABLED else []
        ),
        "asset_add_button_enabled": WEB_ASSET_ADD_BUTTON_ENABLED,
        "transfer_requests_enabled": WEB_TRANSFER_ENABLED,
        "message": request.session.pop("flash_message", None),
    }
    return render_template("admin_transfers.html", context)


@app.post("/admin/asset-add/{request_id}/approve")
async def admin_asset_add_approve(
    request: Request,
    request_id: str,
    sd_request_number: str = Form(""),
    location_id: str = Form(""),
    category_id: str = Form(""),
    category_name: str = Form(""),
    asset_name: str = Form(""),
    serial_number: str = Form(""),
    inventory_number: str = Form(""),
    comment: str = Form(""),
):
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Раздел доступен только администраторам."
        return RedirectResponse(url="/assets", status_code=302)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = (
            "Подача заявки на добавление техники отключена в настройках сайта."
        )
        return RedirectResponse(url="/admin/transfers", status_code=302)
    req = get_asset_add_request(request_id)
    if not req:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/admin/transfers", status_code=302)
    already_approved = req.get("status") == "approved"
    has_final_asset = int(req.get("final_asset_id") or 0) > 0
    # Разрешаем повторное "подтверждение" только если заявка approved,
    # но карточка актива так и не была создана/привязана (legacy/сбойный кейс).
    if already_approved and has_final_asset:
        request.session["flash_message"] = "Заявка уже подтверждена."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)

    sd_request_number = (sd_request_number or "").strip()
    location_id_int = int(str(location_id or "0").strip() or 0)
    location_name = ""
    category_name = (category_name or "").strip()
    category_id_int = int(str(category_id or "0").strip() or 0)
    asset_name = (asset_name or "").strip()
    serial_number = (serial_number or "").strip()
    inventory_number = (inventory_number or "").strip()
    comment = (comment or "").strip()
    if not sd_request_number:
        request.session["flash_message"] = "Перед подтверждением обязательно укажите номер заявки SD."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)
    if category_id_int <= 0 and (not category_name):
        request.session["flash_message"] = "Для подтверждения выберите категорию."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)
    if not asset_name:
        request.session["flash_message"] = "Для подтверждения заполните категорию и наименование."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)

    # Если прислали ID категории/локации, канонизируем имя из справочника.
    if category_id_int > 0 or location_id_int > 0:
        try:
            client = _build_atracker_client()
            if category_id_int > 0:
                cats = await client.get_categories()
                for c in cats or []:
                    cid = int(c.get("ID") or c.get("Id") or 0)
                    if cid == category_id_int:
                        category_name = str(c.get("sFullName") or c.get("Name") or c.get("sName") or "").strip() or category_name
                        break
            if location_id_int > 0:
                raw_loc = await client.get_locations()
                for item in _location_directory_items_flat(raw_loc or []):
                    iid = int(item.get("id") or 0)
                    if iid == location_id_int:
                        location_name = str(item.get("name") or "").strip()
                        break
        except Exception:
            pass

    # Сохраняем правки администратора до финализации.
    req = update_asset_add_request(
        request_id,
        {
            "atracker_category_id": category_id_int if category_id_int > 0 else 0,
            "atracker_location_id": location_id_int if location_id_int > 0 else 0,
            "requester_location_name": location_name or (req.get("requester_location_name") or ""),
            "sd_request_number": sd_request_number,
            "category_name": category_name,
            "asset_name": asset_name,
            "serial_number": serial_number,
            "inventory_number": inventory_number,
            "comment": comment,
        },
    )
    if not req:
        request.session["flash_message"] = "Не удалось сохранить изменения заявки."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)

    finalized_req, finalize_err = await _finalize_asset_add_in_atracker(req)
    if finalize_err and not finalized_req:
        request.session["flash_message"] = finalize_err
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)

    updated = update_asset_add_request(
        request_id,
        {
            "status": "approved",
            "finalized_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "admin_finalize_note": (
                "Подтверждено администратором в вебе. "
                "Актив обработан в A-Tracker (без проверки статусов заявки)."
            ),
        },
    )
    if not updated:
        request.session["flash_message"] = "Не удалось обновить статус заявки."
        return RedirectResponse(url="/admin/transfers", status_code=302)
    if finalize_err:
        request.session["flash_message"] = (
            "Заявка подтверждена и актив обработан, но есть предупреждения: "
            + finalize_err
        )
    else:
        if already_approved and not has_final_asset:
            request.session["flash_message"] = (
                "Заявка была подтверждена ранее, финализация в A-Tracker выполнена сейчас."
            )
        else:
            request.session["flash_message"] = "Заявка на добавление техники подтверждена и актив создан в A-Tracker."

    _notify_asset_add_approved(updated)
    _write_audit(
        request,
        action="asset_add_approved",
        details=(
            f"request_id={request_id}; "
            f"category={category_name}; "
            f"asset={asset_name}; "
            f"serial={serial_number}; "
            f"invent={inventory_number}"
        ),
    )
    return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)


@app.post("/admin/asset-add/{request_id}/reject")
async def admin_asset_add_reject(
    request: Request,
    request_id: str,
    reject_comment: str = Form(""),
):
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Раздел доступен только администраторам."
        return RedirectResponse(url="/assets", status_code=302)
    if not WEB_ASSET_ADD_BUTTON_ENABLED:
        request.session["flash_message"] = (
            "Подача заявки на добавление техники отключена в настройках сайта."
        )
        return RedirectResponse(url="/admin/transfers", status_code=302)

    req = get_asset_add_request(request_id)
    if not req:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/admin/transfers", status_code=302)

    comment = (reject_comment or "").strip()
    if not comment:
        request.session["flash_message"] = "Для отклонения заявки укажите комментарий с причиной."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)
    if req.get("status") in {"approved", "rejected"}:
        request.session["flash_message"] = "Заявка уже обработана."
        return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)

    updated = update_asset_add_request(
        request_id,
        {
            "status": "rejected",
            "rejected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reject_comment": comment,
            "admin_finalize_note": "Отклонено администратором в вебе.",
        },
    )
    if not updated:
        request.session["flash_message"] = "Не удалось отклонить заявку."
        return RedirectResponse(url="/admin/transfers", status_code=302)

    _notify_asset_add_rejected(updated)
    _write_audit(request, action="asset_add_rejected", details=f"request_id={request_id}")
    request.session["flash_message"] = "Заявка отклонена, пользователь уведомлён."
    return RedirectResponse(url=f"/asset-add/{request_id}", status_code=302)


@app.post("/admin/transfers/{transfer_id}/complete")
async def admin_transfer_complete(request: Request, transfer_id: str):
    email = request.session.get("user_email")
    is_admin = bool(request.session.get("is_admin"))
    if not email:
        return RedirectResponse(url="/", status_code=302)
    if not is_admin:
        request.session["flash_message"] = "Раздел доступен только администраторам."
        return RedirectResponse(url="/assets", status_code=302)
    if not WEB_TRANSFER_ENABLED:
        request.session["flash_message"] = (
            "Оформление заявок на перемещение техники отключено в настройках сайта."
        )
        return RedirectResponse(url="/admin/transfers", status_code=302)
    tr = get_transfer(transfer_id)
    if not tr:
        request.session["flash_message"] = "Заявка не найдена."
        return RedirectResponse(url="/admin/transfers", status_code=302)
    if tr.get("status") != "ready_for_admin":
        request.session["flash_message"] = (
            "Завершить можно только заявку в статусе «Ожидает утверждения администратором»."
        )
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    if ATRACKER_TRANSFER_POSTING_SERVICE_ID <= 0:
        request.session["flash_message"] = (
            "Перемещение через A-Tracker не настроено: в config.ini укажите [atracker] transfer_posting_service_id "
            "(ID кастомного сервиса перемещения)."
        )
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    scan_path = Path(tr.get("scan_file_path") or "")
    if not scan_path.is_file():
        request.session["flash_message"] = "Файл скана не найден на сервере. Загрузите акт заново."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    client = _build_atracker_client()
    try:
        employees = await client.get_employees()
    except Exception:
        request.session["flash_message"] = "Не удалось загрузить сотрудников из A-Tracker."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    from_id = tr.get("from_employee_id")
    to_id = tr.get("to_employee_id")
    if from_id is None or from_id == "":
        from_id = employee_id_by_email(employees, tr.get("from_email") or "")
    if to_id is None or to_id == "":
        to_id = employee_id_by_email(employees, tr.get("to_email") or "")
    try:
        from_id = int(from_id) if from_id is not None and from_id != "" else None
    except (TypeError, ValueError):
        from_id = None
    try:
        to_id = int(to_id) if to_id is not None and to_id != "" else None
    except (TypeError, ValueError):
        to_id = None

    if not from_id or not to_id:
        request.session["flash_message"] = (
            "Не удалось определить ID сотрудников отправителя/получателя в A-Tracker. Проверьте почты в справочнике."
        )
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    portfolio_ids: list[int] = []
    for a in tr.get("assets") or []:
        if isinstance(a, dict) and a.get("id") is not None:
            portfolio_ids.append(int(a["id"]))

    if not portfolio_ids:
        request.session["flash_message"] = "В заявке нет активов для перемещения."
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    loc_raw = tr.get("receiver_location_id")
    try:
        if loc_raw is None or (isinstance(loc_raw, str) and not str(loc_raw).strip()):
            loc_id = 0
        else:
            loc_id = int(loc_raw)
    except (TypeError, ValueError):
        loc_id = 0

    body = {
        "lUserIdFrom": from_id,
        "lUserIdTo": to_id,
        "portfolioIds": portfolio_ids,
        "seOrganization": tr.get("organization_name") or "",
        "lReceiverLocationId": loc_id,
    }

    try:
        resp = await client.post_transfer_posting(body)
    except Exception as e:
        err_msg = str(e) or "ошибка перемещения"
        update_transfer(transfer_id, {"posting_last_error": err_msg})
        request.session["flash_message"] = f"Перемещение в A-Tracker не выполнено: {err_msg}"
        return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)

    _oid, op_str = _parse_operation_id_from_posting_response(resp)
    op_display = op_str or (str(_oid) if _oid is not None else "")
    if not op_display:
        raw_d = resp.get("data")
        op_display = str(raw_d)[:120] if raw_d is not None else ""
    op_label = _operation_transfer_label(op_display)

    content = scan_path.read_bytes()
    fname = tr.get("scan_original_name") or scan_path.name
    ctype = _guess_mime_scan(fname)

    failed: list[str] = []
    for aid in portfolio_ids:
        try:
            await client.upload_asset_file(
                asset_id=aid,
                file_name=fname,
                content_bytes=content,
                content_type=ctype,
            )
        except Exception:
            failed.append(str(aid))

    patch = {
        "status": "completed",
        "operation_number": op_display,
        "posting_last_error": "",
        "attachment_failures": ",".join(failed) if failed else "",
    }
    update_transfer(transfer_id, patch)
    tr_done = get_transfer(transfer_id) or {}
    _notify_transfer_completed_both(transfer_id, tr_done)
    _write_audit(
        request,
        action="transfer_completed",
        details=f"transfer_id={transfer_id}; operation={op_display}; attachment_failures={patch['attachment_failures']}",
    )
    if failed:
        request.session["flash_message"] = (
            f"Перемещение создано ({op_label}), но вложение к активам: ошибки по ID {', '.join(failed)}."
        )
    else:
        request.session["flash_message"] = f"Заявка на перемещение техники завершена. {op_label}."
    return RedirectResponse(url=f"/transfers/{transfer_id}", status_code=302)


def create_app() -> FastAPI:
    """Фабрика приложения на случай, если потом будем собирать exe или подключать к IIS."""
    return app


if __name__ == "__main__":
    # Локальный запуск через python front_site/app.py
    import uvicorn

    uvicorn.run("front_site.app:app", host="127.0.0.1", port=8000, reload=True)

