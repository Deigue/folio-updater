# -*- mode: python ; coding: utf-8 -*-

import os

from PyInstaller.utils.hooks import copy_metadata

# PyInstaller spec for folio CLI
# This excludes all unnecessary modules to minimize distribution size

# Build pathex with conditional local ws-api path
pathex = ['src']
ws_api_local_path = os.path.join('..', 'ws-api-python')
if os.path.exists(ws_api_local_path):
    pathex.append(os.path.abspath(ws_api_local_path))

a = Analysis(
    ['src/cli/main.py'],
    pathex=pathex,
    binaries=[],
    # Bundle folio-updater's dist-info so importlib.metadata.version() works
    # at runtime, keeping the version defined solely in pyproject.toml.
    datas=copy_metadata('folio-updater'),
    hiddenimports=[
        # Essential hidden imports only
        'openpyxl.styles.stylesheet',
        'secrets',  # Required by numpy.random
        # ws_api package - ensure it's included whether from local source or PyPI
        'ws_api',
        # yfinance backs `folio dash` / `folio quotes`. It is imported lazily so
        # startup stays fast, which is exactly why PyInstaller cannot see it.
        'yfinance',
        'curl_cffi',
        'peewee',
        'platformdirs',
        'websockets',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Development dependencies (from pyproject.toml dev group)
        'ipykernel',
        'nbstripout', 
        'pyinstaller',
        'ruff',
        
        # Test dependencies (from pyproject.toml test group)
        'pytest',
        'pytest_cov',
        'coverage',
        
        # Jupyter/IPython ecosystem (dev dependencies, not runtime)
        'IPython',
        'jupyter',
        'jupyter_client',
        'jupyter_core',
        'nbformat',
        'nbconvert',
        'notebook',
        'qtconsole',
        'traitlets',
        'jedi',
        'parso',
        
        # GUI libraries (not needed for CLI)
        'tkinter',
        'turtle',
        '_tkinter',
        'Tkinter',
        'tk',
        
        # Plotting/visualization (not used in CLI)
        'matplotlib',
        'pyplot',
        'seaborn',
        'plotly',
        'bokeh',
        'scipy',
        'sklearn',
        
        # Development/debugging tools
        'doctest',
        'pdb',
        'profile',
        'cProfile',
        'pstats',
        'trace',
        'timeit',
        
        # Audio/video
        'wave',
        'aifc',
        'sunau',
        'sndhdr',
        'ossaudiodev',
        'audioop',
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='folio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # Disable UPX as it can cause issues and size benefit is minimal
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='folio',
)
