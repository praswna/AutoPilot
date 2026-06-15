# -*- mode: python ; coding: utf-8 -*-

import glob

# 화면 인식용 PNG는 패턴으로 모두 포함 (generating*, limit_warning*, ready*,
# step*_complete* + 아이콘). 단일 파일만 지정하면 generating2.png /
# limit_warning2.png / ready2.png / stepN_complete.png 등이 누락된다.
_png_datas = [(f, '.') for f in (
    glob.glob('generating*.png') + glob.glob('limit_warning*.png')
    + glob.glob('ready*.png') + glob.glob('step*_complete*.png')
)]
_png_datas.append(('icon.ico', '.'))

# 기본 스텝 저장 파일을 함께 번들 (실행 파일 옆 steps.json 이 없을 때 폴백으로 사용).
import os as _os
if _os.path.exists('steps.json'):
    _png_datas.append(('steps.json', '.'))

a = Analysis(
    ['auto_pilot.py'],
    pathex=[],
    binaries=[],
    datas=_png_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='auto_pilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico',
)
