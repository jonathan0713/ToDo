# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path


_runtime_dir = Path(sys.base_prefix) / 'Library' / 'bin'
_runtime_names = [
    'tcl86t.dll',
    'tk86t.dll',
    'ffi.dll',
    'libmpdec-4.dll',
    'zstd.dll',
    'liblzma.dll',
    'LIBBZ2.dll',
    'libexpat.dll',
]
_runtime_binaries = [
    (str(_runtime_dir / name), '.')
    for name in _runtime_names
    if (_runtime_dir / name).exists()
]


a = Analysis(
    ['todo.py'],
    pathex=[],
    binaries=_runtime_binaries,
    datas=[
        ('left_arrow.png', '.'),
        ('right_arrow.png', '.'),
        ('icon.png', '.'),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'scipy', 'matplotlib'],
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
    name='todo',
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
    icon=['icon.png'],
)
