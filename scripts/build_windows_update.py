"""Build an app-only update for a compatible extracted PCA Windows folder.

The full portable build supplies the application and launchers. Both manifests
must identify the same embedded Python archive and dependency wheels; this
update deliberately contains no runtime and is not a standalone installation.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE_VERSIONS = ('1.14.1', '1.14.2')
TARGET_VERSION = '1.14.3'
REQUIRED_FILES = (
    'BUILD-DETAILS.json', 'LICENSE.txt', 'READ ME FIRST.txt',
    'Workspace Guide.txt', 'What is included.txt',
    'New controls and performance.txt', 'Tracking tools guide.txt', 'Start PCA.cmd',
    'Check Installation.cmd', 'Create PCA Desktop Icon.cmd',
)
REQUIRED_APP_FILES = (
    'desktop_bootstrap.py', 'app_runtime.py', 'diagnose_install.py',
    'create_desktop_shortcut.py', 'images/icon.ico',
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_version(path):
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'APP_VERSION'
                for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError('APP_VERSION is missing from app_runtime.py.')


def validate(portable, baseline):
    details = json.loads((portable/'BUILD-DETAILS.json').read_text(encoding='utf-8'))
    previous = json.loads((baseline/'BUILD-DETAILS.json').read_text(encoding='utf-8'))
    if previous.get('source_version') not in BASE_VERSIONS:
        raise ValueError('This update requires a 1.14.1 or 1.14.2 portable baseline.')
    if details.get('source_version') != TARGET_VERSION:
        raise ValueError(f'The complete build must be version {TARGET_VERSION}.')
    if source_version(portable/'app/app_runtime.py') != TARGET_VERSION:
        raise ValueError('Application version does not match the build manifest.')
    for key in ('python_archive', 'wheels'):
        if not details.get(key) or details[key] != previous.get(key):
            raise ValueError(f'Runtime {key} differs; distribute the full portable build instead.')
    for folder in (portable, baseline):
        if not (folder/'runtime/python.exe').is_file():
            raise ValueError('Both inputs must be complete, assembled Windows portable folders.')
    for name in REQUIRED_FILES:
        if not (portable/name).is_file():
            raise ValueError(f'Complete build is missing {name}.')
    for name in REQUIRED_APP_FILES:
        if not (portable/'app'/name).is_file():
            raise ValueError(f'Complete build is missing app/{name}.')
    actual_sources = {p.relative_to(portable/'app').as_posix(): digest(p)
                      for p in sorted((portable/'app').rglob('*.py'))}
    if actual_sources != details.get('source_files'):
        raise ValueError('Application source differs from BUILD-DETAILS; rebuild the full package first.')
    return details, previous


def build(portable, baseline, output, compatible_baselines=()):
    portable, baseline, output = (Path(p).resolve() for p in (portable, baseline, output))
    archive_path = output.with_name(output.name+'.zip')
    if output.exists() or archive_path.exists():
        raise FileExistsError('Choose a new output path; existing packages are never overwritten.')
    if output.is_relative_to(portable) or output.is_relative_to(baseline):
        raise ValueError('Place the update outside the complete portable input folders.')
    details, previous = validate(portable, baseline)
    verified = {previous['source_version']: digest(baseline/'BUILD-DETAILS.json')}
    for extra in compatible_baselines:
        extra = Path(extra).resolve()
        _, other = validate(portable, extra)
        verified[other['source_version']] = digest(extra/'BUILD-DETAILS.json')
    output.mkdir(parents=True)
    shutil.copytree(portable/'app', output/'app',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store', '*.log'))
    for name in REQUIRED_FILES:
        shutil.copy2(portable/name, output/name)
    shutil.copy2(ROOT/'packaging/README-Update-Windows.txt', output/'UPDATE - START HERE.txt')
    update = dict(source_version=TARGET_VERSION, required_baseline_version=previous['source_version'],
                  compatible_baseline_versions=sorted(verified), baseline_manifests_sha256=verified,
                  runtime_included=False,
                  runtime_compatibility='Embedded Python archive and every wheel filename/hash match the baseline.',
                  python_archive=details['python_archive'], wheels=details['wheels'],
                  baseline_manifest_sha256=digest(baseline/'BUILD-DETAILS.json'),
                  application_manifest_sha256=digest(portable/'BUILD-DETAILS.json'),
                  validation='Package contents checked on macOS; run Check Installation.cmd on Windows.')
    update['files'] = {p.relative_to(output).as_posix(): digest(p)
                       for p in sorted(output.rglob('*')) if p.is_file()}
    (output/'UPDATE-DETAILS.json').write_text(json.dumps(update, indent=2)+'\n', encoding='utf-8')
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                archive.write(path, (Path(output.name)/path.relative_to(output)).as_posix())
    print(f'Created {archive_path.name}: {len(update["files"])+1} files; runtime verified against '+', '.join(sorted(verified))+'.')
    return archive_path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--portable', type=Path, required=True,
                        help='Complete assembled Windows 1.14.3 folder')
    parser.add_argument('--baseline', type=Path, required=True,
                        help='Complete assembled Windows 1.14.1 or 1.14.2 folder')
    parser.add_argument('--compatible-baseline', type=Path, action='append', default=[],
                        help='Additional assembled baseline whose runtime must also match; repeatable')
    parser.add_argument('--output', type=Path, required=True,
                        help='New update folder; also creates a ZIP beside it')
    args = parser.parse_args()
    build(args.portable, args.baseline, args.output, args.compatible_baseline)
