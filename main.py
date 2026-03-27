import asyncio
import logging
import os
import sys
from datetime import datetime, time, timedelta

if getattr(sys, "frozen", False):
    # Модули (bot, aiohttp...) — только в _MEIPASS. Папку exe в path НЕ ставить первой.
    _meipass = getattr(sys, "_MEIPASS", "")
    if _meipass and _meipass not in sys.path:
        sys.path.insert(0, _meipass)
    _exe_dir = os.path.dirname(sys.executable)
    if _exe_dir not in sys.path:
        sys.path.append(_exe_dir)
    os.chdir(_exe_dir)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def _sleep_until(target: time) -> None:
    """Ждать до ближайшего target (по местному времени)."""
    now = datetime.now().replace(tzinfo=None)
    next_run = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    delta = (next_run - now).total_seconds()
    logger.info("Следующий запуск в %s", next_run.strftime("%H:%M %d.%m.%Y"))
    await asyncio.sleep(delta)


async def _sync_loop() -> None:
    """Каждый день в 01:00 — сверка AD → A-Tracker."""
    from sync_ad_atracker import run_sync
    while True:
        await _sleep_until(time(1, 0))
        try:
            await run_sync()
        except Exception as e:
            logger.exception("Ошибка сверки AD → A-Tracker: %s", e)


async def _registry_loop() -> None:
    """Каждый день в 07:00 — проверка реестра увольнений/перемещений."""
    import run_registry_check
    while True:
        await _sleep_until(time(7, 0))
        try:
            await run_registry_check.main()
        except Exception as e:
            logger.exception("Ошибка проверки реестра: %s", e)


async def _run_bot_with_scheduler() -> None:
    """Бот + фоновые задачи 01:00 и 07:00."""
    asyncio.create_task(_sync_loop())
    asyncio.create_task(_registry_loop())
    await _bot_dp.start_polling(_bot_instance)


# Импорт бота один раз при старте (в exe отложенный import внутри async падает)
_bot_dp = None
_bot_instance = None


def _ensure_bot_imported() -> None:
    global _bot_dp, _bot_instance
    if _bot_dp is None:
        import bot as _bot_mod
        _bot_dp = _bot_mod.dp
        _bot_instance = _bot_mod.bot


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1].strip() == "--registry":
        import run_registry_check
        path = sys.argv[2] if len(sys.argv) > 2 else None
        if path is not None:
            sys.argv = [sys.argv[0], path]
        asyncio.run(run_registry_check.main())
        return

    _ensure_bot_imported()
    try:
        asyncio.run(_run_bot_with_scheduler())
    except Exception as e:
        logger.exception("%s", e)
        err_path = os.path.join(os.getcwd(), "tg_scan_error.log")
        try:
            with open(err_path, "a", encoding="utf-8") as f:
                from traceback import format_exc
                f.write(format_exc() + "\n")
            logger.info("Подробности записаны в %s", err_path)
        except Exception:
            pass
        input("Нажмите Enter для выхода...")


if __name__ == "__main__":
    main()
