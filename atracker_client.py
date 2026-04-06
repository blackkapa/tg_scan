import aiohttp
import datetime
import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _inventory_number_from_flat_dict(raw: Dict[str, Any]) -> str:
    """Разбор только «плоской» строки полей (без обёрток)."""
    for k in (
        "sInventNumber",
        "SInventNumber",
        "sInventoryNo",
        "SInventoryNo",
        "sInventNo",
        "SInventNo",
        "InventoryNo",
        "inventoryNo",
        "InventNumber",
        "inventNumber",
    ):
        v = raw.get(k)
        if v is None or isinstance(v, (dict, list)):
            continue
        s = v.strip() if isinstance(v, str) else str(v).strip()
        if s and s != "—":
            return s
    for rk, rv in raw.items():
        if not isinstance(rk, str) or rv is None or isinstance(rv, (dict, list)):
            continue
        rkl = rk.lower()
        if rkl in (
            "sinventnumber",
            "sinventoryno",
            "sinventno",
            "inventoryno",
            "inventnumber",
        ):
            s = rv.strip() if isinstance(rv, str) else str(rv).strip()
            if s and s != "—":
                return s
    return ""


def inventory_number_from_atracker_dict(raw: Optional[Dict[str, Any]]) -> str:
    """Инвентарный номер из строки ответа A-Tracker (itamPortfolio).

    Каноническое поле в модели — ``sInventNumber`` (см. artibut_model).
    Учитываем алиасы, регистр и одну вложенность (если сервис оборачивает строку в ``Row`` / ``Item``).
    """
    if not isinstance(raw, dict):
        return ""
    hit = _inventory_number_from_flat_dict(raw)
    if hit:
        return hit
    for val in raw.values():
        if isinstance(val, dict):
            hit = _inventory_number_from_flat_dict(val)
            if hit:
                return hit
    return ""


def _category_id_from_asset_raw(raw: Dict[str, Any]) -> Optional[int]:
    """Числовой ID категории, если в ответе только ссылка без sFullName."""
    if not isinstance(raw, dict):
        return None
    nested = raw.get("lt_lCategoryId")
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
    if nested is not None and not isinstance(nested, dict):
        try:
            i = int(str(nested).strip())
            if i > 0:
                return i
        except (TypeError, ValueError):
            pass
    for k in ("lCategoryId", "LCategoryId", "l_lCategoryId"):
        v = raw.get(k)
        if v is None:
            continue
        try:
            i = int(v)
            if i > 0:
                return i
        except (TypeError, ValueError):
            pass
    model = raw.get("lModelId")
    if isinstance(model, dict):
        sub = model.get("lCategoryId") or model.get("lt_lCategoryId")
        if isinstance(sub, dict):
            for k in ("ID", "Id", "id"):
                v = sub.get(k)
                if v is not None:
                    try:
                        i = int(v)
                        if i > 0:
                            return i
                    except (TypeError, ValueError):
                        pass
    return None


def _category_name_from_asset_raw(raw: Dict[str, Any]) -> str:
    """Имя категории из полной карточки актива (lt_lCategoryId часто объект с Name/sFullName)."""
    if not isinstance(raw, dict):
        return ""
    model = raw.get("lModelId")
    if isinstance(model, dict):
        sub = model.get("lCategoryId") or model.get("lt_lCategoryId") or model.get("LCategoryId")
        if isinstance(sub, dict):
            for k in ("sFullName", "Name", "sName", "sCategoryName", "CategoryName"):
                v = sub.get(k)
                if v is not None:
                    s = str(v).strip()
                    if s:
                        return s
    nested = raw.get("lt_lCategoryId")
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
    ):
        v = raw.get(key)
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
        if s and s not in ("—", "0"):
            return s
    if nested is not None and not isinstance(nested, dict):
        s = str(nested).strip()
        if s and s not in ("—", "0"):
            return s
    return ""


def _location_name_from_asset_raw(raw: Dict[str, Any]) -> str:
    """Текст местоположения с полной карточки itamPortfolio (как в вебе _asset_location_display)."""
    if not isinstance(raw, dict):
        return ""
    nested = raw.get("lt_lLocationId")
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
        v = raw.get(key)
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
    return ""


class ATrackerClient:
    """
    Простой async‑клиент для работы с A-Tracker:
    - логин по /Api/Login и хранение JWT в памяти
    - вызов сервиса "активы по ФИО"
    - вызов сервиса "отметить инвентаризацию"       
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        assets_service_id: int,
        mark_service_id: int,
        upload_doc_service_id: int,
        asset_info_service_id: Optional[int] = None,
        employees_list_service_id: Optional[int] = None,
        employee_update_service_id: Optional[int] = None,
        employee_add_service_id: Optional[int] = None,
        transfer_posting_service_id: Optional[int] = None,
        locations_list_service_id: Optional[int] = None,
        categories_list_service_id: Optional[int] = None,
        asset_add_request_create_service_id: Optional[int] = None,
        asset_add_request_get_service_id: Optional[int] = None,
        portfolio_create_service_id: Optional[int] = None,
        portfolio_update_service_id: Optional[int] = None,
        request_attach_service_id: Optional[int] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.assets_service_id = assets_service_id
        self.mark_service_id = mark_service_id
        self.upload_doc_service_id = upload_doc_service_id
        self.asset_info_service_id = asset_info_service_id
        self.employees_list_service_id = employees_list_service_id
        self.employee_update_service_id = employee_update_service_id
        self.employee_add_service_id = employee_add_service_id
        self.transfer_posting_service_id = transfer_posting_service_id or 0
        self.locations_list_service_id = locations_list_service_id or 0
        self.categories_list_service_id = int(categories_list_service_id or 0)
        self.asset_add_request_create_service_id = int(asset_add_request_create_service_id or 0)
        self.asset_add_request_get_service_id = int(asset_add_request_get_service_id or 0)
        self.portfolio_create_service_id = int(portfolio_create_service_id or 0)
        self.portfolio_update_service_id = int(portfolio_update_service_id or 0)
        self.request_attach_service_id = int(request_attach_service_id or 0)

        self._token: Optional[str] = None
        self._token_exp: Optional[datetime.datetime] = None
        self._refresh_token: Optional[str] = None

    async def _login(self, session: aiohttp.ClientSession) -> None:
        """Получить новый JWT‑токен по логину/паролю."""
        url = f"{self.base_url}/Api/Login"
        payload = {
            "Username": self.username,
            "Password": self.password,
        }
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            data = await resp.json()

        self._token = data["token"]
        self._refresh_token = data.get("refreshToken")
        exp_str = str(data["expiration"])
        if exp_str.endswith("Z"):
            exp_str = exp_str.replace("Z", "+00:00")
        self._token_exp = datetime.datetime.fromisoformat(exp_str)

    async def _ensure_token(self, session: aiohttp.ClientSession) -> None:
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        if not self._token or not self._token_exp or now >= self._token_exp:
            await self._login(session)

    async def get_assets_by_fio(self, fio: str) -> List[Dict[str, Any]]:
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params = {
                "id": str(self.assets_service_id),
                "fio": fio,
            }
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data.get("data", [])

    async def mark_inventory(
        self,
        asset_id: int,
        fio: str,
        tg_user_id: int,
        tg_username: Optional[str],
    ) -> Dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params = {
                "id": str(self.mark_service_id),
                "AssetId": str(asset_id),
                "Fio": fio,
                "TelegramUserId": str(tg_user_id),
                "TelegramUsername": tg_username or "",
            }
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def upload_asset_file(
        self,
        asset_id: int,
        file_name: str,
        content_bytes: bytes,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        encoded = base64.b64encode(content_bytes).decode("ascii")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={self.upload_doc_service_id}"
            payload = {
                "AssetId": asset_id,
                "assetId": asset_id,
                "FileName": file_name,
                "fileName": file_name,
                "ContentBase64": encoded,
                "contentBase64": encoded,
                "ContentType": content_type or "application/octet-stream",
                "contentType": content_type or "application/octet-stream",
            }
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def get_asset_info(self, asset_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        if not self.asset_info_service_id:
            return (None, "service_error")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params = {
                "id": str(self.asset_info_service_id),
                "AssetId": str(asset_id),
            }
            logger.info(
                "get_asset_info: запрос id=%s AssetId=%s → %s",
                self.asset_info_service_id,
                asset_id,
                f"{url}?id={self.asset_info_service_id}&AssetId={asset_id}",
            )
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                ct = resp.headers.get("Content-Type") or ""
                if "application/json" not in ct:
                    logger.warning(
                        "get_asset_info: ответ не JSON (Content-Type=%s), возможно редирект на Login",
                        ct,
                    )
                    return (None, "service_error")
                try:
                    data = await resp.json()
                except aiohttp.ContentTypeError:
                    logger.warning("get_asset_info: не удалось разобрать ответ как JSON")
                    return (None, "service_error")
        if data.get("returnCode") != "Success":
            return (None, "service_error")
        raw = data.get("data")
        if raw is None:
            return (None, "not_found")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        if not isinstance(raw, dict):
            return (None, "not_found")
        fio = raw.get("OwnerFio") or raw.get("Fio") or raw.get("sOwnerFio") or raw.get("Owner") or "—"
        serial = raw.get("sSerialNo")
        serial = (serial.strip() if isinstance(serial, str) else (str(serial).strip() if serial else ""))
        inv_no = inventory_number_from_atracker_dict(raw)
        category = _category_name_from_asset_raw(raw)
        cid = _category_id_from_asset_raw(raw)
        location = _location_name_from_asset_raw(raw)
        return (
            {
                "ID": raw.get("ID", asset_id),
                "sFullName": raw.get("sFullName") or raw.get("Name") or f"ID {asset_id}",
                "sSerialNo": serial,
                "sInventNumber": inv_no,
                "sInventoryNo": inv_no,
                "OwnerFio": fio,
                "category": category,
                "category_id": cid,
                "location": location,
            },
            None,
        )

    async def get_employees(self) -> List[Dict[str, Any]]:
        if not self.employees_list_service_id:
            return []
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params = {"id": str(self.employees_list_service_id)}
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data.get("data", [])

    async def get_locations(self, q: Optional[str] = None) -> List[Dict[str, Any]]:
        """Справочник местоположений (кастомный GET-сервис, как список сотрудников).

        Необязательный ``q`` передаётся как query-параметр (если сервис на стороне A-Tracker поддерживает поиск).
        """
        if not self.locations_list_service_id:
            return []
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params: Dict[str, Any] = {"id": str(self.locations_list_service_id)}
            qs = (q or "").strip()
            if qs:
                params["q"] = qs
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data.get("data", [])

    async def get_categories(self, q: Optional[str] = None) -> List[Dict[str, Any]]:
        """Справочник категорий (itamCategory), тот же контракт, что у get_locations."""
        if not self.categories_list_service_id:
            return []
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params: Dict[str, Any] = {"id": str(self.categories_list_service_id)}
            qs = (q or "").strip()
            if qs:
                params["q"] = qs
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data.get("data", [])

    async def update_employee(
        self,
        employee_id: int,
        s_full_name: str,
        s_login_name: str,
        s_email: str,
        s_pers_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.employee_update_service_id:
            raise RuntimeError("ATRACKER_EMPLOYEE_UPDATE_SERVICE_ID not set")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={self.employee_update_service_id}"
            payload = {
                "ID": employee_id,
                "sFullName": s_full_name or "",
                "sLoginName": s_login_name or "",
                "sEmail": s_email or "",
                "sPersNo": (s_pers_no or "").strip(),
            }
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def create_employee(
        self,
        s_full_name: str,
        s_login_name: str,
        s_email: str,
        s_pers_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not self.employee_add_service_id:
            raise RuntimeError("ATRACKER_EMPLOYEE_ADD_SERVICE_ID not set")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={self.employee_add_service_id}"
            payload = {
                "sFullName": s_full_name or "",
                "sLoginName": s_login_name or "",
                "sEmail": s_email or "",
                "sPersNo": (s_pers_no or "").strip(),
            }
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def create_asset_add_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sid = int(self.asset_add_request_create_service_id or 0)
        if sid <= 0:
            raise RuntimeError("asset_add_request_create_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={sid}"
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def get_asset_add_request_state(self, request_id: int) -> Dict[str, Any]:
        sid = int(self.asset_add_request_get_service_id or 0)
        if sid <= 0:
            raise RuntimeError("asset_add_request_get_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {"Authorization": f"Bearer {self._token}"}
            url = f"{self.base_url}/Api/Service"
            params = {"id": str(sid), "RequestId": str(request_id)}
            async with session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def create_portfolio_asset(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sid = int(self.portfolio_create_service_id or 0)
        if sid <= 0:
            raise RuntimeError("portfolio_create_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={sid}"
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def update_portfolio_asset(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sid = int(self.portfolio_update_service_id or 0)
        if sid <= 0:
            raise RuntimeError("portfolio_update_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={sid}"
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def attach_document_to_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sid = int(self.request_attach_service_id or 0)
        if sid <= 0:
            raise RuntimeError("request_attach_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={sid}"
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def post_transfer_posting(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        Сервис утверждения перемещения (логика как мастер OneLineTransit2).
        Тело — JSON по контракту сервиса на стороне A-Tracker.
        """
        sid = int(self.transfer_posting_service_id or 0)
        if sid <= 0:
            raise RuntimeError("transfer_posting_service_id не задан в конфигурации клиента")
        async with aiohttp.ClientSession() as session:
            await self._ensure_token(session)
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            url = f"{self.base_url}/Api/Service?id={sid}"
            async with session.post(url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                data = await resp.json()
        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data
