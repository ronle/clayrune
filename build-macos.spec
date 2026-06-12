# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the macOS Clayrune build.
#
# Differs from the (unchecked-in) Windows build.spec in three ways:
#   1. No pythonnet / .NET / WebView2 — macOS pywebview uses Cocoa/WKWebView.
#   2. Bundles the Cocoa platform module via collect_submodules('webview'),
#      same dynamic-import gotcha as Windows' winforms module (BUILD_INSTRUCTIONS.md
#      §Critical: build.spec Hidden Imports).
#   3. BUNDLE step at the end produces a real .app for double-click launch.
#
# Build locally on a Mac (or via .github/workflows/build-macos.yml):
#   pyinstaller build-macos.spec --noconfirm
#
# Output: dist/Clayrune.app  →  zip into MissionControl-macOS.zip for release.

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# pywebview imports its Cocoa backend dynamically inside guilib.py — static
# analysis misses it, so the native window silently fails to open and the
# app falls back to opening Safari. collect_submodules('webview') is the
# same fix Windows uses for winforms (see BUILD_INSTRUCTIONS.md).
hidden = []
hidden += collect_submodules('webview')
hidden += collect_submodules('flask')
# firebase-admin pulls grpc dynamically; missing submodules surface as
# silent push-failure at runtime, not a build error.
hidden += collect_submodules('firebase_admin')
hidden += collect_submodules('google')

# Bundle templates / static / data scaffolding next to app.py so the frozen
# binary sees the same layout as `python app.py`.
datas = [
    ('static', 'static'),
    # Claydo mascot webp/icons live in assets/ and are served by the
    # /assets/<file> Flask route. Bundle them or the UI shows broken images
    # (the FAB + the agent avatar) in the frozen app.
    ('assets', 'assets'),
    ('installer/clayrune.png', 'installer'),
]

# Add SHARED_RULES.md only if present (it's user data; ships as a seed file
# on the Windows side via app.py first-run logic).
import os
if os.path.exists('data/SHARED_RULES.md'):
    datas.append(('data/SHARED_RULES.md', 'data'))

# Claydo reads these from _SERVER_DIR at runtime: USER_GUIDE + CHANGELOG feed
# ask-mode context; docs/claydo/ holds the builder-mode briefs
# (PROMPT_BUILDER_DESIGN.md §5). Without them the frozen app's Claydo 500s.
if os.path.exists('docs/USER_GUIDE.md'):
    datas.append(('docs/USER_GUIDE.md', 'docs'))
if os.path.exists('CHANGELOG.md'):
    datas.append(('CHANGELOG.md', '.'))
if os.path.isdir('docs/claydo'):
    datas.append(('docs/claydo', 'docs/claydo'))

# Include any extra Python modules the app loads from the repo root.
# server.py is implicitly bundled because app.py imports it.
datas += collect_data_files('webview')

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only — fails to import on macOS and pulls nothing useful.
        'pythonnet',
        'clr',
        'clr_loader',
        'winreg',
    ],
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
    name='Clayrune',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # GUI app — no Terminal window
    disable_windowed_traceback=False,
    target_arch=None,  # let host arch decide (CI runners are arm64)
    codesign_identity=None,  # unsigned per project policy
    entitlements_file=None,
    icon='installer/clayrune.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='Clayrune',
)

app = BUNDLE(
    coll,
    name='Clayrune.app',
    icon='installer/clayrune.png',
    bundle_identifier='io.clayrune.app',
    info_plist={
        'CFBundleName': 'Clayrune',
        'CFBundleDisplayName': 'Clayrune',
        'CFBundleShortVersionString': '1.5.1',
        'CFBundleVersion': '1.5.1',
        'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True,
        # Network access — server binds to localhost:5199 inside the app.
        'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True},
    },
)
