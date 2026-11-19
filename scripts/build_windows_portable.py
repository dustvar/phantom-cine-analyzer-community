"""Assemble a Windows x64 offline app from official CPython and Windows wheels.

This is vendoring an embedded interpreter, not cross-compiling an executable.
The assembled app still requires execution/validation on a Windows computer.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT/'app-source/modules/trackmeasure'


def relative_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or ':' in name or '\\' in name:
        raise ValueError(f'Unsafe archive path: {name}')
    return Path(*path.parts)


def unpack_wheel(wheel, runtime):
    site = runtime/'Lib/site-packages'
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            path = relative_member(info.filename)
            if info.is_dir():
                continue
            parts = path.parts
            if parts[0].endswith('.data'):
                if len(parts) < 3:
                    raise ValueError('Invalid wheel data path')
                if parts[1] in ('purelib', 'platlib'):
                    target = site.joinpath(*parts[2:])
                elif parts[1] == 'data':
                    target = runtime.joinpath(*parts[2:])
                elif parts[1] == 'scripts':
                    # These tools are not application entry points, but retain them.
                    target = runtime.joinpath('Scripts', *parts[2:])
                elif parts[1] == 'headers':
                    target = runtime.joinpath('Include', *parts[2:])
                else:
                    raise ValueError('Unknown wheel data category')
            else:
                target = site/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))


def validate_pe(path):
    with path.open('rb') as f:
        if f.read(2) != b'MZ':
            raise ValueError(f'Not a Windows executable: {path}')
        f.seek(0x3c);offset = struct.unpack('<I', f.read(4))[0]
        f.seek(offset)
        if f.read(4) != b'PE\0\0' or struct.unpack('<H', f.read(2))[0] != 0x8664:
            raise ValueError(f'Not Windows x64: {path}')


def build(output, interpreter, wheels, vendor):
    if output.exists():
        raise FileExistsError('Choose a new output directory; existing packages are never overwritten.')
    runtime = output/'runtime';runtime.mkdir(parents=True)
    with zipfile.ZipFile(interpreter) as archive:
        for info in archive.infolist():
            path = runtime/relative_member(info.filename)
            if not info.is_dir():
                path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(archive.read(info))
    manifest = {'validation': 'Assembled on macOS. Windows execution required.', 'python_archive':
                {'name': interpreter.name, 'sha256': hashlib.sha256(interpreter.read_bytes()).hexdigest()}, 'wheels': {}}
    distributions = set()
    for wheel in sorted(wheels.glob('*.whl')):
        distribution = wheel.name.split('-')[0].lower().replace('_', '-')
        if distribution in distributions:
            raise ValueError(f'Duplicate distribution in wheel folder: {distribution}')
        distributions.add(distribution)
        if not (wheel.name.endswith('win_amd64.whl') or wheel.name.endswith('none-any.whl')):
            raise ValueError(f'Wrong platform wheel: {wheel.name}')
        unpack_wheel(wheel, runtime)
        manifest['wheels'][wheel.name] = hashlib.sha256(wheel.read_bytes()).hexdigest()
    site = runtime/'Lib/site-packages'
    shutil.copytree(vendor, site, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
    (runtime/'python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\nimport site\n', encoding='utf-8')
    shutil.copytree(MODULE, output/'app', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '*.log'))
    # Cross-computer defaults must not carry the author's calibration or preferences.
    config = json.loads((output/'app/config.json').read_text())
    config.update(image_scale=1.0, known_length=1.0, length_units='pix', time_scale=1, time_units='s')
    (output/'app/config.json').write_text(json.dumps(config, indent=4)+'\n')
    shutil.copy2(ROOT/'LICENSE', output/'LICENSE.txt')
    shutil.copy2(ROOT/'packaging/README-Windows.txt', output/'READ ME FIRST.txt')
    shutil.copy2(ROOT/'docs/COMPACT_WORKSPACE.md', output/'Workspace Guide.txt')
    shutil.copy2(ROOT/'packaging/FEATURES-Windows.txt', output/'What is included.txt')
    shutil.copy2(ROOT/'docs/PRECISION_AND_PERFORMANCE.md', output/'New controls and performance.txt')
    shutil.copy2(ROOT/'docs/TRACKING_WORKSPACE.md', output/'Tracking tools guide.txt')
    shutil.copy2(ROOT/'packaging/diagnose_install.py', output/'app/diagnose_install.py')
    shutil.copy2(ROOT/'packaging/create_desktop_shortcut.py', output/'app/create_desktop_shortcut.py')
    (output/'Start PCA.cmd').write_text('@echo off\nsetlocal\n'
        'if not exist "%~dp0runtime\\pythonw.exe" (\n'
        ' echo Extract the entire ZIP before opening PCA.\n pause\n exit /b 1\n)\n'
        'start "" "%~dp0runtime\\pythonw.exe" -B "%~dp0app\\desktop_bootstrap.py" %*\n', newline='\r\n')
    (output/'Check Installation.cmd').write_text('@echo off\nsetlocal\n'
        '"%~dp0runtime\\python.exe" -B "%~dp0app\\diagnose_install.py"\n'
        'set "PCA_RESULT=%ERRORLEVEL%"\necho.\npause\nexit /b %PCA_RESULT%\n', newline='\r\n')
    (output/'Create PCA Desktop Icon.cmd').write_text('@echo off\nsetlocal\n'
        '"%~dp0runtime\\python.exe" -B "%~dp0app\\create_desktop_shortcut.py"\n'
        'set "PCA_RESULT=%ERRORLEVEL%"\necho.\npause\nexit /b %PCA_RESULT%\n', newline='\r\n')
    native = sorted(path for path in runtime.rglob('*')
                    if path.is_file() and path.suffix.lower() in ('.pyd','.exe','.dll'))
    for path in native:
        validate_pe(path)
    manifest['x64_native_modules_checked'] = len(native)
    manifest['source_version'] = '1.14.3'
    manifest['source_files'] = {str(p.relative_to(output/'app')): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted((output/'app').rglob('*.py'))}
    (output/'BUILD-DETAILS.json').write_text(json.dumps(manifest, indent=2)+'\n')
    archive_path = output.parent/(output.name+'.zip')
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():archive.write(path, Path(output.name)/path.relative_to(output))
    print(f'Created {archive_path}; checked {len(native)} Windows x64 modules. Not executed on Windows.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python-zip', type=Path, required=True)
    parser.add_argument('--wheels', type=Path, required=True)
    parser.add_argument('--vendor', type=Path, required=True)
    args = parser.parse_args()
    build(args.output, args.python_zip, args.wheels, args.vendor)
