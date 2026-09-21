# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch, AsyncMock
from pathlib import Path
import json

from front_site.inventory_control import (
    STATUS_COMPLETED,
    STATUS_NO_ASSETS,
    set_no_assets_confirmed,
    is_no_assets_confirmed,
    get_no_assets_confirmation,
    save_controlled_employee,
    get_controlled_employee,
    get_controlled_employee_by_email,
    refresh_controlled_employee,
    generate_inventory_control_csv,
    NO_ASSETS_CONFIRMATIONS_STORE_PATH,
    INVENTORY_CONTROL_STORE_PATH,
)


class TestNoAssetsConfirmation(unittest.TestCase):

    def setUp(self):
        # Backup or clear stores for testing
        self.orig_no_assets = None
        if NO_ASSETS_CONFIRMATIONS_STORE_PATH.exists():
            self.orig_no_assets = NO_ASSETS_CONFIRMATIONS_STORE_PATH.read_text(encoding="utf-8")
        NO_ASSETS_CONFIRMATIONS_STORE_PATH.write_text("{}", encoding="utf-8")

        self.orig_control = None
        if INVENTORY_CONTROL_STORE_PATH.exists():
            self.orig_control = INVENTORY_CONTROL_STORE_PATH.read_text(encoding="utf-8")
        INVENTORY_CONTROL_STORE_PATH.write_text("[]", encoding="utf-8")

    def tearDown(self):
        if self.orig_no_assets is not None:
            NO_ASSETS_CONFIRMATIONS_STORE_PATH.write_text(self.orig_no_assets, encoding="utf-8")
        if self.orig_control is not None:
            INVENTORY_CONTROL_STORE_PATH.write_text(self.orig_control, encoding="utf-8")

    def test_set_and_get_no_assets_confirmation(self):
        email = "ivanov@asg.ru"
        fio = "Иванов Иван Иванович"

        self.assertFalse(is_no_assets_confirmed(email=email, fio=fio))

        rec = set_no_assets_confirmed(fio=fio, email=email, confirmed=True, by=email)
        self.assertTrue(rec["confirmed"])
        self.assertTrue(is_no_assets_confirmed(email=email))
        self.assertTrue(is_no_assets_confirmed(fio=fio))

        conf = get_no_assets_confirmation(email=email)
        self.assertIsNotNone(conf)
        self.assertTrue(conf.get("confirmed"))

        # Verify it auto-created a record in inventory_control
        emp = get_controlled_employee_by_email(email)
        self.assertIsNotNone(emp)
        self.assertEqual(emp.get("status"), STATUS_COMPLETED)
        self.assertEqual(emp.get("progress_pct"), 100)
        self.assertEqual(emp.get("status_label"), "Пройдена (нет техники)")
        self.assertTrue(emp.get("no_assets_confirmed"))

    def test_toggle_off_no_assets_confirmation(self):
        email = "petrov@asg.ru"
        fio = "Петров Петр"

        set_no_assets_confirmed(fio=fio, email=email, confirmed=True, by="admin@asg.ru")
        self.assertTrue(is_no_assets_confirmed(email=email))

        # Toggle off
        set_no_assets_confirmed(fio=fio, email=email, confirmed=False, by="admin@asg.ru")
        self.assertFalse(is_no_assets_confirmed(email=email))

        emp = get_controlled_employee_by_email(email)
        self.assertIsNotNone(emp)
        self.assertEqual(emp.get("status"), STATUS_NO_ASSETS)
        self.assertEqual(emp.get("progress_pct"), 0)
        self.assertFalse(emp.get("no_assets_confirmed"))

    def test_csv_export_shows_no_assets_confirmed(self):
        email = "sidorov@asg.ru"
        fio = "Сидоров Семён"
        set_no_assets_confirmed(fio=fio, email=email, confirmed=True, by=email)

        csv_bytes = generate_inventory_control_csv()
        csv_str = csv_bytes.decode("utf-8")

        self.assertIn("Сидоров Семён", csv_str)
        self.assertIn("sidorov@asg.ru", csv_str)
        self.assertIn("Пройдена (нет техники)", csv_str)
        self.assertIn("100%", csv_str)
        self.assertIn("Корпоративная техника отсутствует", csv_str)

    async def _async_refresh_flow(self):
        email = "alex@asg.ru"
        fio = "Алексеев Алексей"
        rec = {
            "id": "emp-100",
            "fio": fio,
            "email": email,
            "total_assets": 0,
            "status": STATUS_NO_ASSETS,
            "no_assets_confirmed": True,
            "no_assets_confirmed_at": "2026-09-21 12:00:00",
        }
        save_controlled_employee(rec)

        # Mock client returning 0 assets
        mock_client = AsyncMock()
        mock_client.get_assets_by_fio.return_value = []

        def dummy_is_inv(a):
            return False

        updated = await refresh_controlled_employee(rec, mock_client, dummy_is_inv)
        self.assertEqual(updated.get("status"), STATUS_COMPLETED)
        self.assertEqual(updated.get("status_label"), "Пройдена (нет техники)")
        self.assertEqual(updated.get("progress_pct"), 100)
        self.assertTrue(updated.get("no_assets_confirmed"))

        # Now suppose A-Tracker now returned 1 asset
        mock_client.get_assets_by_fio.return_value = [
            {"ID": 1, "Name": "Ноутбук HP", "sSerialNo": "SN1", "sInventNumber": "INV1"}
        ]
        updated2 = await refresh_controlled_employee(rec, mock_client, dummy_is_inv)
        self.assertEqual(updated2.get("total_assets"), 1)
        self.assertFalse(updated2.get("no_assets_confirmed"))
        self.assertNotEqual(updated2.get("status"), STATUS_COMPLETED)

    def test_refresh_controlled_employee_behavior(self):
        import asyncio
        asyncio.run(self._async_refresh_flow())

    def test_endpoint_confirm_no_assets(self):
        from starlette.testclient import TestClient
        from front_site.app import app
        from front_site.auth_web import create_code

        client = TestClient(app, follow_redirects=False)

        # Unauthorized
        resp = client.post("/assets/confirm-no-assets")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.headers["location"], "/")

        fio = "Тестов Тест"
        email = "test.noassets@asg.ru"

        with patch("front_site.app._build_atracker_client") as mock_client_builder, \
             patch("front_site.app.send_code_email", return_value=(True, "")), \
             patch("front_site.app.send_plain_text_email", return_value=True):

            mock_atracker = AsyncMock()
            mock_atracker.get_employees.return_value = [{"sFullName": fio, "sEmail": email, "sLoginName": "test.noassets"}]
            # First return 1 asset -> should reject confirmation!
            mock_atracker.get_assets_by_fio.return_value = [{"ID": 999, "Name": "Laptop", "sSerialNo": "1", "sInventNumber": "1"}]
            mock_client_builder.return_value = mock_atracker

            # Send code
            start_resp = client.post("/start-auth", data={"identifier": email})
            self.assertEqual(start_resp.status_code, 302)

            # Enter code
            code = create_code(fio, email)
            enter_resp = client.post("/enter-code", data={"code": code})
            self.assertEqual(enter_resp.status_code, 302)
            self.assertEqual(enter_resp.headers["location"], "/assets")

            # Try confirm when user has 1 asset -> rejected!
            confirm_resp = client.post("/assets/confirm-no-assets")
            self.assertEqual(confirm_resp.status_code, 302)
            self.assertFalse(is_no_assets_confirmed(email=email))

            # Now mock 0 assets -> should succeed!
            mock_atracker.get_assets_by_fio.return_value = []
            confirm_ok_resp = client.post("/assets/confirm-no-assets")
            self.assertEqual(confirm_ok_resp.status_code, 302)
            self.assertTrue(is_no_assets_confirmed(email=email))

            # Check /assets page renders success block
            assets_page_resp = client.get("/assets")
            self.assertEqual(assets_page_resp.status_code, 200)
            self.assertIn("Инвентаризация успешно завершена", assets_page_resp.text)
            self.assertIn("Вы подтвердили, что корпоративная техника у вас отсутствует", assets_page_resp.text)

    def test_admin_toggle_no_assets_endpoint(self):
        from starlette.testclient import TestClient
        from front_site.app import app
        from front_site.auth_web import create_code
        import config

        client = TestClient(app, follow_redirects=False)

        # Create an employee in inventory control
        emp = {
            "id": "emp-test-admin-1",
            "fio": "Козлов Константин",
            "email": "kozlov@asg.ru",
            "login": "kozlov",
            "total_assets": 0,
            "inventoried_assets": 0,
            "status": STATUS_NO_ASSETS,
            "no_assets_confirmed": False,
        }
        save_controlled_employee(emp)

        # Non-admin request -> redirects to /assets
        non_admin_resp = client.post("/admin/inventory-control/emp-test-admin-1/toggle-no-assets")
        self.assertEqual(non_admin_resp.status_code, 302)
        self.assertEqual(non_admin_resp.headers["location"], "/assets")

        # Now authenticate as admin
        admin_email = "admin@asg.ru"
        admin_fio = "Администратор Системы"

        with patch("front_site.app.ADMIN_EMAILS", [admin_email]), \
             patch("front_site.app._build_atracker_client") as mock_client_builder, \
             patch("front_site.app.send_code_email", return_value=(True, "")):

            mock_atracker = AsyncMock()
            mock_atracker.get_employees.return_value = [{"sFullName": admin_fio, "sEmail": admin_email, "sLoginName": "admin"}]
            mock_atracker.get_assets_by_fio.return_value = []
            mock_client_builder.return_value = mock_atracker

            client.post("/start-auth", data={"identifier": admin_email})
            code = create_code(admin_fio, admin_email)
            client.post("/enter-code", data={"code": code})

            # Toggle on as admin
            toggle_on_resp = client.post("/admin/inventory-control/emp-test-admin-1/toggle-no-assets", data={"confirmed": "1"})
            self.assertEqual(toggle_on_resp.status_code, 302)
            self.assertEqual(toggle_on_resp.headers["location"], "/admin/inventory-control")

            updated_emp = get_controlled_employee("emp-test-admin-1")
            self.assertTrue(updated_emp.get("no_assets_confirmed"))
            self.assertEqual(updated_emp.get("status"), STATUS_COMPLETED)
            self.assertEqual(updated_emp.get("progress_pct"), 100)

            # Toggle off as admin
            toggle_off_resp = client.post("/admin/inventory-control/emp-test-admin-1/toggle-no-assets", data={"confirmed": "0"})
            self.assertEqual(toggle_off_resp.status_code, 302)

            updated_emp2 = get_controlled_employee("emp-test-admin-1")
            self.assertFalse(updated_emp2.get("no_assets_confirmed"))
            self.assertEqual(updated_emp2.get("status"), STATUS_NO_ASSETS)
            self.assertEqual(updated_emp2.get("progress_pct"), 0)


if __name__ == "__main__":
    unittest.main()
