from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ASSET_ADD_REQUESTS_PATH = DATA_DIR / "asset_add_requests.json"
_REQ_NUM_RE = re.compile(r"^ZT(\d{6})$")


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _ensure_store() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ASSET_ADD_REQUESTS_PATH.exists():
        ASSET_ADD_REQUESTS_PATH.write_text("[]", encoding="utf-8")


def _format_request_number(seq: int) -> str:
    return f"ZT{seq:06d}"


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


def _backfill_request_numbers(items: List[Dict[str, Any]]) -> bool:
    missing = [it for it in items if not str(it.get("request_number") or "").strip()]
    if not missing:
        return False
    missing.sort(key=lambda it: (str(it.get("created_at") or ""), str(it.get("id") or "")))
    n = _max_request_seq(items) + 1
    for it in missing:
        it["request_number"] = _format_request_number(n)
        n += 1
    return True


def _save_items(items: List[Dict[str, Any]]) -> None:
    _ensure_store()
    ASSET_ADD_REQUESTS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_asset_add_requests() -> List[Dict[str, Any]]:
    _ensure_store()
    try:
        parsed = json.loads(ASSET_ADD_REQUESTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        parsed = []
    if not isinstance(parsed, list):
        return []
    items = [x for x in parsed if isinstance(x, dict)]
    if _backfill_request_numbers(items):
        _save_items(items)
    return items


def get_asset_add_request(local_id: str) -> Optional[Dict[str, Any]]:
    for item in list_asset_add_requests():
        if str(item.get("id")) == str(local_id):
            return item
    return None


def create_asset_add_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    items = list_asset_add_requests()
    obj = dict(payload)
    obj.setdefault("kind", "asset_add")
    obj.setdefault("status", "pending_review")  # pending_review | approved | rejected
    obj.setdefault("atracker_request_id", None)
    obj.setdefault("atracker_req_number", "")
    obj.setdefault("atracker_status", 0)
    obj.setdefault("atracker_status_text", "")
    obj.setdefault("atracker_chosen_portfolio_id", None)
    obj.setdefault("final_asset_id", None)
    obj.setdefault("finalized_at", "")
    obj.setdefault("rejected_at", "")
    obj.setdefault("reject_comment", "")
    obj.setdefault("admin_finalize_note", "")
    obj.setdefault("photos", [])
    obj.setdefault("created_at", _now_str())
    obj.setdefault("updated_at", obj["created_at"])
    obj.setdefault("request_number", _format_request_number(_max_request_seq(items) + 1))
    items.append(obj)
    _save_items(items)
    return obj


def update_asset_add_request(local_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    items = list_asset_add_requests()
    updated: Optional[Dict[str, Any]] = None
    for idx, item in enumerate(items):
        if str(item.get("id")) != str(local_id):
            continue
        merged = dict(item)
        merged.update(patch)
        merged["updated_at"] = _now_str()
        items[idx] = merged
        updated = merged
        break
    if updated is None:
        return None
    _save_items(items)
    return updated
