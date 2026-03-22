# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Mission Control.

Usage:  pyinstaller build.spec --noconfirm
Output: dist/MissionControl/MissionControl.exe
"""

import os

block_cipher = None
ROOT = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'server.py'), '.'),
        (os.path.join(ROOT, 'github_sync.py'), '.'),
        (os.path.join(ROOT, 'static', 'index.html'), 'static'),
        (os.path.join(ROOT, 'mc_tty_shim', 'sitecustomize.py'), 'mc_tty_shim'),
    ],
    hiddenimports=[
        'flask',
        'webview',
        'webview.platforms.edgechromium',
        'clr_loader',
        'pythonnet',
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
    [],
    exclude_binaries=True,
    name='MissionControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=os.path.join(ROOT, 'src-tauri', 'icons', 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MissionControl',
)
