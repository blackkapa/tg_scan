import logging
import random
import smtplib
import string
import time
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_codes: Dict[str, Tuple[str, str, float]] = {}
CODE_TTL_SEC = 600
CODE_LEN = 6


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _norm_login(login: Optional[str]) -> str:
    s = (login or "").strip()
    if "\\" in s:
        s = s.split("\\", 1)[-1]
    return s.lower()


def find_employee_by_input(
    employees: List[Dict[str, Any]],
    user_input: str,
    allowed_domain: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    if not user_input or not user_input.strip():
        return (None, None, "Введите ФИО, логин или почту.")

    raw = user_input.strip()
    lower = raw.lower()
    allowed = allowed_domain.lower().strip()
    if not allowed.startswith("@"):
        allowed = "@" + allowed

    # Похоже на почту
    if "@" in raw:
        if not raw.lower().endswith(allowed):
            return (None, None, f"Разрешена только корпоративная почта {allowed}. Указан другой домен.")
        email = raw.strip()
        for emp in employees:
            if not isinstance(emp, dict):
                continue
            em = (emp.get("sEmail") or emp.get("semail") or "").strip().lower()
            if em == email.lower():
                fio = (emp.get("sFullName") or emp.get("sfullname") or "").strip()
                return (fio or "—", email, None)
        return (None, None, "Сотрудник с такой почтой не найден в системе учёта.")

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
                return (None, None, "У сотрудника не указана почта в системе. Обратитесь к системотехнику.")
            if not email.lower().endswith(allowed):
                return (None, None, f"У сотрудника указана почта не с доменом {allowed}. Вход только через asg.")
            return (fio or "—", email, None)
    return (None, None, "Сотрудник не найден. Проверьте ФИО или логин и попробуйте снова.")


def create_code(fio: str, email: str) -> str:
    code = "".join(random.choices(string.digits, k=CODE_LEN))
    _codes[code] = (fio, email, time.time() + CODE_TTL_SEC)
    return code


def check_code(code: str) -> Optional[Tuple[str, str]]:
    code = (code or "").strip()
    if not code or code not in _codes:
        return None
    fio, email, expires = _codes[code]
    if time.time() > expires:
        del _codes[code]
        return None
    del _codes[code]
    return (fio, email)


def send_code_email(to_email: str, code: str) -> Tuple[bool, str]:
    import config
    host = getattr(config, "SMTP_HOST", "") or ""
    port = int(getattr(config, "SMTP_PORT", 0) or 465)
    use_ssl = getattr(config, "SMTP_USE_SSL", True)
    user = getattr(config, "SMTP_USER", "") or ""
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    from_addr = getattr(config, "SMTP_FROM", "")
    if not host:
        return (False, "Не настроена отправка почты (SMTP_HOST).")
    try:
        msg = MIMEText(f"Код для входа в бота: {code}\n\nДействует 10 минут.", "plain", "utf-8")
        msg["Subject"] = "Код для входа"
        msg["From"] = from_addr
        msg["To"] = to_email
        if use_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20) as s:
                if user and password:
                    s.login(user, password)
                s.sendmail(from_addr, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.starttls()
                if user and password:
                    s.login(user, password)
                s.sendmail(from_addr, [to_email], msg.as_string())
        return (True, "")
    except smtplib.SMTPAuthenticationError as e:
        logger.exception("Ошибка аутентификации SMTP при отправке на %s: %s", to_email, e)
        return (False, "Ошибка входа на почтовый сервер. Проверьте логин/пароль.")
    except Exception as e:
        logger.exception("Ошибка отправки письма на %s: %s", to_email, e)
        return (False, "Не удалось отправить письмо. Попробуйте позже.")
