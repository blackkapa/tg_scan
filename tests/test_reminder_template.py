# -*- coding: utf-8 -*-
import base64
import unittest
from unittest.mock import patch, MagicMock
from front_site.app import _reminder_text_to_html, _build_reminder_message
import config
from front_site.mail_utils import send_plain_text_email


class TestReminderTemplate(unittest.TestCase):

    def test_reminder_text_to_html(self):
        text = """Здравствуйте, Иван!

**Пожалуйста, проведите инвентаризацию закрепленной за вами рабочей техники.**

**Для проведения инвентаризации перейдите по ссылке:**
https://myinvent.ovp.ru

Вопросы на sd@asg.ru."""
        html = _reminder_text_to_html(text)
        self.assertIn("<b>Пожалуйста, проведите инвентаризацию закрепленной за вами рабочей техники.</b>", html)
        self.assertIn("<b>Для проведения инвентаризации перейдите по ссылке:</b>", html)
        self.assertIn('<a href="https://myinvent.ovp.ru"', html)
        self.assertIn('<a href="mailto:sd@asg.ru"', html)
        self.assertIn("<br>", html)

    def test_build_reminder_message(self):
        rec = {
            "fio": "Тестовый Сотрудник",
            "email": "test@asg.ru",
            "total_assets": 2,
            "inventoried_assets": 1,
            "assets_snapshot": [
                {"name": "Ноутбук Dell", "invent": "1001", "serial": "SN1001", "inventoried": True},
                {"name": "Монитор LG", "invent": "1002", "serial": "SN1002", "inventoried": False},
            ],
        }
        subj, body, html_body = _build_reminder_message(rec, "https://myinvent.ovp.ru")
        self.assertIn("Напоминание", subj)
        self.assertIn("Тестовый Сотрудник", body)
        self.assertIn("**Пожалуйста, проведите инвентаризацию закрепленной за вами рабочей техники.**", body)
        self.assertIn("**Для проведения инвентаризации перейдите по ссылке:**", body)
        self.assertIn("Монитор LG", body)
        self.assertIn("1 из 2", body)
        self.assertIn("<b>Пожалуйста, проведите инвентаризацию закрепленной за вами рабочей техники.</b>", html_body)
        self.assertIn("<b>Для проведения инвентаризации перейдите по ссылке:</b>", html_body)

    def test_build_reminder_message_zero_assets(self):
        rec = {
            "fio": "Без Техники",
            "email": "empty@asg.ru",
            "total_assets": 0,
            "inventoried_assets": 0,
            "assets_snapshot": [],
        }
        subj, body, html_body = _build_reminder_message(rec, "https://myinvent.ovp.ru")
        self.assertIn("не числится закрепленной техники", body)
        self.assertIn("не числится закрепленной техники", html_body)

    @patch("smtplib.SMTP")
    @patch("smtplib.SMTP_SSL")
    def test_send_plain_text_email_with_html(self, mock_ssl, mock_smtp):
        mock_instance = MagicMock()
        mock_ssl.return_value.__enter__.return_value = mock_instance
        ok, err = send_plain_text_email(["user@asg.ru"], "Тема", "Текст", html_body="<b>HTML</b>")
        self.assertTrue(ok)
        sent_raw = mock_instance.sendmail.call_args[0][2]
        self.assertIn("multipart/alternative", sent_raw)
        self.assertIn("text/html", sent_raw)
        self.assertIn(base64.b64encode(b"<b>HTML</b>").decode(), sent_raw)

    def test_config_reminder_getters_and_setters(self):
        orig_subj = config.get_reminder_subject()
        orig_tpl = config.get_reminder_template()
        self.assertTrue(len(orig_subj) > 0)
        self.assertTrue(len(orig_tpl) > 0)


if __name__ == "__main__":
    unittest.main()
