"""Локальное хранилище заявок «несоответствие техники» (JSON)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STORE_PATH = DATA_DIR / "discrepancy_requests.json"
_REQ_NUM_RE = re.compile(r"^DC(\d{6})$")

ALLOWED_REASONS = frozenset({"not_mine", "other_emp", "lost", "other"})
REASON_LABELS: Dict[str, str] = {
    "not_mine": "Не моя техника",
    "other_emp": "Числится на другом сотруднике",
    "lost": "Утеряна",
    "other": "Другое",
}

VALID_STATUSES = frozenset({"sent", "in_review", "closed", "rejected"})


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        STORE_PATH.write_text("[]", encoding="utf-8")


def _format_request_number(seq: int) -> str:
    return f"DC{seq:06d}"


def _parse_request_number(value: object) -> Optional[int]:
    if value is None:
        return None
    s = str(value).strip()
    m = _REQ_NUM_RE.match(s)
    if not m:
        return None
    return int(m.group(1))


def _max_request_seq(items: List[Dict[str, Any]]) -> int:
    m = 0
    for it in items:
        seq = _parse_request_number(it.get("request_number"))
        if seq is not None:
            m = max(m, seq)
    return m


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".discrepancy_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def list_discrepancy_requests() -> List[Dict[str, Any]]:
    _ensure_store()
    try:
        parsed = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    return [x for x in parsed if isinstance(x, dict)]


def get_discrepancy_request(local_id: str) -> Optional[Dict[str, Any]]:
    for item in list_discrepancy_requests():
        if str(item.get("id")) == str(local_id):
            return item
    return None


def list_discrepancy_for_email(email: str) -> List[Dict[str, Any]]:
    el = (email or "").lower().strip()
    out: List[Dict[str, Any]] = []
    for item in list_discrepancy_requests():
        if (str(item.get("requester_email") or "").lower().strip()) == el:
            out.append(item)
    return out


def create_discrepancy_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = list_discrepancy_requests()
    obj = dict(payload)
    obj["kind"] = "discrepancy"
    rid = str(obj.get("id") or uuid4())
    obj["id"] = rid
    obj.setdefault("status", "sent")
    if obj["status"] not in VALID_STATUSES:
        obj["status"] = "sent"
    rc = str(obj.get("reason_code") or "").strip()
    if rc not in ALLOWED_REASONS:
        raise ValueError("invalid reason_code")
    obj["reason_code"] = rc
    obj.setdefault("reason_text", REASON_LABELS.get(rc, rc))
    obj.setdefault("comment", "")
    obj.setdefault("photos", [])
    obj.setdefault("admin_note", "")
    obj.setdefault("created_at", _now_str())
    obj.setdefault("updated_at", obj["created_at"])
    obj.setdefault("closed_at", "")
    obj.setdefault("notify_admin_error", "")
    obj.setdefault("notify_user_error", "")
    seq = _max_request_seq(items) + 1
    obj.setdefault("request_number", _format_request_number(seq))
    items.append(obj)
    _save_all(items)
    return obj


def _save_all(items: List[Dict[str, Any]]) -> None:
    _ensure_store()
    _atomic_write_json(STORE_PATH, items)


def update_discrepancy_request(
    local_id: str, patch: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    items = list_discrepancy_requests()
    updated: Optional[Dict[str, Any]] = None
    for idx, item in enumerate(items):
        if str(item.get("id")) != str(local_id):
            continue
        merged = dict(item)
        merged.update(patch)
        merged["updated_at"] = _now_str()
        if merged.get("status") == "closed" or merged.get("status") == "rejected":
            if not (merged.get("closed_at") or "").strip():
                merged["closed_at"] = merged["updated_at"]
        items[idx] = merged
        updated = merged
        break
    if updated is None:
        return None
    _save_all(items)
    return updated


def allowed_admin_transition(from_status: str, to_status: str) -> bool:
    fs = str(from_status or "")
    ts = str(to_status or "")
    if ts not in VALID_STATUSES:
        return False
    if fs == "sent" and ts in ("in_review", "closed", "rejected"):
        return True
    if fs == "in_review" and ts in ("closed", "rejected", "in_review"):
        return True
    return False
