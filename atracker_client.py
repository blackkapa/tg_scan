import aiohttp
import datetime
import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
        # expiration приходит в ISO‑формате, пример: 2021-12-31T23:59:59Z
        exp_str = str(data["expiration"])
        if exp_str.endswith("Z"):
            exp_str = exp_str.replace("Z", "+00:00")
        self._token_exp = datetime.datetime.fromisoformat(exp_str)

    async def _ensure_token(self, session: aiohttp.ClientSession) -> None:
        """Проверить токен и при необходимости перелогиниться."""
        now = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)
        if not self._token or not self._token_exp or now >= self._token_exp:
            await self._login(session)

    async def get_assets_by_fio(self, fio: str) -> List[Dict[str, Any]]:
        """
        Вызов сервиса A-Tracker, который возвращает список активов по ФИО.
        Ожидается, что сервис настроен как GET и принимает параметр fio.
        """
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
        """
        Вызов сервиса A-Tracker, который отмечает инвентаризацию актива.
        В текущей конфигурации сервис настроен как GET и ожидает
        параметры в URL:
        ?AssetId=123&Fio=...&TelegramUserId=...&TelegramUsername=...
        """
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
        """
        Загрузка файла в документы ИТ-актива через отдельный API-сервис.
        Ожидается, что сервис настроен как POST JSON и принимает:
        {
            "AssetId": 123,
            "FileName": "asset_123.jpg",
            "ContentBase64": "<base64>",
            "ContentType": "image/jpeg"
        }
        """
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
                "FileName": file_name,
                "ContentBase64": encoded,
                "ContentType": content_type or "application/octet-stream",
            }
            async with session.post(url, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

        if data.get("returnCode") != "Success":
            raise RuntimeError(f"A-Tracker error: {data.get('message')}")
        return data

    async def get_asset_info(self, asset_id: int) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        Получить информацию об активе по ID (название, владелец ФИО).
        Возвращает (info, None) при успехе, (None, "not_found") если актив не найден в базе,
        (None, "service_error") если сервис не ответил JSON (редирект на Login, не настроен и т.д.).
        """
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
                # A-Tracker при истёкшем токене/неверном сервисе редиректит на Login → HTML
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
        inv_no = raw.get("sInventoryNo")
        serial = (serial.strip() if isinstance(serial, str) else (str(serial).strip() if serial else ""))
        inv_no = (inv_no.strip() if isinstance(inv_no, str) else (str(inv_no).strip() if inv_no else ""))
        return (
            {
                "ID": raw.get("ID", asset_id),
                "sFullName": raw.get("sFullName") or raw.get("Name") or f"ID {asset_id}",
                "sSerialNo": serial,
                "sInventoryNo": inv_no,
                "OwnerFio": fio,
            },
            None,
        )

    async def get_employees(self) -> List[Dict[str, Any]]:
        """Список сотрудников из A-Tracker (itamEmplDept): ID, sFullName, sLoginName, sEmail."""
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

    async def update_employee(
        self,
        employee_id: int,
        s_full_name: str,
        s_login_name: str,
        s_email: str,
        s_pers_no: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Обновить сотрудника в A-Tracker по ID (sFullName, sLoginName, sEmail, sPersNo — табельный номер / objectSid из AD)."""
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
