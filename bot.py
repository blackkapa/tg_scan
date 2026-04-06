import asyncio
import html
import logging
import os
import sys
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional, Tuple
from pathlib import Path

if getattr(sys, "frozen", False):
    _exe_dir = os.path.dirname(sys.executable)
    if _exe_dir not in sys.path:
        sys.path.insert(0, _exe_dir)
    os.chdir(_exe_dir)

import cv2
import numpy as np
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InputMediaPhoto,
    ErrorEvent,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart, StateFilter, ExceptionTypeFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.enums import ParseMode

from config import (
    TELEGRAM_BOT_TOKEN,
    ATRACKER_BASE_URL,
    ATRACKER_USERNAME,
    ATRACKER_PASSWORD,
    ATRACKER_ASSETS_SERVICE_ID,
    ATRACKER_MARK_SERVICE_ID,
    ATRACKER_UPLOAD_DOC_SERVICE_ID,
    ATRACKER_ASSET_INFO_SERVICE_ID,
    ATRACKER_EMPLOYEES_LIST_SERVICE_ID,
    REPORT_GROUP_ID,
    EMAIL_DOMAIN_ALLOWED,
    ADMIN_TELEGRAM_IDS,
)
from atracker_client import ATrackerClient, inventory_number_from_atracker_dict
from auth_by_email import (
    find_employee_by_input,
    create_code,
    check_code,
    send_code_email,
)


logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

atracker = ATrackerClient(
    base_url=ATRACKER_BASE_URL,
    username=ATRACKER_USERNAME,
    password=ATRACKER_PASSWORD,
    assets_service_id=ATRACKER_ASSETS_SERVICE_ID,
    mark_service_id=ATRACKER_MARK_SERVICE_ID,
    upload_doc_service_id=ATRACKER_UPLOAD_DOC_SERVICE_ID,
    asset_info_service_id=ATRACKER_ASSET_INFO_SERVICE_ID,
    employees_list_service_id=ATRACKER_EMPLOYEES_LIST_SERVICE_ID,
)

report_message_to_user: Dict[Tuple[int, int], int] = {}
user_to_group_thread: Dict[int, Tuple[int, int]] = {}


class InvStates(StatesGroup):
    waiting_identifier = State()  # ФИО / логин / почта для авторизации
    waiting_code = State()        # ввод кода из письма
    waiting_fio = State()
    inventory = State()
    reporting_equipment = State()
    reporting_comment = State()
    # Несоответствие: выбор актива → причина → доп. комментарий/фото
    discrepancy_asset = State()
    discrepancy_reason = State()
    discrepancy_reason_other = State()
    discrepancy_extra = State()
    # Нет наклейки с QR: сбор фото техники и серийного номера → отправка в группу
    no_qr_photo = State()
    # Идентифицировать устройство: сканирование QR → показ владельца актива
    identify_device = State()


# Клавиатуры
def kb_inventory() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Завершить инвентаризацию")]],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def kb_reporting() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Отправить заявку")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def kb_reporting_comment() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Пропустить")],
            [KeyboardButton(text="Отмена")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


@dp.error(ExceptionTypeFilter(TelegramForbiddenError))
async def handle_user_blocked_bot(event: ErrorEvent):
    update = event.update
    user_id = "?"
    if getattr(update, "message", None) and update.message.from_user:
        user_id = update.message.from_user.id
    elif getattr(update, "callback_query", None) and update.callback_query.from_user:
        user_id = update.callback_query.from_user.id
    logging.info("Пользователь %s заблокировал бота, update_id=%s", user_id, update.update_id)
    return True 


def _identifier_prompt(user_id: Optional[int]) -> str:
    if user_id is not None and user_id in ADMIN_TELEGRAM_IDS:
        return (
            "Режим администратора. Введите <b>ФИО</b>, <b>логин</b> или <b>почту</b> сотрудника — "
            "код не требуется."
        )
    return (
        r"Введите <b>корпоративную почту</b> (@asg.ru), <b>ФИО</b> или <b>логин</b> (без ovp)  — "
        "на почту придёт код для входа."
    )


def _format_username(from_user) -> str:
    """Форматирует username для подписи в заявках: @username или id или «—»."""
    if not from_user:
        return "—"
    if getattr(from_user, "username", None):
        return f"@{from_user.username}"
    return str(getattr(from_user, "id", "—"))


async def _safe_delete_message(msg: Optional[Message]) -> None:
    """Удаляет сообщение бота; при ошибке (например, уже удалено) молча игнорирует."""
    if not msg:
        return
    try:
        await bot.delete_message(chat_id=msg.chat.id, message_id=msg.message_id)
    except TelegramBadRequest:
        pass


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user and message.from_user.id in user_to_group_thread:
        del user_to_group_thread[message.from_user.id]
    user_id = message.from_user.id if message.from_user else None
    await message.answer(
        "Здравствуйте! Я помогу с инвентаризацией техники.\n\n"
        + _identifier_prompt(user_id),
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(InvStates.waiting_identifier)


@dp.message(F.text == "Начать заново")
async def btn_start_over(message: Message, state: FSMContext):
    had_thread = message.from_user and message.from_user.id in user_to_group_thread
    await state.clear()
    if message.from_user and message.from_user.id in user_to_group_thread:
        del user_to_group_thread[message.from_user.id]
    if had_thread:
        await message.answer(
            "Напоминание: ответы по вашей заявке больше не будут приходить сюда. "
            "Если ждёте ответ — свяжитесь с системотехником."
        )
    user_id = message.from_user.id if message.from_user else None
    await message.answer(
        _identifier_prompt(user_id),
        reply_markup=ReplyKeyboardRemove(),
    )
    await state.set_state(InvStates.waiting_identifier)


@dp.callback_query(F.data == "start_over")
async def callback_start_over(callback: CallbackQuery, state: FSMContext):
    """Инлайн-кнопка «Начать заново» — то же, что /start."""
    had_thread = callback.from_user and callback.from_user.id in user_to_group_thread
    await state.clear()
    if callback.from_user and callback.from_user.id in user_to_group_thread:
        del user_to_group_thread[callback.from_user.id]
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        if had_thread:
            await callback.message.answer(
                "Напоминание: ответы по вашей заявке больше не будут приходить сюда. "
                "Если ждёте ответ — свяжитесь с системотехником."
            )
        user_id = callback.from_user.id if callback.from_user else None
        await callback.message.answer(
            _identifier_prompt(user_id),
            reply_markup=ReplyKeyboardRemove(),
        )
    await callback.answer()
    await state.set_state(InvStates.waiting_identifier)


@dp.message(F.reply_to_message, F.chat.id != REPORT_GROUP_ID)
async def handle_user_reply_to_bot(message: Message, state: FSMContext):
    if not message.from_user:
        return
    user_id = message.from_user.id
    reply_to = message.reply_to_message
    if not reply_to.from_user:
        return
    me = await bot.get_me()
    if reply_to.from_user.id != me.id:
        return  # ответ не на сообщение бота
    
    # Проверяем, есть ли активная переписка по заявке
    thread = user_to_group_thread.get(user_id)
    if not thread:
        return  # нет активной переписки, пропускаем другим обработчикам
    
    current_state = await state.get_state()
    if current_state and current_state not in (None, InvStates.waiting_identifier, InvStates.waiting_code, InvStates.waiting_fio):
        return
    
    group_chat_id, group_message_id = thread
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text:
        await message.answer("Пожалуйста сделайте Replay, напишите текст сообщения для ответа.")
        return
    
    try:
        # Отправляем ответ в группу как ответ на исходное сообщение с заявкой
        sent_to_group = await bot.send_message(
            group_chat_id,
            f"Ответ заявителя:\n\n{html.escape(reply_text)}",
            reply_to_message_id=group_message_id,
        )
        # Сохраняем связь нового сообщения с заявителем, чтобы ответы на него тоже доходили
        report_message_to_user[(group_chat_id, sent_to_group.message_id)] = user_id
        logging.info(f"Сохранена связь ответа заявителя: chat_id={group_chat_id}, msg_id={sent_to_group.message_id}, user_id={user_id}")
        await message.answer("Ответ отправлен.")
    except Exception as e:
        logging.exception("Ошибка при отправке ответа заявителя")
        await message.answer(f"Не удалось отправить ответ. Попробуйте позже или напишите системотехнику напрямую.")


def _is_asset_inventoried(a: Dict) -> bool:
    return (
        a.get("IsInventoried") == True
        or a.get("IsInventoried") == "True"
        or a.get("InventoryStatus") == "Completed"
        or a.get("InventoryStatus") == "Проведена"
        or a.get("bInventoried") == True
        or a.get("bInventoried") == 1
    )


def build_discrepancy_asset_keyboard(assets: List[Dict]):
    kb = InlineKeyboardBuilder()
    for a in assets:
        asset_id = int(a["ID"])
        asset_name = a.get("sFullName", f"ID {asset_id}")
        button_text = asset_name if len(asset_name) <= 50 else asset_name[:47] + "..."
        kb.button(text=button_text, callback_data=f"discrepancy_asset_{asset_id}")
    kb.button(text="Отмена", callback_data="discrepancy_cancel")
    kb.adjust(1)
    return kb.as_markup()


def build_assets_list_message(
    assets: List[Dict],
    include_start_inventory: bool = False,
) -> tuple:
    # Показывать «Начать инвентаризацию», если есть хотя бы один непроведённый актив
    if not include_start_inventory and assets:
        include_start_inventory = any(not _is_asset_inventoried(a) for a in assets)

    text_lines = ["<b>Ваши активы:</b>\n"]
    kb = InlineKeyboardBuilder()

    for a in assets:
        asset_id = int(a["ID"])
        asset_name = a.get("sFullName", f"ID {asset_id}")
        serial = a.get("sSerialNo") or "-"
        is_inventoried = _is_asset_inventoried(a)
        status_icon = "✅" if is_inventoried else "❌"
        button_text = f"{status_icon} {asset_name}"
        if len(button_text) > 50:
            button_text = f"{status_icon} {asset_name[:47]}..."
        kb.button(text=button_text, callback_data=f"asset_{asset_id}")
        status_text = "проведена" if is_inventoried else "не проведена"
        text_lines.append(
            f"{status_icon} <b>ID {asset_id}</b>: {asset_name}\n"
            f"   Серийный: {serial} | Инвентаризация: {status_text}"
        )

    text_lines.append("\nВыберите актив для просмотра или действие ниже:")
    kb.button(text="У меня есть еще техника!", callback_data="report_equipment")
    kb.button(text="Сообщить о несоответствии", callback_data="report_discrepancy")
    kb.button(text="Узнать чье устройство по QR-коду", callback_data="identify_device")
    kb.button(text="Завершить", callback_data="finish_session")
    if include_start_inventory:
        kb.button(text="Начать инвентаризацию", callback_data="start_inventory")
    kb.adjust(1)
    return text_lines, kb


async def _return_to_asset_list(
    state: FSMContext,
    answer_fn,
    cancel_text: str,
    *,
    remove_reply_keyboard: bool = False,
    message_to_delete: Optional[Message] = None,
) -> None:
    await _safe_delete_message(message_to_delete)
    data = await state.get_data()
    assets = data.get("assets") or {}
    await state.set_state(InvStates.waiting_fio)
    if cancel_text:
        if remove_reply_keyboard:
            await answer_fn(cancel_text, reply_markup=ReplyKeyboardRemove())
        else:
            await answer_fn(cancel_text)
    if assets:
        assets_list = list(assets.values())
        text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
        await answer_fn("\n".join(text_lines), reply_markup=kb.as_markup())
    else:
        await state.set_state(InvStates.waiting_identifier)
        await answer_fn("Введите ФИО, логин или почту (@asg.ru) — на почту придёт код для входа.")


async def _send_to_report_group(
    report_text: str,
    photos: List[str],
    submitter_user_id: int,
    log_label: str = "заявка",
) -> Tuple[bool, Optional[str]]:
    """
    Отправляет текст и фото в группу заявок, регистрирует в report_message_to_user.
    Возвращает (True, None) при успехе, (False, сообщение_об_ошибке) при ошибке.
    """
    try:
        sent_text = await bot.send_message(REPORT_GROUP_ID, report_text)
        report_message_to_user[(REPORT_GROUP_ID, sent_text.message_id)] = submitter_user_id
        logging.info("Сохранена %s: chat_id=%s, msg_id=%s, user_id=%s", log_label, REPORT_GROUP_ID, sent_text.message_id, submitter_user_id)
        if len(photos) == 1:
            sent_photo = await bot.send_photo(REPORT_GROUP_ID, photos[0])
            report_message_to_user[(REPORT_GROUP_ID, sent_photo.message_id)] = submitter_user_id
        elif len(photos) > 1:
            media = [InputMediaPhoto(media=fid) for fid in photos]
            sent_media = await bot.send_media_group(REPORT_GROUP_ID, media)
            for m in sent_media:
                report_message_to_user[(REPORT_GROUP_ID, m.message_id)] = submitter_user_id
        return (True, None)
    except TelegramBadRequest as e:
        logging.exception("Ошибка при отправке %s в группу", log_label)
        err = str(e).lower()
        if "chat not found" in err or "chat_not_found" in err:
            return (False, "Не удалось отправить. Обратитесь к системотехнику.")
        return (False, "Не удалось отправить. Попробуйте позже.")
    except Exception:
        logging.exception("Ошибка при отправке %s в группу", log_label)
        return (False, "Не удалось отправить. Попробуйте позже.")


async def _return_user_to_asset_list_after_report(
    state: FSMContext,
    submitter_user_id: int,
    answer_fn,
    success_message: str,
    *,
    remove_reply_keyboard: bool = False,
) -> None:
    """После успешной отправки заявки в группу: сброс состояния, возврат к списку активов."""
    data = await state.get_data()
    fio = data.get("fio", "—")
    assets = data.get("assets") or {}
    await state.clear()
    await state.set_state(InvStates.waiting_fio)
    await state.update_data(fio=fio, assets=assets)
    if submitter_user_id in user_to_group_thread:
        del user_to_group_thread[submitter_user_id]
    if remove_reply_keyboard:
        await answer_fn(success_message, reply_markup=ReplyKeyboardRemove())
    else:
        await answer_fn(success_message)
    if assets:
        assets_list = list(assets.values())
        text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
        await answer_fn("\n".join(text_lines), reply_markup=kb.as_markup())
    else:
        await state.set_state(InvStates.waiting_identifier)
        await answer_fn("Введите ФИО, логин или почту (@asg.ru) — на почту придёт код для входа.")


@dp.message(F.text, StateFilter(InvStates.waiting_identifier))
async def handle_identifier(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if not text:
        await message.answer("Введите ФИО, логин или почту @asg.ru.")
        return
    is_admin = message.from_user and message.from_user.id in ADMIN_TELEGRAM_IDS
    await message.answer("Проверяю данные…" if is_admin else "Проверяю данные и отправляю код на почту…")
    try:
        employees = await atracker.get_employees()
    except Exception as e:
        logging.exception("Ошибка загрузки сотрудников A-Tracker: %s", e)
        await message.answer("Не удалось проверить данные. Попробуйте позже или обратитесь к администратору.")
        return
    fio, email, err = find_employee_by_input(employees, text, EMAIL_DOMAIN_ALLOWED)
    if err:
        await message.answer(err)
        return
    if not fio or not email:
        await message.answer("Сотрудник не найден или почта не указана. Обратитесь к системотехнику.")
        return
    if is_admin:
        # Админы: без кода — сразу загружаем активы выбранного сотрудника
        await state.update_data(fio=fio)
        await state.set_state(InvStates.waiting_fio)
        await message.answer(f"Загружаю активы для <b>{html.escape(fio)}</b>…")
        try:
            assets = await atracker.get_assets_by_fio(fio)
        except Exception as e:
            logging.exception("Ошибка при загрузке активов: %s", e)
            await message.answer("Не удалось загрузить список активов. Попробуйте позже или обратитесь к системотехнику.")
            return
        if not assets:
            await message.answer(
                "По указанным данным активы не найдены. Обратитесь к системотехнику."
            )
            return
        assets_by_id = {int(a["ID"]): a for a in assets}
        await state.update_data(assets=assets_by_id)
        text_lines, kb = build_assets_list_message(assets, include_start_inventory=False)
        await message.answer("\n".join(text_lines), reply_markup=kb.as_markup())
        return
    code = create_code(fio, email)
    ok, send_err = send_code_email(email, code)
    if not ok:
        await message.answer(send_err)
        return
    await message.answer(
        f"Код отправлен на почту <b>{html.escape(email)}</b>. Введите его ниже (действует 10 минут)."
    )
    await state.set_state(InvStates.waiting_code)


@dp.message(F.text, StateFilter(InvStates.waiting_code))
async def handle_code(message: Message, state: FSMContext):
    code = (message.text or "").strip()
    if not code:
        await message.answer("Введите код из письма.")
        return
    result = check_code(code)
    if not result:
        await message.answer("Код неверный или истёк. Введите ФИО/логин/почту снова для нового кода.")
        return
    fio, email = result
    await state.update_data(fio=fio)
    await state.set_state(InvStates.waiting_fio)
    await message.answer("Вход выполнен. Загружаю ваши активы…")
    try:
        assets = await atracker.get_assets_by_fio(fio)
    except Exception as e:
        logging.exception("Ошибка при загрузке активов: %s", e)
        await message.answer("Не удалось загрузить список активов. Попробуйте позже или обратитесь к системотехнику.")
        return
    if not assets:
        await message.answer(
            "По вашим данным активы не найдены. Обратитесь к системотехнику."
        )
        return
    assets_by_id = {int(a["ID"]): a for a in assets}
    await state.update_data(assets=assets_by_id)
    text_lines, kb = build_assets_list_message(assets, include_start_inventory=False)
    await message.answer("\n".join(text_lines), reply_markup=kb.as_markup())


@dp.message(F.text, StateFilter(InvStates.waiting_fio))
async def handle_fio(message: Message, state: FSMContext):
    fio = (message.text or "").strip()
    if not fio:
        await message.answer("Не удалось распознать ФИО. Введите, пожалуйста, текстом.")
        return

    # Уже есть список активов по другому сотруднику — поиск по новому ФИО только после «Завершить»
    data = await state.get_data()
    if data.get("assets"):
        await message.answer(
            "Чтобы искать по другому сотруднику, нажмите «Завершить», затем введите ФИО другого сотрудника."
        )
        return

    # Если это ссылка с QR-кодом (содержит ID=), пропускаем - обработает другой обработчик
    if "ID=" in fio:
        return

    await state.update_data(fio=fio)
    await message.answer("Ищу ваши активы… Подождите, пожалуйста.")

    try:
        assets = await atracker.get_assets_by_fio(fio)
    except Exception as e:
        logging.exception("Ошибка при обращении к A-Tracker")
        await message.answer(
            "Не удалось загрузить список активов. Попробуйте позже или обратитесь к системотехнику."
        )
        return

    if not assets:
        await message.answer(
            "По указанному ФИО активы не найдены.\n\n"
            "Проверьте написание и введите ФИО ещё раз или обратитесь к системотехнику."
        )
        return

    # Сохраняем активы в состоянии (по ID), чтобы потом сверять при сканировании QR
    assets_by_id: Dict[int, Dict] = {int(a["ID"]): a for a in assets}
    await state.update_data(assets=assets_by_id)

    text_lines, kb = build_assets_list_message(assets, include_start_inventory=False)
    await message.answer("\n".join(text_lines), reply_markup=kb.as_markup())


@dp.callback_query(F.data.startswith("asset_"), StateFilter(InvStates.waiting_fio))
async def handle_asset_click(callback: CallbackQuery, state: FSMContext):
    try:
        asset_id = int(callback.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный идентификатор актива.", show_alert=True)
        return
    
    data = await state.get_data()
    assets = data.get("assets") or {}
    asset = assets.get(asset_id)
    
    if not asset:
        await callback.answer("Актив не найден в списке.", show_alert=True)
        return
    
    asset_name = asset.get("sFullName", f"ID {asset_id}")
    serial = asset.get("sSerialNo") or "-"
    is_inventoried = _is_asset_inventoried(asset)
    status_icon = "✅" if is_inventoried else "❌"
    status_text = "проведена" if is_inventoried else "не проведена"
    
    if is_inventoried:
        hint = "Инвентаризация по этому активу не требуется."
    else:
        hint = "Для инвентаризации сделайте фото QR-кода на технике."
    info_text = (
        f"{status_icon} <b>Актив ID {asset_id}</b>\n\n"
        f"Название: {asset_name}\n"
        f"Серийный номер: {serial}\n"
        f"Статус инвентаризации: {status_text}\n\n"
        f"{hint}"
    )
    
    await callback.answer()
    if callback.message:
        chat_id = callback.message.chat.id
        await _safe_delete_message(callback.message)
        # Для актива без инвентаризации добавляем кнопку «Нет наклейки с QR»
        reply_markup = None
        if not is_inventoried:
            kb_no_qr = InlineKeyboardBuilder()
            kb_no_qr.button(text="Нет наклейки с QR", callback_data=f"no_qr_{asset_id}")
            kb_no_qr.adjust(1)
            reply_markup = kb_no_qr.as_markup()
        await bot.send_message(chat_id, info_text, reply_markup=reply_markup)
        # Если актив уже учтён — сразу показываем список. Если нет — список выведется после инвентаризации (QR/фото)
        if is_inventoried:
            assets_list = list(assets.values())
            text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
            await bot.send_message(chat_id, "\n".join(text_lines), reply_markup=kb.as_markup())


@dp.callback_query(F.data == "start_inventory", StateFilter(InvStates.waiting_fio))
async def start_inventory(callback: CallbackQuery, state: FSMContext):
    """Переход в режим инвентаризации."""
    await state.set_state(InvStates.inventory)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Режим инвентаризации включён.\n\n"
            "Пришлите фото QR-кода на технике. Распознается автоматически.",
            reply_markup=kb_inventory(),
        )
    await callback.answer()


REPORT_DESCRIPTION = (
    "Если у вас есть техника, которой нет в списке ваших активов — "
    "пришлите фото техники (очень важно чтобы был виден серийний номер техники и модель).\n\n"
    "Отправьте одно или несколько фото, затем нажмите «Отправить заявку»."
)


@dp.callback_query(F.data == "wrong_qr_retry", StateFilter(InvStates.waiting_fio, InvStates.inventory))
async def wrong_qr_retry(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("Отправьте фото с QR-кодом техники.")


@dp.callback_query(F.data == "wrong_qr_finish", StateFilter(InvStates.waiting_fio, InvStates.inventory))
async def wrong_qr_finish(callback: CallbackQuery, state: FSMContext):
    """После «не тот QR»: вернуться к списку активов."""
    await callback.answer()
    data = await state.get_data()
    assets = data.get("assets") or {}
    if not assets:
        await state.set_state(InvStates.waiting_identifier)
        if callback.message:
            await _safe_delete_message(callback.message)
            await callback.message.answer(
                "Список активов недоступен. Введите ФИО, логин или почту (@asg.ru) для входа или отправьте /start."
            )
        return
    await state.set_state(InvStates.waiting_fio)
    if callback.message:
        await _safe_delete_message(callback.message)
        assets_list = list(assets.values())
        text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
        await callback.message.answer(
            "\n".join(text_lines),
            reply_markup=kb.as_markup(),
        )


# --- Нет наклейки с QR: фото техники и серийного номера → в группу ---

NO_QR_SEND_TO_GROUP = (
    "Нет наклейки с QR — нужно зафиксировать технику по-другому.\n\n"
    "Сделайте фото техники и её серийного номера. "
    "Отправьте одно или несколько фото, затем нажмите «Отправить»."
)


def kb_no_qr_extra():
    """Инлайн-кнопки «Отправить» / «Отмена» для шага «нет наклейки с QR»."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Отправить", callback_data="no_qr_submit")
    kb.button(text="Отмена", callback_data="no_qr_cancel")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(F.data.startswith("no_qr_"), StateFilter(InvStates.waiting_fio, InvStates.inventory))
async def no_qr_sticker_start(callback: CallbackQuery, state: FSMContext):
    """Нажатие «Нет наклейки с QR» — переходим к сбору фото техники и серийного номера."""
    try:
        suffix = callback.data.split("_", 2)[2]
        asset_id = int(suffix)
    except (ValueError, IndexError):
        return  # no_qr_submit / no_qr_cancel обрабатываются другими обработчиками в no_qr_photo
    data = await state.get_data()
    assets = data.get("assets") or {}
    asset = assets.get(asset_id)
    if not asset:
        await callback.answer("Актив не найден в списке.", show_alert=True)
        return
    await state.update_data(
        no_qr_asset_id=asset_id,
        no_qr_asset_name=asset.get("sFullName", f"ID {asset_id}"),
        no_qr_asset_serial=asset.get("sSerialNo") or "-",
        no_qr_photos=[],
    )
    await state.set_state(InvStates.no_qr_photo)
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(NO_QR_SEND_TO_GROUP, reply_markup=kb_no_qr_extra())


@dp.callback_query(F.data == "no_qr_submit", StateFilter(InvStates.no_qr_photo))
async def no_qr_submit(callback: CallbackQuery, state: FSMContext):
    """Отправка «нет наклейки с QR» в группу."""
    data = await state.get_data()
    photos: List[str] = data.get("no_qr_photos") or []
    if not photos:
        await callback.answer("Пожалуйста, добавьте хотя бы одно фото техники и серийного номера.", show_alert=True)
        return
    asset_id = data.get("no_qr_asset_id")
    asset_name = data.get("no_qr_asset_name", "")
    asset_serial = data.get("no_qr_asset_serial", "-")
    fio = data.get("fio", "—")
    username_str = _format_username(callback.from_user)
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    report_text = (
        "<b>Нет наклейки с QR</b>\n\n"
        f"ФИО: {html.escape(fio)}\n"
        f"Username: {html.escape(username_str)}\n"
        f"Дата: {date_str}\n\n"
        f"Актив ID {asset_id}: {html.escape(asset_name)}\n"
        f"Серийный номер: {html.escape(asset_serial)}\n\n"
        "Фото техники и серийного номера:"
    )
    submitter_user_id = callback.from_user.id if callback.from_user else 0
    try:
        sent_text = await bot.send_message(REPORT_GROUP_ID, report_text)
        report_message_to_user[(REPORT_GROUP_ID, sent_text.message_id)] = submitter_user_id
        if len(photos) == 1:
            sent_photo = await bot.send_photo(REPORT_GROUP_ID, photos[0])
            report_message_to_user[(REPORT_GROUP_ID, sent_photo.message_id)] = submitter_user_id
        elif len(photos) > 1:
            media = [InputMediaPhoto(media=fid) for fid in photos]
            sent_media = await bot.send_media_group(REPORT_GROUP_ID, media)
            for m in sent_media:
                report_message_to_user[(REPORT_GROUP_ID, m.message_id)] = submitter_user_id
    except TelegramBadRequest as e:
        err = str(e).lower()
        await callback.answer("Не удалось отправить в группу.", show_alert=True)
        if callback.message and ("chat not found" not in err and "chat_not_found" not in err):
            await callback.message.answer(str(e))
        return
    except Exception as e:
        logging.exception("Ошибка при отправке «нет наклейки с QR» в группу")
        await callback.answer("Не удалось отправить.", show_alert=True)
        if callback.message:
            await callback.message.answer(f"Ошибка: {e}")
        return

    # Загружаем каждое фото в карточку актива A-Tracker (помимо отправки в группу)
    ts_base = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_errors = 0
    for idx, file_id in enumerate(photos):
        try:
            file = await bot.get_file(file_id)
            file_obj = await bot.download_file(file.file_path)
            image_bytes = file_obj.getvalue() if isinstance(file_obj, BytesIO) else file_obj.read()
            suffix = ".jpg"
            if getattr(file, "file_path", None) and str(file.file_path).lower().endswith(".png"):
                suffix = ".png"
            file_name = f"Asset_{asset_id}_{ts_base}_{idx + 1}{suffix}"
            content_type = "image/png" if suffix == ".png" else "image/jpeg"
            await atracker.upload_asset_file(
                asset_id=asset_id,
                file_name=file_name,
                content_bytes=image_bytes,
                content_type=content_type,
            )
        except Exception as e:
            logging.exception("Ошибка при загрузке фото в карточку актива A-Tracker (no_qr)")
            upload_errors += 1

    await state.set_state(InvStates.waiting_fio)
    await state.update_data(no_qr_asset_id=None, no_qr_asset_name=None, no_qr_asset_serial=None, no_qr_photos=None)
    await callback.answer()
    if callback.message:
        await _safe_delete_message(callback.message)
        msg = "Отправлено! После проверки инженерами информация в системе учёта будет обновлена."
        if upload_errors:
            msg += f"\n\nФото отправлены в группу. В карточку актива не удалось загрузить {upload_errors} фото."
        elif len(photos) > 0:
            msg += "\n\nФото также загружены в карточку актива."
        await callback.message.answer(msg)
        data = await state.get_data()
        assets = data.get("assets") or {}
        if assets:
            assets_list = list(assets.values())
            text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
            await callback.message.answer("\n".join(text_lines), reply_markup=kb.as_markup())


@dp.callback_query(F.data == "no_qr_cancel", StateFilter(InvStates.no_qr_photo))
async def no_qr_cancel(callback: CallbackQuery, state: FSMContext):
    """Отмена заявки «нет наклейки с QR» — возврат к списку активов."""
    await state.update_data(no_qr_asset_id=None, no_qr_asset_name=None, no_qr_asset_serial=None, no_qr_photos=None)
    await callback.answer()
    if callback.message:
        await _return_to_asset_list(
            state, callback.message.answer, "Заявка отменена.",
            message_to_delete=callback.message,
        )


@dp.message(F.photo, StateFilter(InvStates.no_qr_photo))
async def no_qr_photo(message: Message, state: FSMContext):
    """Приём фото в заявке «нет наклейки с QR»."""
    data = await state.get_data()
    photos: List[str] = list(data.get("no_qr_photos") or [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(no_qr_photos=photos)
    await message.answer(
        f"Фото добавлено ({len(photos)}). Можно отправить ещё или нажать «Отправить».",
        reply_markup=kb_no_qr_extra(),
    )


# --- Идентифицировать устройство (информационно: кто владелец актива) ---

IDENTIFY_PROMPT = (
    "Пришлите фото QR-кода на технике. Распознается автоматически.\n\n"
    "Бот покажет, за кем числится актив."
)


def kb_identify_cancel():
    """Кнопка «Отмена» для режима идентификации."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Отмена", callback_data="identify_cancel")
    return kb.as_markup()


@dp.callback_query(F.data == "identify_device", StateFilter(InvStates.waiting_fio))
async def start_identify_device(callback: CallbackQuery, state: FSMContext):
    await state.set_state(InvStates.identify_device)
    await callback.answer()
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(IDENTIFY_PROMPT, reply_markup=kb_identify_cancel())


@dp.callback_query(F.data == "identify_cancel", StateFilter(InvStates.identify_device))
async def cancel_identify_device(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await _return_to_asset_list(
            state, callback.message.answer, "Идентификация отменена.",
            message_to_delete=callback.message,
        )


async def _do_identify_asset(message: Message, state: FSMContext, asset_id: int) -> None:
    try:
        info, reason = await atracker.get_asset_info(asset_id)
    except Exception:
        logging.exception("Ошибка при запросе информации об активе")
        await message.answer(
            "Не удалось получить данные об активе. Попробуйте позже."
        )
        await _return_to_asset_list(state, message.answer, "")
        return
    if reason == "service_error":
        await message.answer(
            f"Не удалось получить данные из A-Tracker по активу ID {asset_id}.\n\n"
            "Сервис идентификации не настроен или недоступен. Актив может быть в базе — "
            "обратитесь к системотехнику для настройки."
        )
        await _return_to_asset_list(state, message.answer, "")
        return
    if reason == "not_found" or not info:
        await message.answer(
            f"Актив с ID {asset_id} не найден в базе."
        )
        await _return_to_asset_list(state, message.answer, "")
        return
    name = info.get("sFullName", f"ID {asset_id}")
    owner = info.get("OwnerFio", "—")
    serial = (info.get("sSerialNo") or "").strip() or "—"
    inv_no = inventory_number_from_atracker_dict(info) or "—"
    await message.answer(
        f"<b>Актив ID {asset_id}</b>: {html.escape(name)}\n\n"
        f"Серийный номер: {html.escape(serial)}\n"
        f"Инв. номер: {html.escape(inv_no)}\n\n"
        f"Числится за: <b>{html.escape(owner)}</b>"
    )
    await _return_to_asset_list(state, message.answer, "")


@dp.message(F.text == "Отмена", StateFilter(InvStates.identify_device))
async def identify_device_cancel_text(message: Message, state: FSMContext):
    await _return_to_asset_list(
        state, message.answer, "Идентификация отменена."
    )


@dp.message(F.text, StateFilter(InvStates.identify_device))
async def identify_device_text(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if "ID=" in text:
        asset_id = extract_asset_id(text)
        if asset_id is None:
            await message.answer("Не удалось извлечь ID из ссылки. Проверьте формат (ожидается …/ID=число).")
            return
        await _do_identify_asset(message, state, asset_id)
        return
    await message.answer(
        "Отправьте фото с QR-кодом.",
        reply_markup=kb_identify_cancel(),
    )


@dp.message(F.photo, StateFilter(InvStates.identify_device))
async def identify_device_photo(message: Message, state: FSMContext):
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        file_obj = await bot.download_file(file.file_path)
        image_bytes = file_obj.getvalue() if isinstance(file_obj, BytesIO) else file_obj.read()
    except Exception:
        await message.answer("Не удалось загрузить фото. Попробуйте ещё раз.")
        return
    qr_text = decode_qr_from_bytes(image_bytes)
    if not qr_text:
        await message.answer(
            "На изображении не найден QR-код. Убедитесь, что QR-код чётко виден.",
            reply_markup=kb_identify_cancel(),
        )
        return
    asset_id = extract_asset_id(qr_text)
    if asset_id is None:
        await message.answer("Не удалось распознать ID актива из QR. Убедитесь, что QR-код чётко виден.")
        return
    await _do_identify_asset(message, state, asset_id)


@dp.callback_query(F.data == "finish_session", StateFilter(InvStates.waiting_fio))
async def finish_session(callback: CallbackQuery, state: FSMContext):
    """Кнопка «Завершить» — сброс сессии и сразу приглашение ввести ФИО (без кнопки «Начать заново»)."""
    await state.clear()
    if callback.from_user and callback.from_user.id in user_to_group_thread:
        del user_to_group_thread[callback.from_user.id]
    user_id = callback.from_user.id if callback.from_user else None
    if callback.message:
        await _safe_delete_message(callback.message)
        await callback.message.answer(
            "Спасибо за обращение! 👋\n\n"
            + _identifier_prompt(user_id),
            reply_markup=ReplyKeyboardRemove(),
        )
    await state.set_state(InvStates.waiting_identifier)
    await callback.answer()


@dp.callback_query(F.data == "report_equipment", StateFilter(InvStates.waiting_fio))
async def start_report_equipment(callback: CallbackQuery, state: FSMContext):
    """Переход в режим заявки о технике."""
    await state.set_state(InvStates.reporting_equipment)
    await state.update_data(report_photos=[])
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(REPORT_DESCRIPTION, reply_markup=kb_reporting())
    await callback.answer()


@dp.message(F.photo, StateFilter(InvStates.reporting_equipment))
async def handle_report_photo(message: Message, state: FSMContext):
    """Приём фото в заявке о технике."""
    data = await state.get_data()
    photos: List[str] = list(data.get("report_photos") or [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(report_photos=photos)
    count = len(photos)
    await message.answer(
        f"Фото добавлено ({count}). Можно отправить ещё или нажать «Отправить заявку»."
    )



async def _send_report_to_group(message: Message, state: FSMContext, extra_info: str) -> None:
    data = await state.get_data()
    photos: List[str] = data.get("report_photos") or []
    fio = data.get("fio", "—")
    username_str = _format_username(message.from_user)
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    extra_display = (extra_info or "").strip() or "—"

    report_text = (
        "Заявка о технике (не в списке)\n\n"
        f"ФИО: {html.escape(fio)}\n"
        f"Username: {html.escape(username_str)}\n"
        f"Дата: {date_str}\n"
        f"Доп. информация: {html.escape(extra_display)}"
    )

    submitter_user_id = message.from_user.id if message.from_user else 0
    ok, err_msg = await _send_to_report_group(report_text, photos, submitter_user_id, log_label="заявка о технике")
    if not ok:
        await message.answer(err_msg or "Не удалось отправить заявку. Попробуйте позже.")
        return

    await _return_user_to_asset_list_after_report(
        state,
        submitter_user_id,
        message.answer,
        "Заявка отправлена. Спасибо!\n\n"
        "Ожидайте ответа из группы. Пока ждёте — не нажимайте «Завершить», иначе ответы не будут приходить сюда.",
        remove_reply_keyboard=True,
    )


@dp.message(F.text == "Отправить заявку", StateFilter(InvStates.reporting_equipment))
async def btn_submit_report(message: Message, state: FSMContext):
    """Переход к шагу «доп. информация» перед отправкой заявки."""
    data = await state.get_data()
    photos: List[str] = data.get("report_photos") or []
    if not photos:
        await message.answer("Пожалуйста, добавьте хотя бы одно фото техники и нажмите «Отправить заявку».")
        return

    await state.set_state(InvStates.reporting_comment)
    await message.answer(
        "При необходимости укажите телефон или комментарий. Или нажмите «Пропустить».",
        reply_markup=kb_reporting_comment(),
    )


@dp.message(F.text == "Пропустить", StateFilter(InvStates.reporting_comment))
async def btn_skip_comment(message: Message, state: FSMContext):
    await _send_report_to_group(message, state, "")


@dp.message(F.text == "Отмена", StateFilter(InvStates.reporting_comment))
async def btn_cancel_report_comment(message: Message, state: FSMContext):
    await _return_to_asset_list(
        state, message.answer, "Заявка отменена.", remove_reply_keyboard=True
    )


@dp.message(F.text, StateFilter(InvStates.reporting_comment))
async def handle_report_comment(message: Message, state: FSMContext):
    extra_info = (message.text or "").strip()
    await _send_report_to_group(message, state, extra_info)


@dp.message(F.text == "Отмена", StateFilter(InvStates.reporting_equipment))
async def btn_cancel_report(message: Message, state: FSMContext):
    await _return_to_asset_list(
        state, message.answer, "Заявка отменена.", remove_reply_keyboard=True
    )

DISCREPANCY_REASONS = [
    ("Не моя техника", "not_mine", "Не моя техника"),
    ("Числится на другом сотруднике", "other_emp", "Числится на другом сотруднике"),
    ("Утеряна", "lost", "Утеряна"),
    ("Другой (напишу в комментарии)", "other", None),  # None = запросить текст
]


def kb_discrepancy_reason():
    kb = InlineKeyboardBuilder()
    for label, data, _ in DISCREPANCY_REASONS:
        kb.button(text=label, callback_data=f"disc_r_{data}")
    kb.button(text="Отмена", callback_data="discrepancy_cancel")
    kb.adjust(1)
    return kb.as_markup()


def kb_discrepancy_extra():
    kb = InlineKeyboardBuilder()
    kb.button(text="Отправить", callback_data="disc_submit")
    kb.button(text="Отмена", callback_data="disc_cancel_extra")
    kb.adjust(1)
    return kb.as_markup()


@dp.callback_query(
    F.data == "discrepancy_cancel",
    StateFilter(
        InvStates.discrepancy_asset,
        InvStates.discrepancy_reason,
        InvStates.discrepancy_reason_other,
    ),
)
async def btn_cancel_discrepancy(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await _return_to_asset_list(
            state, callback.message.answer, "Сообщение о несоответствии отменено.",
            message_to_delete=callback.message,
        )


@dp.callback_query(F.data == "report_discrepancy", StateFilter(InvStates.waiting_fio))
async def start_report_discrepancy(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    assets = data.get("assets") or {}
    if not assets:
        await callback.answer("Список активов недоступен. Введите ФИО заново.", show_alert=True)
        return
    await state.set_state(InvStates.discrepancy_asset)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Выберите актив, по которому хотите сообщить о несоответствии:",
            reply_markup=build_discrepancy_asset_keyboard(list(assets.values())),
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("discrepancy_asset_"), StateFilter(InvStates.discrepancy_asset))
async def discrepancy_choose_asset(callback: CallbackQuery, state: FSMContext):
    try:
        asset_id = int(callback.data.split("_", 2)[2])
    except (ValueError, IndexError):
        await callback.answer("Ошибка: неверный идентификатор актива.", show_alert=True)
        return
    data = await state.get_data()
    assets = data.get("assets") or {}
    asset = assets.get(asset_id)
    if not asset:
        await callback.answer("Актив не найден в списке.", show_alert=True)
        return
    await state.update_data(
        discrepancy_asset_id=asset_id,
        discrepancy_asset_name=asset.get("sFullName", f"ID {asset_id}"),
        discrepancy_asset_serial=asset.get("sSerialNo") or "-",
    )
    await state.set_state(InvStates.discrepancy_reason)
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Укажите причину несоответствия:",
            reply_markup=kb_discrepancy_reason(),
        )
    await callback.answer()


@dp.callback_query(F.data.startswith("disc_r_"), StateFilter(InvStates.discrepancy_reason))
async def discrepancy_choose_reason(callback: CallbackQuery, state: FSMContext):
    code = callback.data.replace("disc_r_", "", 1)
    reason_text = None
    for _label, _data, text in DISCREPANCY_REASONS:
        if _data == code:
            reason_text = text
            break
    if reason_text is None:
        # "other" — запросить текст
        await state.set_state(InvStates.discrepancy_reason_other)
        if callback.message:
            await callback.message.edit_reply_markup(reply_markup=None)
            await callback.message.answer("Опишите причину несоответствия своими словами:")
        await callback.answer()
        return
    await state.update_data(discrepancy_reason=reason_text)
    await state.set_state(InvStates.discrepancy_extra)
    await state.update_data(discrepancy_comment="", discrepancy_photos=[])
    if callback.message:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            "Напишите комментарий и отправьте его сообщением. При необходимости приложите фото. "
            "Затем нажмите кнопку «Отправить» ниже.",
            reply_markup=kb_discrepancy_extra(),
        )
    await callback.answer()


@dp.message(F.text == "Отмена", StateFilter(InvStates.discrepancy_reason_other))
async def btn_cancel_discrepancy_reason_other(message: Message, state: FSMContext):
    await _return_to_asset_list(
        state, message.answer, "Сообщение о несоответствии отменено."
    )


@dp.message(F.text, StateFilter(InvStates.discrepancy_reason_other))
async def discrepancy_reason_other_text(message: Message, state: FSMContext):
    """Текстовая причина несоответствия («другой»)."""
    reason = (message.text or "").strip()
    if not reason:
        await message.answer("Пожалуйста, введите причину текстом.")
        return
    await state.update_data(discrepancy_reason=reason)
    await state.set_state(InvStates.discrepancy_extra)
    await state.update_data(discrepancy_comment="", discrepancy_photos=[])
    await message.answer(
        "При необходимости напишите комментарий и отправьте его сообщением. Можно приложить фото.\n\n"
        "Когда будете готовы — нажмите кнопку «Отправить» ниже.",
        reply_markup=kb_discrepancy_extra(),
    )


@dp.callback_query(F.data == "disc_submit", StateFilter(InvStates.discrepancy_extra))
async def btn_submit_discrepancy(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await _safe_delete_message(callback.message)
        await _send_discrepancy_to_group(
            state, callback.from_user, callback.message.answer
        )


@dp.callback_query(F.data == "disc_cancel_extra", StateFilter(InvStates.discrepancy_extra))
async def btn_cancel_discrepancy_extra(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    if callback.message:
        await _return_to_asset_list(
            state, callback.message.answer, "Сообщение о несоответствии отменено.",
            message_to_delete=callback.message,
        )


@dp.message(F.photo, StateFilter(InvStates.discrepancy_extra))
async def discrepancy_extra_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    photos: List[str] = list(data.get("discrepancy_photos") or [])
    photos.append(message.photo[-1].file_id)
    await state.update_data(discrepancy_photos=photos)
    await message.answer(
        f"Фото добавлено ({len(photos)}). Можно отправить ещё или нажать «Отправить».",
        reply_markup=kb_discrepancy_extra(),
    )


@dp.message(F.text, StateFilter(InvStates.discrepancy_extra))
async def discrepancy_extra_text(message: Message, state: FSMContext):
    comment = (message.text or "").strip()
    await state.update_data(discrepancy_comment=comment)
    await message.answer(
        "Комментарий сохранён. Можно приложить фото или нажать «Отправить».",
        reply_markup=kb_discrepancy_extra(),
    )



async def _send_discrepancy_to_group(
    state: FSMContext,
    from_user,
    answer_fn,  # async fn(text, reply_markup=...) — отправить сообщение пользователю
) -> None:
    data = await state.get_data()
    asset_id = data.get("discrepancy_asset_id")
    asset_name = data.get("discrepancy_asset_name", "")
    asset_serial = data.get("discrepancy_asset_serial", "-")
    reason = data.get("discrepancy_reason", "—")
    comment = (data.get("discrepancy_comment") or "").strip() or "—"
    photos: List[str] = data.get("discrepancy_photos") or []
    fio = data.get("fio", "—")
    username_str = _format_username(from_user)
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    report_text = (
        "<b>НЕСООТВЕСТВИЕ</b>\n\n"
        f"ФИО: {html.escape(fio)}\n"
        f"Username: {html.escape(username_str)}\n"
        f"Дата: {date_str}\n\n"
        f"Актив ID {asset_id}: {html.escape(asset_name)}\n"
        f"Серийный номер: {html.escape(asset_serial)}\n"
        f"Причина: {html.escape(reason)}\n"
        f"Комментарий: {html.escape(comment)}"
    )

    submitter_user_id = from_user.id if from_user else 0
    ok, err_msg = await _send_to_report_group(report_text, photos, submitter_user_id, log_label="несоответствие")
    if not ok:
        await answer_fn(err_msg or "Не удалось отправить сообщение. Попробуйте позже или обратитесь к системотехнику.")
        return

    await _return_user_to_asset_list_after_report(
        state, submitter_user_id, answer_fn,
        "Сообщение о несоответствии отправлено. Спасибо за информацию!",
    )


@dp.message(F.reply_to_message)
async def handle_group_reply_to_report(message: Message):
    """Ответ в группе на заявку о технике — переслать текст заявителю в личку."""
    # Проверяем, что это сообщение из группы заявок
    if message.chat.id != REPORT_GROUP_ID:
        return
    
    me = await bot.get_me()
    reply_to = message.reply_to_message
    if not reply_to:
        return
    
    logging.info(
        f"Получен ответ {message.chat.id}: reply_to_msg_id={reply_to.message_id}, "
        f"from_user={reply_to.from_user.id if reply_to.from_user else None}, "
        f"sender_chat={reply_to.sender_chat.id if reply_to.sender_chat else None}, "
        f"me.id={me.id}, text={message.text[:50] if message.text else None}"
    )
    
    # Проверяем, что ответ на сообщение бота (может быть от from_user или sender_chat)
    is_from_bot = False
    if reply_to.from_user and reply_to.from_user.id == me.id:
        is_from_bot = True
    elif reply_to.sender_chat and reply_to.sender_chat.id == me.id:
        is_from_bot = True
    
    if not is_from_bot:
        logging.info(f"Ответ не на сообщение бота (пропускаем)")
        return
    
    logging.info(f"Обрабатываю ответ из группы на сообщение {reply_to.message_id}")
    
    key = (message.chat.id, reply_to.message_id)
    user_id = report_message_to_user.get(key)
    if not user_id:
        logging.warning(
            f"Не найдена связь для сообщения {reply_to.message_id} в чате {message.chat.id}. "
            f"Доступные ключи (первые 10): {list(report_message_to_user.keys())[:10]}"
        )
        return
    
    reply_text = (message.text or message.caption or "").strip()
    if not reply_text:
        reply_text = "Ответ без текста (возможно, отправлено медиа)."
    
    logging.info(f"Пересылаю ответ пользователю {user_id}: {reply_text[:50]}")
    try:
        await bot.send_message(
            user_id,
            f"По вашей заявке о технике пришел ответ:\n\n{html.escape(reply_text)}",
        )
        # Сохраняем связь для обратной переписки: заявитель может ответить на это сообщение
        user_to_group_thread[user_id] = (message.chat.id, reply_to.message_id)
        await message.reply("Ответ отправлен заявителю.")
        logging.info(f"Ответ успешно отправлен пользователю {user_id}")
    except Exception as e:
        logging.exception("Ошибка при отправке ответа заявителю")
        err = str(e).lower()
        if "blocked" in err or "user is deactivated" in err or "chat not found" in err:
            hint = (
                "Не удалось доставить ответ заявителю (возможно, он завершил сессию или заблокировал бота). "
                "Свяжитесь с ним напрямую — username и ФИО указаны в заявке выше."
            )
        else:
            hint = (
                "Не удалось отправить заявителю. "
                "Попробуйте связаться с ним напрямую — username и ФИО указаны в заявке выше."
            )
        await message.reply(hint)


async def finish_inventory(message: Message, state: FSMContext) -> None:
    """«Завершить инвентаризацию» — возврат на главный экран (список активов)."""
    await state.set_state(InvStates.waiting_fio)
    await message.answer(
        "Инвентаризация завершена. Спасибо за работу!",
        reply_markup=ReplyKeyboardRemove(),
    )
    data = await state.get_data()
    assets = data.get("assets") or {}
    if assets:
        assets_list = list(assets.values())
        text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
        await message.answer("\n".join(text_lines), reply_markup=kb.as_markup())
    else:
        await state.set_state(InvStates.waiting_identifier)
        user_id = message.from_user.id if message.from_user else None
        await message.answer(_identifier_prompt(user_id))


@dp.message(F.text == "/done", StateFilter(InvStates.inventory))
async def cmd_done(message: Message, state: FSMContext):
    await finish_inventory(message, state)


@dp.message(F.text == "Завершить инвентаризацию", StateFilter(InvStates.inventory))
async def btn_done(message: Message, state: FSMContext):
    await finish_inventory(message, state)


def extract_asset_id(text: str) -> Optional[int]:
    marker = "ID="
    idx = text.find(marker)
    if idx == -1:
        return None
    part = text[idx + len(marker):]
    part = part.split("&", 1)[0]
    try:
        return int(part)
    except ValueError:
        return None


def decode_qr_from_bytes(image_bytes: bytes) -> Optional[str]:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    detector = cv2.QRCodeDetector()

    # Пробуем: оригинал, ч/б, уменьшенное (иногда лучше распознаётся)
    variants = [img]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    variants.append(gray)
    h, w = img.shape[:2]
    if max(h, w) > 1200:
        scale = 1200 / max(h, w)
        small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
        variants.append(small)
        variants.append(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))

    for im in variants:
        data, _, _ = detector.detectAndDecode(im)
        if data and data.strip():
            return data.strip()
    return None


async def _load_photo_and_decode_qr(message: Message) -> Optional[Tuple[bytes, Optional[str], str]]:
    """
    Скачивает фото из сообщения, распознаёт QR.
    Возвращает (image_bytes, original_filename, qr_text) или None (при ошибке уже отправлено сообщение пользователю).
    """
    original_filename: Optional[str] = None
    try:
        photo = message.photo[-1]
        file = await bot.get_file(photo.file_id)
        if file.file_path:
            original_filename = Path(file.file_path).name
        file_obj = await bot.download_file(file.file_path)
        if isinstance(file_obj, BytesIO):
            image_bytes = file_obj.getvalue()
        else:
            image_bytes = file_obj.read()
    except Exception:
        logging.exception("Ошибка при загрузке фото")
        await message.answer("Не удалось загрузить фото. Попробуйте отправить другое изображение.")
        return None

    qr_text = decode_qr_from_bytes(image_bytes)
    if not qr_text:
        await message.answer("На изображении не найден QR-код. Убедитесь, что QR-код чётко виден.")
        return None

    return (image_bytes, original_filename, qr_text)


async def process_inventory_qr(
    message: Message,
    state: FSMContext,
    qr_text: str,
    image_bytes: Optional[bytes] = None,
    original_filename: Optional[str] = None,
) -> None:
    data = await state.get_data()
    fio = data.get("fio")
    assets = data.get("assets") or {}

    asset_id = extract_asset_id(qr_text)

    if asset_id is None:
        await message.answer(
            "Не удалось распознать QR-код.\n\n"
            "Убедитесь, что отсканирован QR-код с техники. "
            "Попробуйте отправить другое фото."
        )
        return

    asset = assets.get(asset_id)
    if not asset:
        kb = InlineKeyboardBuilder()
        kb.button(text="Прислать фото ещё раз", callback_data="wrong_qr_retry")
        kb.button(text="Завершить по активу", callback_data="wrong_qr_finish")
        kb.adjust(1)
        await message.answer(
            f"Это не тот QR-код: актив с ID {asset_id} не закреплён за вами.\n\n"
            "Пришлите другое фото с QR-кодом или завершите инвентаризацию по этому активу.",
            reply_markup=kb.as_markup(),
        )
        return

    if _is_asset_inventoried(asset):
        await message.answer(
            f"✅ Актив ID {asset_id} ({asset.get('sFullName', '')}) уже учтён.\n\n"
            "Инвентаризация по этому активу не требуется."
        )
        return

    try:
        res = await atracker.mark_inventory(
            asset_id=asset_id,
            fio=fio,
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
        )
    except Exception as e:
        logging.exception("Ошибка при отметке инвентаризации")
        await message.answer("Не удалось отметить инвентаризацию. Попробуйте позже.")
        return

    # Если есть фото, пытаемся прикрепить его к карточке актива (уникальное имя — каждое загруженное сохраняется отдельно)
    if image_bytes:
        try:
            suffix = ""
            if original_filename:
                suffix = Path(original_filename).suffix
            if not suffix:
                suffix = ".jpg"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            file_name = f"Asset_{asset_id}_{ts}{suffix}"
            content_type = "image/jpeg"
            if suffix.lower() == ".png":
                content_type = "image/png"

            await atracker.upload_asset_file(
                asset_id=asset_id,
                file_name=file_name,
                content_bytes=image_bytes,
                content_type=content_type,
            )
        except Exception as e:
            logging.exception("Ошибка при загрузке файла в A-Tracker")
            await message.answer("Инвентаризация отмечена. Не удалось прикрепить фото к карточке — попробуйте позже.")
            return

    # Обновляем статус актива в сохранённых данных
    if asset:
        asset["IsInventoried"] = True
        asset["InventoryStatus"] = "Проведена"
        assets[asset_id] = asset
        await state.update_data(assets=assets)
    
    sys_msg = (res.get("message") or "").strip()
    msg = f"✅ Инвентаризация по активу ID {asset_id} ({asset['sFullName']}) отмечена."
    if sys_msg:
        msg += f"\n\n{sys_msg}"
    await message.answer(msg)
    
    # Показываем список активов с обновлёнными статусами
    assets_list = list(assets.values())
    text_lines, kb = build_assets_list_message(assets_list, include_start_inventory=False)
    await message.answer("\n".join(text_lines), reply_markup=kb.as_markup())


@dp.message(F.photo, StateFilter(InvStates.waiting_fio))
async def handle_qr_photo_in_waiting(message: Message, state: FSMContext):
    result = await _load_photo_and_decode_qr(message)
    if result is None:
        return
    image_bytes, original_filename, qr_text = result
    await process_inventory_qr(
        message, state, qr_text,
        image_bytes=image_bytes,
        original_filename=original_filename,
    )


@dp.message(F.text, StateFilter(InvStates.inventory))
async def handle_qr_text(message: Message, state: FSMContext):
    qr_text = (message.text or "").strip()
    await process_inventory_qr(message, state, qr_text)


@dp.message(F.photo, StateFilter(InvStates.inventory))
async def handle_qr_photo(message: Message, state: FSMContext):
    result = await _load_photo_and_decode_qr(message)
    if result is None:
        return
    image_bytes, original_filename, qr_text = result
    await process_inventory_qr(
        message, state, qr_text,
        image_bytes=image_bytes,
        original_filename=original_filename,
    )


async def main() -> None:
    """Точка входа: запускаем long polling."""
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())