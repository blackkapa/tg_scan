#!/usr/bin/env python3
"""
Выгрузка сотрудников из A-Tracker и поиск дублей по ФИО.
Запуск: python3 check_duplicates_fio.py
"""
import asyncio
import sys
import os

if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)
    os.chdir(_exe_dir)

import config
from atracker_client import ATrackerClient


def _norm_fio(s: str) -> str:
    """Нормализация ФИО для сравнения: пробелы, нижний регистр."""
    return " ".join((s or "").split()).lower().strip()


async def main():
    client = ATrackerClient(
        base_url=config.ATRACKER_BASE_URL,
        username=config.ATRACKER_USERNAME,
        password=config.ATRACKER_PASSWORD,
        assets_service_id=config.ATRACKER_ASSETS_SERVICE_ID,
        mark_service_id=config.ATRACKER_MARK_SERVICE_ID,
        upload_doc_service_id=config.ATRACKER_UPLOAD_DOC_SERVICE_ID,
        asset_info_service_id=config.ATRACKER_ASSET_INFO_SERVICE_ID,
        employees_list_service_id=config.ATRACKER_EMPLOYEES_LIST_SERVICE_ID,
    )

    print("Загружаю сотрудников из A-Tracker…")
    employees = await client.get_employees()
    print(f"Всего сотрудников: {len(employees)}")

    # Группировка по нормализованному ФИО
    by_fio: dict[str, list[dict]] = {}
    for emp in employees:
        if not isinstance(emp, dict):
            continue
        fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
        n = _norm_fio(fio)
        if not n:
            continue
        by_fio.setdefault(n, []).append(emp)

    # Дубли
    duplicates = [(fio, items) for fio, items in by_fio.items() if len(items) > 1]
    duplicates.sort(key=lambda x: -len(x[1]))

    if not duplicates:
        print("\nДублей по ФИО не найдено.")
        return

    print(f"\nНайдено дублей по ФИО: {len(duplicates)}")
    print(f"Всего записей-дублей: {sum(len(items) for _, items in duplicates)}\n")
    print("-" * 80)

    for norm_fio, items in duplicates:
        # Показываем первое вхождение как «каноническое» ФИО
        first_fio = (items[0].get("sFullName") or items[0].get("sfullname") or "").strip()
        print(f"\nФИО: {first_fio}")
        print(f"  Записей: {len(items)}")
        for emp in items:
            eid = emp.get("ID", "?")
            login = (emp.get("sLoginName") or emp.get("sloginname") or "").strip()
            email = (emp.get("sEmail") or emp.get("semail") or "").strip()
            pers_no = (emp.get("sPersNo") or emp.get("spersno") or "").strip()
            print(f"    ID={eid}  логин={login}  почта={email}  sPersNo={pers_no[:20] if pers_no else '-'}...")
        print()


if __name__ == "__main__":
    asyncio.run(main())
