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

echo "Запускаю бота..."
python bot.py

