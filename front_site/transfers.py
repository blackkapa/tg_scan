from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TRANSFERS_PATH = DATA_DIR / "transfers.json"

# Номер накладной: СП + 6 цифр (кириллица С и П)
_WAYBILL_RE = re.compile(r"^СП(\d{6})$")


def _format_waybill(seq: int) -> str:
    return f"СП{seq:06d}"


def _parse_waybill_seq(value: object) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    m = _WAYBILL_RE.match(s)
    if not m:
        return None
    return int(m.group(1))


def _max_waybill_seq(items: List[Dict[str, Any]]) -> int:
    m = 0
    for it in items:
        seq = _parse_waybill_seq(it.get("waybill_number"))
        if seq is not None:
            m = max(m, seq)
    return m


def _backfill_waybill_numbers(items: List[Dict[str, Any]]) -> bool:
    """Заполняет waybill_number у записей без номера; порядок — по created_at."""
    missing = [it for it in items if not str(it.get("waybill_number") or "").strip()]
    if not missing:
        return False

    def _sort_key(it: Dict[str, Any]) -> tuple:
        return (str(it.get("created_at") or ""), str(it.get("id") or ""))

    missing.sort(key=_sort_key)
    n = _max_waybill_seq(items) + 1
    for it in missing:
        it["waybill_number"] = _format_waybill(n)
        n += 1
    return True


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not TRANSFERS_PATH.exists():
        TRANSFERS_PATH.write_text("[]", encoding="utf-8")


def list_transfers() -> List[Dict[str, Any]]:
    _ensure_store()
    try:
        parsed = json.loads(TRANSFERS_PATH.read_text(encoding="utf-8"))
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    items = [x for x in parsed if isinstance(x, dict)]
    if _backfill_waybill_numbers(items):
        _save_transfers(items)
    return items


def _save_transfers(items: List[Dict[str, Any]]) -> None:
    _ensure_store()
    TRANSFERS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_transfer(transfer_id: str) -> Optional[Dict[str, Any]]:
    for item in list_transfers():
        if str(item.get("id")) == str(transfer_id):
            return item
    return None


def create_transfer(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = list_transfers()
    obj = dict(payload)
    use_drawn = bool(obj.get("use_drawn_signatures"))
    if "status" not in obj:
        obj["status"] = "pending_sender_sign" if use_drawn else "pending_receiver"
    obj.setdefault("use_drawn_signatures", False)
    obj.setdefault("sender_signature_path", "")
    obj.setdefault("receiver_signature_path", "")
    obj.setdefault("sender_signed_at", "")
    obj.setdefault("receiver_signed_at", "")
    obj.setdefault("scan_file_path", "")
    obj.setdefault("scan_original_name", "")
    obj.setdefault("scan_verified", False)
    obj.setdefault("operation_number", "")
    obj.setdefault("waybill_number", "")
    obj.setdefault("cancel_reason", "")
    obj.setdefault("notification_sent_at", "")
    obj.setdefault("notification_last_error", "")
    obj.setdefault("from_employee_id", None)
    obj.setdefault("to_employee_id", None)
    obj.setdefault("receiver_location_id", None)
    obj.setdefault("receiver_location_name", "")
    obj.setdefault("posting_last_error", "")
    obj.setdefault("attachment_failures", "")
    obj.setdefault("created_at", _now_str())
    obj.setdefault("updated_at", obj["created_at"])
    # Номер накладной: сквозная нумерация СП000001, СП000002, …
    obj["waybill_number"] = _format_waybill(_max_waybill_seq(items) + 1)
    items.append(obj)
    _save_transfers(items)
    return obj


def update_transfer(transfer_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = list_transfers()
    updated: Optional[Dict[str, Any]] = None
    for idx, item in enumerate(items):
        if str(item.get("id")) != str(transfer_id):
            continue
        merged = dict(item)
        merged.update(patch)
        merged["updated_at"] = _now_str()
        items[idx] = merged
        updated = merged
        break
    if updated is None:
        return None
    _save_transfers(items)
    return updated

