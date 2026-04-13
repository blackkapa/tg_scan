#!/usr/bin/env bash

set -e

PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
  echo "Создаю виртуальное окружение в $VENV_DIR..."
  $PYTHON_BIN -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "Устанавливаю зависимости..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Запуск веб-приложения (Ctrl+C для остановки)..."
python -m uvicorn front_site.app:app --host 127.0.0.1 --port 8000 --reload
