import logging
import re
import secrets
import smtplib
import string
import threading
import time
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# _codes: Dict[code, {"fio": str, "email": str, "expires": float, "attempts": int}]
_codes: Dict[str, Dict[str, Any]] = {}
_codes_lock = threading.Lock()

CODE_TTL_SEC = 600
CODE_LEN = 6
MAX_CODE_ATTEMPTS = 5


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _norm_login(login: Optional[str]) -> str:
    value = (login or "").strip()
    if "\\" in value:
        value = value.split("\\", 1)[-1]
    return value.lower()


def _match_domain(email: str, allowed_domain: str) -> bool:
    """Строгая проверка соответствия домена почты корпоративному домену."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[-1].strip().lower()
    allowed = allowed_domain.strip().lstrip("@").lower()
    if not allowed:
        return True
    return domain == allowed or domain.endswith("." + allowed)


def find_employee_by_input(
    employees: List[Dict[str, Any]],
    user_input: str,
    allowed_domain: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Ищем сотрудника по ФИО, логину или почте с проверкой домена."""
    if not user_input or not user_input.strip():
        return (None, None, "Введите ФИО, логин или почту.")

    raw = user_input.strip()
    allowed_label = "@" + allowed_domain.strip().lstrip("@")

    # Ввели почту
    if "@" in raw:
        if not _match_domain(raw, allowed_domain):
            return (None, None, f"Разрешена только корпоративная почта {allowed_label}. Указан другой домен.")
        email = raw.strip()
        for emp in employees:
            if not isinstance(emp, dict):
                continue
            em = (emp.get("sEmail") or emp.get("semail") or "").strip().lower()
            if em == email.lower():
                fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
                return (fio or "—", email, None)
        return (None, None, "Сотрудник с такой почтой не найден в системе учёта.")

    # ФИО или логин
    norm_fio = _norm(raw)
    norm_login_input = _norm_login(raw)
    for emp in employees:
        if not isinstance(emp, dict):
            continue
        fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
        login = (emp.get("sLoginName") or emp.get("sloginname") or "").strip()
        email = (emp.get("sEmail") or emp.get("semail") or "").strip()
        if _norm(fio) == norm_fio or _norm_login(login) == norm_login_input:
            if not email:
                return (None, None, "У сотрудника не указана почта в системе. Обратитесь к системному администратору.")
            if not _match_domain(email, allowed_domain):
                return (None, None, f"У сотрудника указана почта не с доменом {allowed_label}. Вход только через почту {allowed_label}.")
            return (fio or "—", email, None)
    return (None, None, "Сотрудник не найден. Проверьте ФИО или логин и попробуйте снова.")


def employee_id_by_email(employees: List[Dict[str, Any]], email: str) -> Optional[int]:
    """ID сотрудника itamEmplDept по корпоративной почте (для перемещения в A-Tracker)."""
    if not email:
        return None
    want = email.strip().lower()
    for emp in employees:
        if not isinstance(emp, dict):
            continue
        em = (emp.get("sEmail") or emp.get("semail") or "").strip().lower()
        if em != want:
            continue
        eid = emp.get("ID")
        if eid is None:
            continue
        try:
            return int(eid)
        except (TypeError, ValueError):
            continue
    return None


def create_code(fio: str, email: str) -> str:
    """Создаёт криптостойкий одноразовый код и запоминает его на ограниченное время."""
    now = time.time()
    code = "".join(secrets.choice(string.digits) for _ in range(CODE_LEN))
    with _codes_lock:
        # Очистка устаревших кодов
        expired_keys = [k for k, v in _codes.items() if now > v.get("expires", 0)]
        for k in expired_keys:
            del _codes[k]

        _codes[code] = {
            "fio": fio,
            "email": email,
            "expires": now + CODE_TTL_SEC,
            "attempts": 0,
        }
    return code


def check_code(code: str, expected_email: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Проверяет код, учитывая лимит попыток и соответствие email в сессии."""
    code = (code or "").strip()
    now = time.time()
    with _codes_lock:
        if not code or code not in _codes:
            return None
        item = _codes[code]
        if now > item.get("expires", 0):
            del _codes[code]
            return None

        item["attempts"] = item.get("attempts", 0) + 1
        if item["attempts"] > MAX_CODE_ATTEMPTS:
            del _codes[code]
            return None

        fio = item.get("fio") or ""
        email = item.get("email") or ""

        if expected_email and email.strip().lower() != expected_email.strip().lower():
            # Если код не принадлежит этому email, не выдаем сессию
            return None

        del _codes[code]
        return (fio, email)


def send_code_email(to_email: str, code: str) -> Tuple[bool, str]:
    """Отправляет письмо с кодом на корпоративную почту."""
    import config

    host = getattr(config, "SMTP_HOST", "") or ""
    port = int(getattr(config, "SMTP_PORT", 0) or 465)
    use_ssl = getattr(config, "SMTP_USE_SSL", True)
    user = getattr(config, "SMTP_USER", "") or ""
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    from_addr = getattr(config, "SMTP_FROM", "")
    if not host:
        return (False, "Не настроена отправка почты (SMTP_HOST).")

    # Санитизация заголовков от CRLF
    clean_from = re.sub(r"[\r\n]+", "", str(from_addr or "")).strip()
    clean_to = re.sub(r"[\r\n]+", "", str(to_email or "")).strip()

    try:
        msg = MIMEText(f"Код для входа в сервис инвентаризации: {code}\n\nКод действует 10 минут.", "plain", "utf-8")
        msg["Subject"] = "Код для входа"
        msg["From"] = clean_from
        msg["To"] = clean_to
        if use_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(clean_from, [clean_to], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=20) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(clean_from, [clean_to], msg.as_string())
        return (True, "")
    except smtplib.SMTPAuthenticationError as ex:
        logger.exception("Ошибка аутентификации SMTP при отправке на %s: %s", to_email, ex)
        return (False, "Не удалось войти на почтовый сервер. Проверьте учётные данные на сервере.")
    except Exception as ex:
        logger.exception("Ошибка отправки письма на %s: %s", to_email, ex)
        return (False, "Не удалось отправить письмо. Попробуйте позже.")


