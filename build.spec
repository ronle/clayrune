# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Mission Control.

Usage:  python pre_build_fix.py && pyinstaller build.spec --noconfirm
Output: dist/MissionControl/MissionControl.exe

IMPORTANT: Run pre_build_fix.py first to fix .NET DLL variants!
"""

import os
import site
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
ROOT = os.path.dirname(os.path.abspath(SPEC))

# Collect ALL webview submodules — pywebview's own hook only collects data files,
# not hidden imports.  guilib.py dynamically imports webview.platforms.winforms
# inside a function, which PyInstaller's static analysis cannot detect.
_webview_hiddens = collect_submodules('webview')
_clr_loader_hiddens = collect_submodules('clr_loader')

# Collect pythonnet and clr_loader packages from site-packages
_pythonnet_datas = []
for _sp in site.getsitepackages():
    # pythonnet runtime directory (DLL + runtimeconfig)
    _pn_runtime = os.path.join(_sp, 'pythonnet', 'runtime')
    if os.path.isdir(_pn_runtime):
        for _f in os.listdir(_pn_runtime):
            _pythonnet_datas.append((os.path.join(_pn_runtime, _f), 'pythonnet/runtime'))

    # pythonnet package Python files
    _pn_pkg = os.path.join(_sp, 'pythonnet')
    if os.path.isdir(_pn_pkg):
        for _f in os.listdir(_pn_pkg):
            _fp = os.path.join(_pn_pkg, _f)
            if os.path.isfile(_fp) and _f.endswith('.py'):
                _pythonnet_datas.append((_fp, 'pythonnet'))

    # clr_loader package (needed by pythonnet)
    _clr = os.path.join(_sp, 'clr_loader')
    if os.path.isdir(_clr):
        for _root, _dirs, _files in os.walk(_clr):
            _rel = os.path.relpath(_root, _sp)
            for _f in _files:
                _pythonnet_datas.append((os.path.join(_root, _f), _rel))

    if _pythonnet_datas:
        break  # Found packages, stop searching

a = Analysis(
    [os.path.join(ROOT, 'app.py')],
    pathex=[ROOT],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'server.py'), '.'),
        (os.path.join(ROOT, 'github_sync.py'), '.'),
        (os.path.join(ROOT, 'static', 'index.html'), 'static'),
        (os.path.join(ROOT, 'mc_tty_shim', 'sitecustomize.py'), 'mc_tty_shim'),
    ] + _pythonnet_datas,
    hiddenimports=[
        'flask',
        'pythonnet',
    ] + _webview_hiddens + _clr_loader_hiddens,
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
