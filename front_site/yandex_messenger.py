# -*- coding: utf-8 -*-
"""
Интеграция с корпоративным Яндекс Мессенджером через Bot API.
Позволяет отправлять уведомления и напоминания сотрудникам в личный чат по email/логину.
"""
import asyncio
import json
import logging
from typing import Tuple
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

YANDEX_BOT_API_URL = "https://botapi.messenger.yandex.net/bot/v1/messages/sendText/"


def _sync_send_yandex_message(
    login: str,
    text: str,
    token: str,
    important: bool = False,
    timeout_sec: int = 8,
) -> Tuple[bool, str]:
    headers = {
        "Authorization": f"OAuth {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "login": login,
        "text": text,
    }
    if important:
        payload["important"] = True

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(YANDEX_BOT_API_URL, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            resp_body = resp.read().decode("utf-8")
            parsed = json.loads(resp_body)
            if parsed.get("ok"):
                logger.info("Yandex Messenger message delivered to %s (msg_id: %s)", login, parsed.get("message_id"))
                return True, ""
            err = parsed.get("description") or parsed.get("code") or "Неизвестная ошибка API"
            logger.warning("Yandex Messenger API returned error for %s: %s", login, err)
            return False, f"Ошибка Яндекс API: {err}"
    except urllib.error.HTTPError as ex:
        err_body = ex.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
            err_msg = parsed.get("description") or parsed.get("code") or f"HTTP {ex.code}"
        except Exception:
            err_msg = f"HTTP {ex.code}: {ex.reason}"
        logger.warning("HTTP error sending Yandex Messenger message to %s: %s", login, err_msg)
        return False, f"Ошибка Яндекс API: {err_msg}"
    except Exception as ex:
        logger.warning("Failed to send Yandex Messenger message to %s: %s", login, ex)
        return False, f"Сетевая ошибка: {ex}"


async def send_yandex_message(
    login: str,
    text: str,
    token: str,
    important: bool = False,
    timeout_sec: int = 8,
) -> Tuple[bool, str]:
    """
    Асинхронная отправка текстового сообщения в личный чат сотрудника Яндекс Мессенджера.
    Возвращает (success, error_description).
    """
    clean_login = (login or "").strip().lower()
    clean_token = (token or "").strip()
    clean_text = (text or "").strip()

    if not clean_login:
        return False, "Не указан логин/email получателя"
    if not clean_token:
        return False, "Не указан токен Яндекс бота"
    if not clean_text:
        return False, "Пустой текст сообщения"

    return await asyncio.to_thread(
        _sync_send_yandex_message,
        clean_login,
        clean_text,
        clean_token,
        important,
        timeout_sec,
    )
