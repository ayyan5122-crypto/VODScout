# -*- mode: python ; coding: utf-8 -*-

# ── CHANGE 1: import PyInstaller helper utilities ─────────────────────────────
# collect_data_files  → copies non-Python files that live inside a package tree
# collect_dynamic_libs → copies .dll / .so / .dylib native libraries
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

# ── CHANGE 2: faster-whisper data files ──────────────────────────────────────
# The root cause of the crash.  faster_whisper ships a plain-file asset
# directory that PyInstaller never sees during its static analysis pass.
# collect_data_files() walks the installed package tree and returns a list of
# (src_path, dest_path) tuples so that _MEIPASS/faster_whisper/assets/* — and
# specifically silero_vad_v6.onnx — will exist at runtime.  Because
# faster_whisper resolves the path via os.path.dirname(__file__), the dest
# path must mirror the original package layout exactly, which collect_data_files
# guarantees automatically.
fw_datas = collect_data_files('faster_whisper')

# ── CHANGE 3: onnxruntime native libraries ────────────────────────────────────
# onnxruntime ships several shared libraries next to its Python files
# (libonnxruntime.so / onnxruntime.dll, provider DLLs, the pybind11 .so).
# collect_dynamic_libs() finds every .dll / .so in the package directory and
# returns (src, dest) tuples for the binaries= list.  Without this, the ONNX
# InferenceSession that loads silero_vad_v6.onnx will fail even if the .onnx
# file itself is present.
onnx_binaries = collect_dynamic_libs('onnxruntime')

# ── CHANGE 4: onnxruntime Python-layer data files ─────────────────────────────
# Picks up _pybind_state.py, _ld_preload.py, onnxruntime_validation.py, etc.
# that are required at import time but are plain .py/.json files that
# collect_dynamic_libs skips.
onnx_datas = collect_data_files('onnxruntime')

# ── CHANGE 5: ctranslate2 native libraries ────────────────────────────────────
# ctranslate2 is the core C++ backend that faster-whisper wraps.  It bundles
# its own shared libraries (BLAS / oneDNN / CUDA stubs) which PyInstaller
# cannot locate through import analysis alone.
ct2_binaries = collect_dynamic_libs('ctranslate2')


a = Analysis(
    ['app.py'],
    pathex=[],
    # ── CHANGE 6: wire the collected binaries into the Analysis ──────────────
    binaries=onnx_binaries + ct2_binaries,
    # ── CHANGE 7: wire the collected data files into the Analysis ────────────
    datas=fw_datas + onnx_datas + [
    ("tools", "tools"),
    ],
    # ── CHANGE 8: hidden imports ─────────────────────────────────────────────
    # PyInstaller performs static analysis (AST import scanning).  Any module
    # loaded at runtime via importlib, __import__, or a C-extension bootstrap
    # is invisible to that scan and must be declared explicitly.
    hiddenimports=[
        # faster-whisper + its pure-Python dependencies
        'faster_whisper',           # top-level package marker
        'ctranslate2',              # C++ backend; __init__ bootstraps the .so
        'huggingface_hub',          # model downloading; uses dynamic loaders
        'tokenizers',               # Rust-backed .so with a thin Python shim

        # onnxruntime C-extension entry points
        # _pybind_state.py is the Python shim that does
        #   from .onnxruntime_pybind11_state import *
        # PyInstaller sees _pybind_state but can miss the C extension import
        # inside it, so both are listed for safety.
        'onnxruntime',
        'onnxruntime.capi',
        'onnxruntime.capi._pybind_state',

        # yt-dlp registers extractors via importlib.import_module() at
        # runtime from a generated list; the individual extractor modules
        # are invisible to static analysis unless the top-level extractors
        # registry file is declared.
        'yt_dlp',
        'yt_dlp.utils',
        'yt_dlp.extractor',
        'yt_dlp.extractor.extractors',
    ],
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
    name='VODScout',
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
    icon=['assets\\icon.ico'],
)
