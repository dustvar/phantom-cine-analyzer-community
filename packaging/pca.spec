# Self-contained native desktop app. Build on the target OS/architecture.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

repo = Path(SPECPATH).parent
module = repo / 'app-source/modules/trackmeasure'
vendor = Path(os.environ['PCA_VENDOR_SITE']).resolve()
assets = []
for source in module.rglob('*'):
    if source.is_file() and source.suffix not in ('.py', '.pyc') and '__pycache__' not in source.parts and source.name != '.DS_Store':
        assets.append((str(source), str(source.parent.relative_to(module))))
assets.append((str(repo/'LICENSE'), 'licenses'))
assets += collect_data_files('imageio_ffmpeg')
for name in ('numpy', 'opencv-contrib-python', 'PySide6', 'PySide6_Addons',
             'PySide6_Essentials', 'shiboken6', 'scipy', 'pandas', 'matplotlib',
             'numba', 'llvmlite', 'fpdf2', 'defusedxml', 'fonttools', 'Pillow',
             'imageio-ffmpeg', 'imageio', 'python-dateutil', 'six', 'scikit-image',
             'networkx', 'lazy-loader', 'tifffile', 'psutil', 'kiwisolver',
             'contourpy', 'cycler', 'packaging', 'pyparsing', 'pytz', 'tzdata'):
    assets += copy_metadata(name)
# Preserve notices/metadata for the authorized PCA helper packages too.
for directory in vendor.glob('*.dist-info'):
    for source in directory.rglob('*'):
        if source.is_file():
            assets.append((str(source), str(source.parent.relative_to(vendor))))
# The helper __init__ modules enumerate and import adjacent .py files at runtime.
# Keep those sources on disk and analyze their dynamic imports/dependencies.
vendor_modes = {}
vendor_imports = []
for directory in vendor.iterdir():
    if not (directory/'__init__.py').is_file():
        continue
    vendor_modes[directory.name] = 'pyz+py'
    for source in directory.rglob('*'):
        if source.suffix == '.py':
            parts = list(source.relative_to(vendor).with_suffix('').parts)
            if parts[-1] == '__init__':
                parts.pop()
            vendor_imports.append('.'.join(parts))
        elif source.is_file() and source.suffix != '.pyc' and '__pycache__' not in source.parts:
            assets.append((str(source), str(source.parent.relative_to(vendor))))
analysis = Analysis(
    [str(module/'desktop_bootstrap.py')], pathex=[str(module), str(vendor)],
    binaries=[], datas=assets, hiddenimports=['startup_diagnostics']+vendor_imports,
    hookspath=[], hooksconfig={'matplotlib': {'backends': ['QtAgg']}},
    runtime_hooks=[], excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'IPython', 'pytest'],
    noarchive=False, module_collection_mode=vendor_modes,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True,
          name='PhantomCineAnalyzer', debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, argv_emulation=False,
          target_arch='arm64', codesign_identity=None, entitlements_file=None,
          icon=str(module/'images/icon.icns'))
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False,
                     name='PhantomCineAnalyzer')
app = BUNDLE(collection, name='Phantom Cine Analyzer Additional Features.app',
             icon=str(module/'images/icon.icns'),
             bundle_identifier='local.phantom-cine-analyzer.playback', version='1.14.9',
             info_plist={
                 'CFBundleDisplayName':'Phantom Cine Analyzer Additional Features',
                 'CFBundleShortVersionString':'1.14.9',
                 'LSMinimumSystemVersion':'13.0', 'NSHighResolutionCapable':True,
                 'NSPrincipalClass':'NSApplication',
                 'CFBundleDocumentTypes':[
                     {'CFBundleTypeName':'Phantom Analyzer Job', 'CFBundleTypeExtensions':['pcajob'],
                      'CFBundleTypeRole':'Editor', 'LSHandlerRank':'Owner'},
                     {'CFBundleTypeName':'Phantom Cine Video', 'CFBundleTypeExtensions':['cine'],
                      'CFBundleTypeRole':'Viewer', 'LSHandlerRank':'Alternate'}]})
