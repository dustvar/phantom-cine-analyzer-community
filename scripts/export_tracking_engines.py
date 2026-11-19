"""Export the two portable engines and their handoff material; no PCA GUI/Classic."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / 'app-source/modules/trackmeasure'


def export(destination):
    destination = destination.resolve()
    if destination.exists():
        raise FileExistsError(f'Choose a new handoff directory: {destination}')
    destination.mkdir(parents=True)
    for source in (ROOT/'standalone_tracking').iterdir():
        if source.is_file(): shutil.copy2(source, destination/source.name)
    names = ('edge_tracking.py', 'tumbling_tracker.py')
    manifest = {'engines': {}, 'dependencies': {}}
    for name in names:
        shutil.copy2(MODULES/name, destination/name)
        manifest['engines'][name] = hashlib.sha256((MODULES/name).read_bytes()).hexdigest()
    shutil.copy2(ROOT/'LICENSE', destination/'PROJECT_ORIGIN_LICENSE')
    shutil.copy2(ROOT/'docs/OBJECT_TRACKING.md', destination/'TECHNICAL.md')
    test_dir = destination/'tests'; test_dir.mkdir()
    for name in ('test_portable_tracking.py', 'test_tumbling_tracker.py'):
        # The export root is importable when tests run here. A nonexistent PCA
        # path from the original regression test is harmless, but omit it.
        source = (ROOT/'tests'/name).read_text()
        source = source.replace("sys.path.insert(0,str(ROOT/'app-source/modules/trackmeasure'))", "sys.path.insert(0,str(ROOT))")
        source = source.replace("ROOT/'app-source/modules/trackmeasure'/name", "ROOT/name")
        source = source.replace("sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))", "sys.path.insert(0,str(Path(__file__).resolve().parents[1]))")
        (test_dir/name).write_text(source, encoding='utf-8')
    for package in ('numpy', 'opencv-contrib-python'):
        distribution = importlib.metadata.distribution(package)
        manifest['dependencies'][package] = distribution.version
        for item in distribution.files or ():
            if 'dist-info' in str(item) and 'license' in str(item).lower():
                source = Path(distribution.locate_file(item))
                if source.is_file():
                    folder = destination/'licenses'/package; folder.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, folder/source.name)
    (destination/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(export(parser.parse_args().destination))
