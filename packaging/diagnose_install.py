"""User-run Windows installation check; keeps a report beside startup logs."""
import importlib
import json
import platform
from pathlib import Path
import subprocess
import sys
import traceback
from app_runtime import configure_runtime, user_data_dir, APP_VERSION


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(errors='replace')
    configure_runtime()
    report = dict(version=APP_VERSION, platform=platform.platform(), machine=platform.machine(),
                  python=sys.version, modules={}, errors=[])
    for name in ('numpy', 'cv2', 'scipy', 'skimage', 'pandas', 'matplotlib', 'numba',
                 'PySide6', 'pyphantom_cine', 'pyphantom_image', 'pyphantom_config',
                 'pyphantom_qt', 'pyphantom_ipc.ipc_api', 'simplemeas_ui',
                 'playback_frames', 'bos_processing', 'bos_correlation', 'bos_display',
                 'bos_ui', 'cine_trim', 'cine_render', 'viewer_scrubber',
                 'viewer_track_fade', 'video_export'):
        try:
            module = importlib.import_module(name)
            report['modules'][name] = getattr(module, '__version__', 'Imported successfully')
            print('OK ', name)
        except Exception:
            report['errors'].append({'module': name, 'error': traceback.format_exc()})
            print('FAILED ', name)
    try:
        from PySide6.QtWidgets import QApplication, QWidget
        app = QApplication.instance() or QApplication([])
        window = QWidget();window.resize(100, 100);window.grab();window.close()
        report['qt_platform'] = app.platformName()
        import imageio_ffmpeg
        result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-version'], capture_output=True, text=True, timeout=15, check=True)
        report['ffmpeg'] = result.stdout.splitlines()[0]
    except Exception:
        report['errors'].append({'module':'GUI / video export', 'error':traceback.format_exc()})
    path = user_data_dir()/'logs/installation-check.json'
    path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('\n'+('PASS — runtime and GUI checks succeeded.' if not report['errors'] else 'Some checks failed.'))
    print('Report: '+str(path))
    print('Next: open PCA, load a Cine, and verify playback, tracking and export before a customer demo.')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
