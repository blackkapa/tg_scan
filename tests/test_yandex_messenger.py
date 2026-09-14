# -*- coding: utf-8 -*-
import io
import json
import unittest
from unittest.mock import patch, MagicMock
from front_site.yandex_messenger import send_yandex_message, _sync_send_yandex_message


class TestYandexMessenger(unittest.IsolatedAsyncioTestCase):

    async def test_empty_params(self):
        ok, err = await send_yandex_message("", "test", "token123")
        self.assertFalse(ok)
        self.assertIn("Не указан логин", err)

        ok, err = await send_yandex_message("user@asg.ru", "test", "")
        self.assertFalse(ok)
        self.assertIn("Не указан токен", err)

        ok, err = await send_yandex_message("user@asg.ru", "", "token123")
        self.assertFalse(ok)
        self.assertIn("Пустой текст", err)

    @patch("urllib.request.urlopen")
    async def test_send_success(self, mock_urlopen):
        resp_data = json.dumps({"ok": True, "message_id": 12345}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, err = await send_yandex_message("user@asg.ru", "Привет!", "fake-token")
        self.assertTrue(ok)
        self.assertEqual(err, "")

    @patch("urllib.request.urlopen")
    async def test_send_api_error(self, mock_urlopen):
        resp_data = json.dumps({"ok": False, "description": "User not found"}).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = resp_data
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, err = await send_yandex_message("unknown@asg.ru", "Привет!", "fake-token")
        self.assertFalse(ok)
        self.assertIn("User not found", err)


if __name__ == "__main__":
    unittest.main()
