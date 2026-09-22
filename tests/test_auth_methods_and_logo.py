import unittest
from pathlib import Path
from starlette.testclient import TestClient
from front_site.auth_web import find_employee_by_input
from front_site.app import app
import config


class TestAuthMethodsAndLogo(unittest.TestCase):
    def setUp(self):
        self.dummy_employees = [
            {
                "sFullName": "Иванов Иван Иванович",
                "sLoginName": "asg\\ivanov",
                "sEmail": "ivanov@asg.ru",
                "ID": 101,
            },
            {
                "sFullName": "Петров Петр Петрович",
                "sLoginName": "petrov",
                "sEmail": "petrov@asg.ru",
                "ID": 102,
            },
            {
                "sFullName": "Сидоров Сидор",
                "sLoginName": "sidorov",
                "sEmail": "sidorov@gmail.com",  # non-asg domain
                "ID": 103,
            },
            {
                "sFullName": "Безпочтов Некто",
                "sLoginName": "noemail",
                "sEmail": "",
                "ID": 104,
            },
        ]

    def test_email_only_mode(self):
        """Когда включен только вход по почте (по умолчанию)."""
        # Поиск по почте успешен
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "ivanov@asg.ru",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=False,
        )
        self.assertIsNone(err)
        self.assertEqual(fio, "Иванов Иван Иванович")
        self.assertEqual(email, "ivanov@asg.ru")

        # Поиск по ФИО блокируется
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "Иванов Иван Иванович",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=False,
        )
        self.assertIsNotNone(err)
        self.assertIn("Вход по ФИО и логину временно отключён", err)
        self.assertIn("@asg.ru", err)

        # Поиск по логину блокируется
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "ivanov",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=False,
        )
        self.assertIsNotNone(err)
        self.assertIn("Вход по ФИО и логину временно отключён", err)

        # Пустой ввод в режиме только email
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=False,
        )
        self.assertEqual(err, "Введите корпоративную почту.")

    def test_fio_allowed_mode(self):
        """Когда разрешен вход по ФИО, но логин выключен."""
        # По ФИО находит
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "Иванов Иван Иванович",
            "asg.ru",
            allow_email=True,
            allow_fio=True,
            allow_login=False,
        )
        self.assertIsNone(err)
        self.assertEqual(fio, "Иванов Иван Иванович")

        # По логину сообщает, что логин отключен
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "petrov",
            "asg.ru",
            allow_email=True,
            allow_fio=True,
            allow_login=False,
        )
        self.assertIsNotNone(err)
        self.assertIn("Вход по логину временно отключён", err)

    def test_login_allowed_mode(self):
        """Когда разрешен вход по логину, но ФИО выключен."""
        # По логину находит
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "petrov",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=True,
        )
        self.assertIsNone(err)
        self.assertEqual(email, "petrov@asg.ru")

        # По ФИО сообщает, что ФИО отключено
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "Петров Петр Петрович",
            "asg.ru",
            allow_email=True,
            allow_fio=False,
            allow_login=True,
        )
        self.assertIsNotNone(err)
        self.assertIn("Вход по ФИО временно отключён", err)

    def test_email_disabled_mode(self):
        """Когда вход по почте отключен."""
        fio, email, err = find_employee_by_input(
            self.dummy_employees,
            "ivanov@asg.ru",
            "asg.ru",
            allow_email=False,
            allow_fio=True,
            allow_login=True,
        )
        self.assertIsNotNone(err)
        self.assertIn("Вход по корпоративной почте временно отключён", err)

    def test_index_page_contains_email_only_copy(self):
        """На главной странице отображается правильный текст и поле почты."""
        client = TestClient(app)
        resp = client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        # Проверяем обновленный текст
        self.assertIn("Войдите под корпоративной учётной записью, введя корпоративную почту", html)
        self.assertIn("placeholder=\"ivanov@asg.ru\"", html)
        self.assertIn("Корпоративная почта", html)

    def test_logo_and_favicons_exist(self):
        """Логотип АСГ и фавиконы созданы и доступны."""
        static_dir = Path(__file__).resolve().parent.parent / "front_site" / "static"
        logo = static_dir / "asg_logo.png"
        favicon_png = static_dir / "favicon.png"
        favicon_ico = static_dir / "favicon.ico"

        self.assertTrue(logo.is_file(), "asg_logo.png does not exist")
        self.assertTrue(favicon_png.is_file(), "favicon.png does not exist")
        self.assertTrue(favicon_ico.is_file(), "favicon.ico does not exist")
        self.assertGreater(logo.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
