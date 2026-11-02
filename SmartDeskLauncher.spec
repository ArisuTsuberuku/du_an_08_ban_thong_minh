# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

datas = []
datas += collect_data_files('uiautomation')


a = Analysis(
    ['he_thong_quan_ly_3_cap.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['core.config_manager', 'core.failsafe', 'services.giam_sat_worker', 'services.posture_controller', 'services.telegram_service', 'telegram_service', 'trang_web', 'win32gui', 'win32process', 'uiautomation', 'comtypes', 'comtypes.client', 'comtypes.gen'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['cv2', 'mediapipe', 'matplotlib', 'sounddevice', 'numpy', 'scipy', 'pandas', 'pyarrow', 'onnxruntime', 'chromadb', 'lxml', 'openpyxl', 'pymupdf', 'pdfplumber', 'grpc', 'cryptography'],
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
    name='SmartDeskLauncher',
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
    uac_admin=True,
)
