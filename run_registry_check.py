import asyncio
import json
import logging
import os
import shutil
import sys
from datetime import datetime, date

# При запуске из exe (PyInstaller): конфиг и пути ищем рядом с exe
if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)
    os.chdir(_exe_dir)

from config import (
    TELEGRAM_BOT_TOKEN,
    ATRACKER_BASE_URL,
    ATRACKER_USERNAME,
    ATRACKER_PASSWORD,
    ATRACKER_ASSETS_SERVICE_ID,
    ATRACKER_MARK_SERVICE_ID,
    ATRACKER_UPLOAD_DOC_SERVICE_ID,
    ATRACKER_ASSET_INFO_SERVICE_ID,
    REPORT_GROUP_ID,
    REGISTRY_FILE_PATH,
    REGISTRY_NOTIFY_GROUP_ID,
    REGISTRY_STATE_FILE,
    REGISTRY_PROCESSED_DIR,
)
from atracker_client import ATrackerClient
from registry_reader import load_registry
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Группа для оповещений: из конфига или REPORT_GROUP_ID
NOTIFY_GROUP_ID = REGISTRY_NOTIFY_GROUP_ID if REGISTRY_NOTIFY_GROUP_ID is not None else REPORT_GROUP_ID

# Оповещать только по строкам, где дата уже наступила (увольнение/перемещение в прошлом или сегодня)
ONLY_PAST_OR_TODAY = True
# Только эти типы документов: Увольнение, Перевод (Прием не смотрим)
REGISTRY_DOC_TYPES = ("Увольнение", "Перевод")


def _load_state() -> dict:
    path = os.path.abspath(REGISTRY_STATE_FILE)
    if not os.path.isfile(path):
        return {"processed": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and "processed" in data else {"processed": {}}
    except Exception:
        return {"processed": {}}


def _save_state(state: dict) -> None:
    path = os.path.abspath(REGISTRY_STATE_FILE)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def _already_processed(state: dict, file_path: str) -> bool:
    path = os.path.abspath(file_path)
    if path not in state.get("processed", {}):
        return False
    try:
        return state["processed"][path]["mtime"] == os.path.getmtime(path)
    except OSError:
        return False


def _mark_processed(state: dict, file_path: str) -> None:
    path = os.path.abspath(file_path)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return
    state.setdefault("processed", {})[path] = {
        "mtime": mtime,
        "processed_at": datetime.now().isoformat(),
    }


def _collect_files(path_arg: str) -> list:
    path = os.path.abspath(path_arg)
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        return [path] if ext in (".xls", ".xlsx") else []
    if os.path.isdir(path):
        files = []
        for name in os.listdir(path):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if not os.path.isfile(full):
                continue
            if os.path.splitext(name)[1].lower() in (".xls", ".xlsx"):
                files.append(full)
        return sorted(files)
    return []


def _move_to_processed(file_path: str) -> bool:
    if not REGISTRY_PROCESSED_DIR:
        return False
    dest_dir = os.path.abspath(REGISTRY_PROCESSED_DIR)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        base = os.path.basename(file_path)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
        new_name = f"{stamp}_{base}"
        dest = os.path.join(dest_dir, new_name)
        shutil.move(file_path, dest)
        logger.info("Файл перемещён в %s", dest)
        return True
    except Exception as e:
        logger.warning("Не удалось переместить файл в processed: %s", e)
        return False


def _format_asset_list(assets: list) -> str:
    if not assets:
        return ""
    lines = []
    for a in assets[:15]:
        name = a.get("sFullName") or a.get("Name") or f"ID {a.get('ID', '?')}"
        lines.append(f"  • {name}")
    if len(assets) > 15:
        lines.append(f"  … и ещё {len(assets) - 15}")
    return "\n".join(lines)


async def _process_rows(rows: list, atracker: ATrackerClient, bot: Bot) -> tuple:
    today = date.today()
    sent = 0
    errors = 0
    for row in rows:
        doc_type = (row.get("Документ") or "").strip()
        if doc_type not in REGISTRY_DOC_TYPES:
            continue
        fio = (row.get("Сотрудник") or "").strip()
        if not fio:
            continue
        dt = row.get("date")
        if ONLY_PAST_OR_TODAY and dt is not None:
            d = dt.date() if hasattr(dt, "date") else dt
            if d > today:
                continue
        try:
            assets = await atracker.get_assets_by_fio(fio)
        except Exception as e:
            logger.exception("Ошибка запроса активов по ФИО %s: %s", fio, e)
            errors += 1
            continue
        if not assets:
            continue
        sheet_name = row.get("sheet_name", "")
        date_str = row.get("date_raw") or (dt.strftime("%d.%m.%Y") if dt else "")
        doc = (row.get("Документ") or "").strip() or "—"
        position = (row.get("Должность") or "").strip() or "—"
        note = (row.get("Примечание") or "").strip() or "—"
        asset_list = _format_asset_list(assets)
        text = (
            "<b>Реестр: у сотрудника числится техника</b>\n\n"
            f"Лист: {sheet_name}\n"
            f"Дата: {date_str}\n"
            f"Сотрудник: <b>{fio}</b>\n"
            f"Документ: {doc}\n"
            f"Должность: {position}\n"
            f"Примечание: {note}\n\n"
            f"<b>Техника в A-Tracker ({len(assets)}):</b>\n{asset_list}"
        )
        try:
            await bot.send_message(NOTIFY_GROUP_ID, text)
            sent += 1
        except Exception as e:
            logger.exception("Не удалось отправить сообщение в группу %s: %s", NOTIFY_GROUP_ID, e)
            errors += 1
        await asyncio.sleep(0.3)
    return sent, errors


async def main() -> None:
    path_arg = sys.argv[1] if len(sys.argv) > 1 else REGISTRY_FILE_PATH
    if not path_arg:
        logger.error("Не задан путь к файлу/папке реестра. Укажите в config.REGISTRY_FILE_PATH или аргументом.")
        return

    files = _collect_files(path_arg)
    if not files:
        logger.warning("Нет .xls/.xlsx файлов для обработки: %s", path_arg)
        return

    state = _load_state()
    to_process = [f for f in files if not _already_processed(state, f)]
    skipped = len(files) - len(to_process)
    if skipped:
        logger.info("Пропущено уже обработанных файлов: %s", skipped)
    if not to_process:
        logger.info("Все файлы уже обработаны.")
        return

    atracker = ATrackerClient(
        base_url=ATRACKER_BASE_URL,
        username=ATRACKER_USERNAME,
        password=ATRACKER_PASSWORD,
        assets_service_id=ATRACKER_ASSETS_SERVICE_ID,
        mark_service_id=ATRACKER_MARK_SERVICE_ID,
        upload_doc_service_id=ATRACKER_UPLOAD_DOC_SERVICE_ID,
        asset_info_service_id=ATRACKER_ASSET_INFO_SERVICE_ID,
    )
    bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    total_sent = 0
    total_errors = 0
    for file_path in to_process:
        logger.info("Обработка: %s", file_path)
        rows = load_registry(file_path)
        if not rows:
            logger.warning("  Нет строк с «Сотрудник» — помечаем обработанным и пропускаем.")
            _mark_processed(state, file_path)
            _save_state(state)
            if REGISTRY_PROCESSED_DIR:
                _move_to_processed(file_path)
            continue
        sent, errors = await _process_rows(rows, atracker, bot)
        total_sent += sent
        total_errors += errors
        _mark_processed(state, file_path)
        _save_state(state)
        if REGISTRY_PROCESSED_DIR:
            _move_to_processed(file_path)

    await bot.session.close()
    logger.info("Готово: обработано файлов %s, отправлено оповещений %s, ошибок %s.", len(to_process), total_sent, total_errors)


if __name__ == "__main__":
    asyncio.run(main())
