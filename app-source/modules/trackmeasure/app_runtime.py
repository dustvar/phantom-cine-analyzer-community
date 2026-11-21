"""Writable application state, shared by source and self-contained desktop builds."""
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import shutil
import sys
import time

APP_VERSION = '1.14.16'
APP_NAME = 'Phantom Cine Analyzer Additional Features'


def user_data_dir():
    override = os.environ.get('PCA_USER_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/PhantomCineAnalyzer'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local'))/'PhantomCineAnalyzer'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home()/'.local/share'))/'PhantomCineAnalyzer'


def resource_dir():
    return Path(__file__).resolve().parent


def settings_path(seed=None):
    """Copy existing settings once; never write inside a signed/read-only app."""
    directory = user_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory/'settings.json'
    if target.exists():
        try:
            if not isinstance(json.loads(target.read_text(encoding='utf-8')), dict):
                raise ValueError('Settings must be an object')
            return str(target)
        except (ValueError, OSError):
            backup = target.with_name(f'settings.invalid-{time.time_ns()}.json')
            shutil.copy2(target, backup)
            logging.warning('Invalid settings retained at %s; restoring defaults', backup)
    source = Path(seed) if seed is not None else resource_dir()/'config.json'
    values = json.loads(source.read_text(encoding='utf-8'))
    if not isinstance(values, dict):
        raise ValueError('Invalid default configuration')
    temporary = target.with_suffix(f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(values, indent=4)+'\n', encoding='utf-8')
    os.replace(temporary, target)
    return str(target)


def configure_runtime():
    directory = user_data_dir()
    for name in ('logs', 'cache', 'cache/matplotlib', 'cache/numba'):
        (directory/name).mkdir(parents=True, exist_ok=True)
    # PyInstaller's Matplotlib hook sets a temporary MPLCONFIGDIR before our
    # entry point. Replace it so font discovery is cached across app launches.
    os.environ['MPLCONFIGDIR'] = str(directory/'cache/matplotlib')
    os.environ['NUMBA_CACHE_DIR'] = str(directory/'cache/numba')
    # Python libraries must never mutate the signed application resources.
    sys.dont_write_bytecode = True
    log = directory/'logs/startup.log'
    logger = logging.getLogger()
    if not any(getattr(h, '_pca_startup', False) for h in logger.handlers):
        handler = RotatingFileHandler(log, maxBytes=2_000_000, backupCount=3, encoding='utf-8')
        handler._pca_startup = True
        handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return log


def launch_config(arguments):
    """Accept Finder/Explorer documents and the legacy launcher's JSON config."""
    args = [str(a) for a in arguments if not str(a).startswith('-psn_')]
    if not args:
        return {}
    if len(args) == 1 and args[0].lower().endswith('.pcajob'):
        return {'job_path': str(Path(args[0]).resolve())}
    if all(a.lower().endswith('.cine') for a in args):
        if len(args) > 4:
            raise ValueError('Open up to four Cine files at a time.')
        return {'cine_paths': [str(Path(a).resolve()) for a in args]}
    if len(args) == 1 and args[0].lower().endswith('.json'):
        config = json.loads(Path(args[0]).read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('The launch configuration must be a JSON object.')
        return config
    raise ValueError('Open Cine files, a .pcajob job, or a launcher .json configuration.')
