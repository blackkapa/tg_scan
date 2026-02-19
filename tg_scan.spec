# ОДИН EXE (one-file). Рядом с exe: config.ini, папка data\
# Сборка: pyinstaller --noconfirm tg_scan.spec

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

aiohttp_datas, aiohttp_binaries, aiohttp_hidden = collect_all('aiohttp')
aiogram_hidden = collect_submodules('aiogram')

# .py проекта в архив exe → при запуске распаковываются в _MEIPASS, import bot находит bot.py
_project_py = [
    'bot.py', 'config.py', 'atracker_client.py', 'auth_by_email.py',
    'registry_reader.py', 'run_registry_check.py', 'sync_ad_atracker.py',
]
project_datas = [(f, '.') for f in _project_py]

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=aiohttp_binaries,
    datas=aiohttp_datas + project_datas,
    hiddenimports=[
        'bot',
        'run_registry_check',
        'sync_ad_atracker',
        'registry_reader',
        'config',
        'atracker_client',
        'auth_by_email',
        'cv2',
        'numpy',
        'openpyxl',
        'xlrd',
    ] + aiohttp_hidden + aiogram_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['pyi_rth_meipass.py'],
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
