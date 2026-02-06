# -*- coding: utf-8 -*-
"""
Синхронизация AD → A-Tracker (односторонняя).
Загрузка пользователей AD из JSON-файла (выгрузка PowerShell), сверка по sPersNo → почта → ФИО,
обновление в A-Tracker: sFullName, sLoginName, sEmail, sPersNo (табельный номер = objectSid). ID не меняется.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _norm(s: Optional[str]) -> str:
    """Нормализация строки для сравнения: нижний регистр, пробелы по краям."""
    if s is None:
        return ""
    return (s or "").strip().lower()


def load_ad_from_file(path: str) -> List[Dict[str, Any]]:
    """
    Загрузить пользователей AD из JSON-файла.
    Ожидаемый формат: массив объектов с полями cn, mail, sAMAccountName, objectSid (опционально).
    """
    if not path or not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, list):
        return []
    return data


def load_ad() -> List[Dict[str, Any]]:
    """Загрузить пользователей AD из JSON-файла (config.AD_EXPORT_PATH; при exe — рядом с exe)."""
    import config
    import sys
    path = getattr(config, "AD_EXPORT_PATH", "") or ""
    if not path:
        return []
    if not os.path.isabs(path):
        base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.getcwd()
        path = os.path.join(base, path)
    return load_ad_from_file(path)


def build_atracker_index(
    employees: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict], Dict[str, Dict], Dict[str, Dict]]:
    """
    Индексы для сверки с AD (приоритет: sPersNo → почта → ФИО).
    by_pers_no: табельный номер (sPersNo) — уникальный ключ, по нему обновляем ФИО без потери техники.
    by_email: почта (sEmail); используется, если sPersNo не проставлен.
    by_fio: ФИО (sFullName); используется, если нет ни sPersNo, ни почты.
    """
    by_pers_no: Dict[str, Dict] = {}
    by_email: Dict[str, Dict] = {}
    by_fio: Dict[str, Dict] = {}
    for emp in employees:
        if not isinstance(emp, dict):
            continue
        atr_id = emp.get("ID")
        if atr_id is None:
            continue
        pers_no = (emp.get("sPersNo") or emp.get("spersno") or "").strip()
        email = (emp.get("sEmail") or emp.get("semail") or "").strip()
        fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
        npers = pers_no.lower() if pers_no else ""
        nemail = _norm(email)
        nfio = _norm(fio)
        if npers and npers not in by_pers_no:
            by_pers_no[npers] = emp
        if nemail and nemail not in by_email:
            by_email[nemail] = emp
        if nfio and nfio not in by_fio:
            by_fio[nfio] = emp
    return by_pers_no, by_email, by_fio


def find_atracker_match(
    ad_user: Dict[str, Any],
    by_pers_no: Dict[str, Dict],
    by_email: Dict[str, Dict],
    by_fio: Dict[str, Dict],
) -> Optional[Dict[str, Any]]:
    """
    Найти сотрудника в A-Tracker по данным из AD.
    Приоритет: 1) sPersNo (objectSid) — уникальный, по нему ловим смену ФИО без потери техники;
    2) если sPersNo не проставлен — по почте; 3) если почты нет — по ФИО.
    """
    ad_sid = (ad_user.get("objectSid") or ad_user.get("ObjectSid") or "") or ""
    if isinstance(ad_sid, list):
        ad_sid = (ad_sid[0] or "") if ad_sid else ""
    ad_sid = str(ad_sid).strip().lower()
    ad_mail = (ad_user.get("mail") or ad_user.get("Mail") or "") or ""
    if isinstance(ad_mail, list):
        ad_mail = (ad_mail[0] or "") if ad_mail else ""
    ad_mail = _norm(ad_mail)
    ad_cn = (ad_user.get("cn") or ad_user.get("CN") or "") or ""
    if isinstance(ad_cn, list):
        ad_cn = (ad_cn[0] or "") if ad_cn else ""
    ad_fio = _norm(ad_cn)

    if ad_sid and ad_sid in by_pers_no:
        return by_pers_no[ad_sid]
    if ad_mail and ad_mail in by_email:
        return by_email[ad_mail]
    if ad_fio and ad_fio in by_fio:
        return by_fio[ad_fio]
    return None


def ad_values(ad_user: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """Извлечь из записи AD: ФИО, логин, почту, табельный номер (objectSid → sPersNo)."""
    cn = (ad_user.get("cn") or ad_user.get("CN") or "") or ""
    if isinstance(cn, list):
        cn = (cn[0] or "") if cn else ""
    mail = (ad_user.get("mail") or ad_user.get("Mail") or "") or ""
    if isinstance(mail, list):
        mail = (mail[0] or "") if mail else ""
    login = (ad_user.get("sAMAccountName") or ad_user.get("samaccountname") or "") or ""
    if isinstance(login, list):
        login = (login[0] or "") if login else ""
    pers_no = (ad_user.get("objectSid") or ad_user.get("ObjectSid") or "") or ""
    if isinstance(pers_no, list):
        pers_no = (pers_no[0] or "") if pers_no else ""
    return (str(cn).strip(), str(login).strip(), str(mail).strip(), str(pers_no).strip())


async def run_sync() -> Dict[str, int]:
    """
    Выполнить синхронизацию AD → A-Tracker.
    Возвращает счётчики: updated, skipped, only_ad, only_atracker, errors.
    """
    from atracker_client import ATrackerClient
    import config

    stats = {"updated": 0, "skipped": 0, "only_ad": 0, "only_atracker": 0, "errors": 0}

    # Загрузка AD
    ad_users = load_ad()
    if not ad_users:
        logger.warning("Список пользователей AD пуст (файл не найден или LDAP не вернул записей)")
        return stats

    logger.info("Загружено из AD: %d пользователей", len(ad_users))

    # Клиент A-Tracker
    list_id = getattr(config, "ATRACKER_EMPLOYEES_LIST_SERVICE_ID", None)
    update_id = getattr(config, "ATRACKER_EMPLOYEE_UPDATE_SERVICE_ID", None)
    if not list_id or not update_id:
        logger.warning("Не заданы ATRACKER_EMPLOYEES_LIST_SERVICE_ID или ATRACKER_EMPLOYEE_UPDATE_SERVICE_ID")
        return stats

    client = ATrackerClient(
        base_url=config.ATRACKER_BASE_URL,
        username=config.ATRACKER_USERNAME,
        password=config.ATRACKER_PASSWORD,
        assets_service_id=config.ATRACKER_ASSETS_SERVICE_ID,
        mark_service_id=config.ATRACKER_MARK_SERVICE_ID,
        upload_doc_service_id=config.ATRACKER_UPLOAD_DOC_SERVICE_ID,
        asset_info_service_id=getattr(config, "ATRACKER_ASSET_INFO_SERVICE_ID", None),
        employees_list_service_id=list_id,
        employee_update_service_id=update_id,
    )

    # Загрузка сотрудников A-Tracker
    try:
        atr_employees = await client.get_employees()
    except Exception as e:
        logger.exception("Ошибка загрузки сотрудников A-Tracker: %s", e)
        stats["errors"] += 1
        return stats

    logger.info("Загружено из A-Tracker: %d сотрудников", len(atr_employees))

    by_pers_no, by_email, by_fio = build_atracker_index(atr_employees)
    matched_atr_ids = set()

    for ad_user in ad_users:
        match = find_atracker_match(ad_user, by_pers_no, by_email, by_fio)
        if not match:
            stats["only_ad"] += 1
            continue

        atr_id = match.get("ID")
        if atr_id is None:
            continue
        matched_atr_ids.add(atr_id)

        ad_fio, ad_login, ad_mail, ad_pers_no = ad_values(ad_user)
        atr_fio = (match.get("sFullName") or match.get("sfullname") or "").strip()
        atr_login = (match.get("sLoginName") or match.get("sloginname") or "").strip()
        atr_mail = (match.get("sEmail") or match.get("semail") or "").strip()
        atr_pers_no = (match.get("sPersNo") or match.get("spersno") or "").strip()

        if ad_fio == atr_fio and ad_login == atr_login and ad_mail == atr_mail and ad_pers_no == atr_pers_no:
            stats["skipped"] += 1
            continue

        try:
            await client.update_employee(
                employee_id=int(atr_id),
                s_full_name=ad_fio,
                s_login_name=ad_login,
                s_email=ad_mail,
                s_pers_no=ad_pers_no,
            )
            stats["updated"] += 1
            logger.debug("Обновлён ID=%s: %s", atr_id, ad_fio or ad_login)
        except Exception as e:
            logger.warning("Ошибка обновления сотрудника ID=%s: %s", atr_id, e)
            stats["errors"] += 1

    stats["only_atracker"] = len(atr_employees) - len(matched_atr_ids)
    logger.info(
        "Синхронизация завершена: обновлено=%d, без изменений=%d, только в AD=%d, только в A-Tracker=%d, ошибок=%d",
        stats["updated"],
        stats["skipped"],
        stats["only_ad"],
        stats["only_atracker"],
        stats["errors"],
    )
    return stats


def run_sync_sync() -> Dict[str, int]:
    """Синхронная обёртка для run_sync (для вызова из планировщика и т.п.)."""
    return asyncio.run(run_sync())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_sync_sync()
