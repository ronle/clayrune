# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Mission Control.

Usage:  python pre_build_fix.py && pyinstaller build.spec --noconfirm
Output: dist/MissionControl/MissionControl.exe

IMPORTANT: Run pre_build_fix.py first to fix .NET DLL variants!
"""

import os
import site

block_cipher = None
ROOT = os.path.dirname(os.path.abspath(SPEC))

# Find pythonnet runtimeconfig.json in site-packages
_runtimeconfig_datas = []
for _sp in site.getsitepackages():
    _rc = os.path.join(_sp, 'pythonnet', 'runtime', 'Python.Runtime.runtimeconfig.json')
    if os.path.exists(_rc):
        _runtimeconfig_datas.append((_rc, 'pythonnet/runtime'))
        break

a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'server.py'), '.'),
        (os.path.join(ROOT, 'github_sync.py'), '.'),
        (os.path.join(ROOT, 'static', 'index.html'), 'static'),
        (os.path.join(ROOT, 'mc_tty_shim', 'sitecustomize.py'), 'mc_tty_shim'),
    ] + _runtimeconfig_datas,
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
    console=True,
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
