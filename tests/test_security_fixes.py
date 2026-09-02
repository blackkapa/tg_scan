import os
import sys
import unittest
import time
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import config
from front_site.auth_web import (
    create_code,
    check_code,
    _match_domain,
    find_employee_by_input,
)
from front_site.mail_utils import _clean_header
from front_site.app import (
    _path_is_under_dir,
    _check_settings_secret,
    _check_auth_rate_limit,
    app,
)
from starlette.testclient import TestClient


class TestSecurityHardening(unittest.TestCase):
    def test_session_secret_loaded(self):
        """Проверка, что SESSION_SECRET_KEY не пустой и криптостойкий."""
        secret = config.SESSION_SECRET_KEY
        self.assertTrue(len(secret) >= 32)

    def test_settings_secret_timing_safe(self):
        """Проверка корректной валидации секрета настроек."""
        self.assertTrue(_check_settings_secret("whorebear"))
        self.assertFalse(_check_settings_secret("wrongpassword"))
        self.assertFalse(_check_settings_secret(""))

    def test_code_generation_and_limits(self):
        """Проверка генерации кодов, лимита попыток и привязки к email."""
        fio = "Тестов Тест Тестович"
        email = "test@asg.ru"
        code = create_code(fio, email)

        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())

        # Неверный email при проверке
        res_wrong_email = check_code(code, expected_email="other@asg.ru")
        self.assertIsNone(res_wrong_email)

        # 4 неудачные попытки с неправильным кодом
        for _ in range(4):
            check_code("000000", expected_email=email)

        # Правильный ввод на оставшейся попытке
        res_ok = check_code(code, expected_email=email)
        self.assertEqual(res_ok, (fio, email))

        # Повторный ввод (код уже должен быть сожжён)
        res_again = check_code(code, expected_email=email)
        self.assertIsNone(res_again)

    def test_code_max_attempts_burn(self):
        """Проверка, что после 5 неверных попыток код сгорает."""
        code = create_code("Иванов Иван", "ivanov@asg.ru")
        for _ in range(6):
            check_code(code, expected_email="wrong@asg.ru")

        # Теперь даже с правильным email код сожжён
        self.assertIsNone(check_code(code, expected_email="ivanov@asg.ru"))

    def test_domain_validation(self):
        """Проверка защиты от суффиксного обхода домена."""
        self.assertTrue(_match_domain("user@asg.ru", "asg.ru"))
        self.assertTrue(_match_domain("user@sub.asg.ru", "asg.ru"))
        self.assertFalse(_match_domain("user@evil-asg.ru", "asg.ru"))
        self.assertFalse(_match_domain("user@asg.ru.attacker.com", "asg.ru"))
        self.assertFalse(_match_domain("user@evilcorp.ru", "asg.ru"))
        self.assertFalse(_match_domain("invalid-email", "asg.ru"))

    def test_mail_header_sanitization(self):
        """Проверка санитизации заголовков от CRLF инъекций."""
        dirty = "Subject line\r\nBcc: victim@evil.com\nAnotherHeader: test"
        clean = _clean_header(dirty)
        self.assertNotIn("\r", clean)
        self.assertNotIn("\n", clean)
        self.assertEqual(clean, "Subject line Bcc: victim@evil.com AnotherHeader: test")

    def test_path_is_under_dir(self):
        """Проверка защиты от path traversal."""
        root = BASE_DIR / "front_site" / "uploads"
        safe_child = root / "transfers" / "doc.pdf"
        evil_traversal = root / ".." / ".." / "etc" / "passwd"

        self.assertTrue(_path_is_under_dir(safe_child, root))
        self.assertFalse(_path_is_under_dir(evil_traversal, root))

    def test_csrf_middleware(self):
        """Проверка блокировки внешних CSRF POST запросов."""
        client = TestClient(app)

        # GET запросы всегда проходят
        get_resp = client.get("/")
        self.assertEqual(get_resp.status_code, 200)

        # POST с легитимным origin / host
        legit_resp = client.post(
            "/start-auth",
            data={"identifier": ""},
            headers={"Host": "testserver", "Origin": "http://testserver"},
        )
        self.assertIn(legit_resp.status_code, (400, 422, 429, 502))

        # POST с атакующего Origin
        csrf_resp = client.post(
            "/start-auth",
            data={"identifier": "admin"},
            headers={"Host": "testserver", "Origin": "https://evil-attacker.com"},
        )
        self.assertEqual(csrf_resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
