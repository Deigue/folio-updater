# -*- mode: python ; coding: utf-8 -*-

# PyInstaller spec for folio CLI
# This excludes all unnecessary modules to minimize distribution size

a = Analysis(
    ['src/cli/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Essential hidden imports only
        'openpyxl.styles.stylesheet',
        'secrets',  # Required by numpy.random
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
