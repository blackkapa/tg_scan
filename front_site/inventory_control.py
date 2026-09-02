from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INVENTORY_CONTROL_STORE_PATH = DATA_DIR / "inventory_control.json"

STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_NOT_STARTED = "not_started"
STATUS_NO_ASSETS = "no_assets"
STATUS_ERROR = "error"

STATUS_LABELS: dict[str, str] = {
    STATUS_COMPLETED: "Пройдена",
    STATUS_IN_PROGRESS: "В процессе",
    STATUS_NOT_STARTED: "Не начата",
    STATUS_NO_ASSETS: "Нет техники",
    STATUS_ERROR: "Ошибка",
}


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not INVENTORY_CONTROL_STORE_PATH.exists():
        INVENTORY_CONTROL_STORE_PATH.write_text("[]", encoding="utf-8")


def _save_items(items: List[Dict[str, Any]]) -> None:
    _ensure_store()
    INVENTORY_CONTROL_STORE_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_controlled_employees() -> List[Dict[str, Any]]:
    _ensure_store()
    try:
        parsed = json.loads(INVENTORY_CONTROL_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict)]


def get_controlled_employee(emp_id: str) -> Optional[Dict[str, Any]]:
    for item in list_controlled_employees():
        if str(item.get("id")) == str(emp_id):
            return item
    return None


def get_controlled_employee_by_email(email: str) -> Optional[Dict[str, Any]]:
    want = (email or "").strip().lower()
    if not want:
        return None
    for item in list_controlled_employees():
        if str(item.get("email") or "").strip().lower() == want:
            return item
    return None


def delete_controlled_employee(emp_id: str) -> bool:
    items = list_controlled_employees()
    new_items = [x for x in items if str(x.get("id")) != str(emp_id)]
    if len(new_items) != len(items):
        _save_items(new_items)
        return True
    return False


def save_controlled_employee(record: Dict[str, Any]) -> Dict[str, Any]:
    items = list_controlled_employees()
    emp_id = str(record.get("id") or "")
    if not emp_id:
        emp_id = str(uuid4())
        record["id"] = emp_id

    record.setdefault("created_at", _now_str())
    record.setdefault("remind_count", 0)
    record.setdefault("last_reminded_at", "")

    found = False
    for idx, item in enumerate(items):
        if str(item.get("id")) == emp_id:
            items[idx] = record
            found = True
            break
    if not found:
        items.append(record)

    _save_items(items)
    return record


def compute_inventory_summary(
    raw_assets: List[Dict[str, Any]],
    is_inventoried_fn,
    inv_number_fn=None,
) -> tuple[int, int, int, str, list[dict]]:
    """
    Анализирует список активов сотрудника:
    Возвращает:
    (total_assets, inventoried_assets, progress_pct, status, assets_snapshot)
    """
    total = len(raw_assets)
    if total == 0:
        return 0, 0, 0, STATUS_NO_ASSETS, []

    inventoried_count = 0
    snapshot: list[dict] = []

    for a in raw_assets:
        if not isinstance(a, dict):
            continue
        asset_id = int(a.get("ID") or 0)
        name = str(a.get("sFullName") or a.get("Name") or f"ID {asset_id}").strip()
        serial = str(a.get("sSerialNo") or "—").strip()
        invent = ""
        if inv_number_fn:
            invent = inv_number_fn(a)
        if not invent:
            invent = str(a.get("sInventNumber") or a.get("sInventoryNo") or "—").strip()
        
        is_inv = bool(is_inventoried_fn(a))
        if is_inv:
            inventoried_count += 1

        snapshot.append({
            "id": asset_id,
            "name": name,
            "serial": serial,
            "invent": invent or "—",
            "inventoried": is_inv,
        })

    pct = int(round((inventoried_count / total) * 100)) if total > 0 else 0

    if inventoried_count == total:
        status = STATUS_COMPLETED
    elif inventoried_count > 0:
        status = STATUS_IN_PROGRESS
    else:
        status = STATUS_NOT_STARTED

    return total, inventoried_count, pct, status, snapshot


async def refresh_controlled_employee(
    emp_record: Dict[str, Any],
    client,
    is_inventoried_fn,
    inv_number_fn=None,
) -> Dict[str, Any]:
    """Запрашивает свежие данные из A-Tracker по сотруднику и обновляет запись."""
    fio = str(emp_record.get("fio") or "").strip()
    try:
        raw_assets = await client.get_assets_by_fio(fio)
        total, inv_count, pct, status, snapshot = compute_inventory_summary(
            raw_assets or [],
            is_inventoried_fn,
            inv_number_fn,
        )
        emp_record["total_assets"] = total
        emp_record["inventoried_assets"] = inv_count
        emp_record["progress_pct"] = pct
        emp_record["status"] = status
        emp_record["status_label"] = STATUS_LABELS.get(status, status)
        emp_record["assets_snapshot"] = snapshot
        emp_record["last_checked_at"] = _now_str()
        if status == STATUS_COMPLETED and not emp_record.get("completed_at"):
            emp_record["completed_at"] = _now_str()
        elif status != STATUS_COMPLETED:
            emp_record["completed_at"] = ""
        emp_record["check_error"] = ""
    except Exception as ex:
        logger.exception("Error checking inventory status for %s: %s", fio, ex)
        emp_record["check_error"] = str(ex)
        emp_record["last_checked_at"] = _now_str()
        if not emp_record.get("status"):
            emp_record["status"] = STATUS_ERROR
            emp_record["status_label"] = STATUS_LABELS[STATUS_ERROR]

    save_controlled_employee(emp_record)
    return emp_record


async def refresh_all_controlled_employees(
    client,
    is_inventoried_fn,
    inv_number_fn=None,
) -> tuple[int, int]:
    """Массовое обновление всех сотрудников на контроле. Возвращает (успешно, ошибок)."""
    items = list_controlled_employees()
    success = 0
    errors = 0
    for it in items:
        try:
            await refresh_controlled_employee(it, client, is_inventoried_fn, inv_number_fn)
            if not it.get("check_error"):
                success += 1
            else:
                errors += 1
        except Exception:
            errors += 1
    return success, errors


def generate_inventory_control_csv() -> bytes:
    """Генерирует CSV-файл отчёта (в кодировке UTF-8 с BOM для прямого открытия в MS Excel)."""
    items = list_controlled_employees()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)

    # Заголовки
    writer.writerow([
        "ФИО сотрудника",
        "Email",
        "Логин",
        "Статус инвентаризации",
        "Прогресс (%)",
        "Проверено единиц",
        "Всего единиц",
        "Дата последней проверки",
        "Дата завершения 100%",
        "Напоминаний отправлено",
        "Дата добавления",
        "Список техники (детали)",
    ])

    for it in items:
        status_label = STATUS_LABELS.get(it.get("status", ""), it.get("status", ""))
        assets_text = ""
        for a in it.get("assets_snapshot") or []:
            mark = "[✓]" if a.get("inventoried") else "[ ]"
            name = a.get("name") or f"ID {a.get('id')}"
            inv = a.get("invent") or "—"
            sn = a.get("serial") or "—"
            assets_text += f"{mark} {name} (Инв: {inv}, Сер: {sn}) | "

        writer.writerow([
            it.get("fio") or "",
            it.get("email") or "",
            it.get("login") or "",
            status_label,
            f"{it.get('progress_pct', 0)}%",
            it.get("inventoried_assets", 0),
            it.get("total_assets", 0),
            it.get("last_checked_at") or "",
            it.get("completed_at") or "",
            it.get("remind_count", 0),
            it.get("created_at") or "",
            assets_text.rstrip(" | "),
        ])

    # Добавляем UTF-8 BOM (\ufeff), чтобы Excel открывал кириллицу без искажений
    return ("\ufeff" + output.getvalue()).encode("utf-8")
