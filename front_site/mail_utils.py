"""Отправка писем с вложениями (SMTP из config)."""
from __future__ import annotations

import logging
import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Tuple

import config

logger = logging.getLogger(__name__)


def send_plain_text_email(
    to_addrs: list[str],
    subject: str,
    body: str,
) -> Tuple[bool, str]:
    """Текстовое письмо без вложений (тот же SMTP, что и для кодов входа)."""
    to_addrs = [a.strip() for a in to_addrs if (a or "").strip()]
    if not to_addrs:
        return False, "Не указаны адреса получателей."

    host = getattr(config, "SMTP_HOST", "") or ""
    port = int(getattr(config, "SMTP_PORT", 0) or 465)
    use_ssl = getattr(config, "SMTP_USE_SSL", True)
    user = getattr(config, "SMTP_USER", "") or ""
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    from_addr = getattr(config, "SMTP_FROM", "")
    if not host:
        return False, "Не настроена отправка почты (SMTP_HOST)."

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)

    try:
        if use_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        return True, ""
    except smtplib.SMTPException as ex:
        logger.exception("SMTP error (plain): %s", ex)
        return False, f"Ошибка отправки почты: {ex}"
    except OSError as ex:
        logger.exception("Network error (plain): %s", ex)
        return False, f"Ошибка сети: {ex}"


def send_email_with_attachment(
    to_addrs: list[str],
    subject: str,
    body: str,
    attachment_path: Path,
    attachment_filename: str,
) -> Tuple[bool, str]:
    """
    Отправка одного вложения. to_addrs — непустой список адресов.
    """
    to_addrs = [a.strip() for a in to_addrs if (a or "").strip()]
    if not to_addrs:
        return False, "Не указаны адреса получателей."

    host = getattr(config, "SMTP_HOST", "") or ""
    port = int(getattr(config, "SMTP_PORT", 0) or 465)
    use_ssl = getattr(config, "SMTP_USE_SSL", True)
    user = getattr(config, "SMTP_USER", "") or ""
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    from_addr = getattr(config, "SMTP_FROM", "")
    if not host:
        return False, "Не настроена отправка почты (SMTP_HOST)."

    if not attachment_path.is_file():
        return False, "Файл вложения не найден."

    try:
        with attachment_path.open("rb") as f:
            raw = f.read()
    except OSError as ex:
        return False, f"Не удалось прочитать файл: {ex}"

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("application", "octet-stream")
    part.set_payload(raw)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=attachment_filename)
    msg.attach(part)

    try:
        if use_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        return True, ""
    except smtplib.SMTPException as ex:
        logger.exception("SMTP error: %s", ex)
        return False, f"Ошибка отправки почты: {ex}"
    except OSError as ex:
        logger.exception("Ошибка сети при отправке почты: %s", ex)
        return False, f"Ошибка сети: {ex}"


def send_email_with_attachments(
    to_addrs: list[str],
    subject: str,
    body: str,
    attachments: list[tuple[Path, str]],
) -> Tuple[bool, str]:
    """Отправка письма с несколькими вложениями."""
    to_addrs = [a.strip() for a in to_addrs if (a or "").strip()]
    if not to_addrs:
        return False, "Не указаны адреса получателей."
    if not attachments:
        return False, "Не указаны вложения."

    host = getattr(config, "SMTP_HOST", "") or ""
    port = int(getattr(config, "SMTP_PORT", 0) or 465)
    use_ssl = getattr(config, "SMTP_USE_SSL", True)
    user = getattr(config, "SMTP_USER", "") or ""
    password = getattr(config, "SMTP_PASSWORD", "") or ""
    from_addr = getattr(config, "SMTP_FROM", "")
    if not host:
        return False, "Не настроена отправка почты (SMTP_HOST)."

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    for file_path, file_name in attachments:
        if not file_path.is_file():
            return False, f"Файл вложения не найден: {file_name or file_path.name}"
        try:
            raw = file_path.read_bytes()
        except OSError as ex:
            return False, f"Не удалось прочитать файл {file_name or file_path.name}: {ex}"
        part = MIMEBase("application", "octet-stream")
        part.set_payload(raw)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=(file_name or file_path.name))
        msg.attach(part)

    try:
        if use_ssl or port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls()
                if user and password:
                    smtp.login(user, password)
                smtp.sendmail(from_addr, to_addrs, msg.as_string())
        return True, ""
    except smtplib.SMTPException as ex:
        logger.exception("SMTP error (multi attachments): %s", ex)
        return False, f"Ошибка отправки почты: {ex}"
    except OSError as ex:
        logger.exception("Ошибка сети при отправке почты (multi attachments): %s", ex)
        return False, f"Ошибка сети: {ex}"
