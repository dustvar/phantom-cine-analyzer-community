"""Recover the platform-independent PCA packages from an authorized installation.

Run with its Python. No credentials, installed bytecode, native binaries or user
configuration are copied. The destination is build input, not repository source.
"""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil

PACKAGES = ('pyphantom-cine', 'pyphantom-config', 'pyphantom-image',
            'pyphantom-ipc', 'pyphantom-qt', 'pyphantom-utils', 'cinereader')


def collect(destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name in PACKAGES:
        distribution = importlib.metadata.distribution(name)
        if 'Root-Is-Purelib: true' not in (distribution.read_text('WHEEL') or ''):
            raise ValueError(f'{name} is not a portable Python package')
        records = {}
        for item in distribution.files or ():
            relative = Path(str(item))
            if relative.is_absolute() or '..' in relative.parts:
                continue
            if relative.name in ('direct_url.json', 'INSTALLER', 'REQUESTED', 'RECORD'):
                continue
            if '__pycache__' in relative.parts or relative.suffix == '.pyc':
                continue
            if relative.suffix.lower() in ('.so', '.dylib', '.pyd', '.dll', '.exe'):
                raise ValueError(f'Unexpected native file in {name}: {relative}')
            source = Path(distribution.locate_file(item))
            if source.is_file():
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                records[str(relative)] = hashlib.sha256(target.read_bytes()).hexdigest()
        manifest[name] = {'version': distribution.version, 'files': records}
    (destination/'vendor-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    print(f'Collected {len(manifest)} pure Python packages in {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    collect(parser.parse_args().destination)
