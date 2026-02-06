# Один exe: бот + сверка AD в 01:00 + реестр в 07:00. Точка входа main.py
# Сборка: pyinstaller --noconfirm tg_scan.spec
# Рядом с exe: config.ini, data\, ad_export.json (выгрузка AD через PowerShell)

# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'bot',
        'run_registry_check',
        'sync_ad_atracker',
        'registry_reader',
        'config',
        'atracker_client',
        'cv2',
        'numpy',
        'aiohttp',
        'aiogram',
        'aiogram.client',
        'aiogram.client.default',
        'aiogram.enums',
        'aiogram.filters',
        'aiogram.fsm',
        'aiogram.types',
        'aiogram.utils',
        'openpyxl',
        'xlrd',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='tg_scan',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
