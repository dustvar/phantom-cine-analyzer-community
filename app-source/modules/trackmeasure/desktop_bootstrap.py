"""Small desktop entry point; report failures even before the Qt UI can import."""
import logging
import os
from pathlib import Path
import sys
import traceback
from app_runtime import configure_runtime


def main():
    log = None
    try:
        log = configure_runtime()
        from launch_app import main as launch_main
        return launch_main()
    except Exception as error:
        details = traceback.format_exc()
        logging.critical('Startup failed\n%s', details)
        if os.environ.get('PCA_STARTUP_TEST_REPORT'):
            import json
            report = Path(os.environ['PCA_STARTUP_TEST_REPORT'])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps({'error': details}), encoding='utf-8')
            return 1
        message = f'Phantom Cine Analyzer could not start.\n\n{error}'
        if log:
            message += f'\n\nDiagnostic log:\n{log}'
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication([])
            QMessageBox.critical(None, 'Phantom Cine Analyzer — startup', message)
        except Exception:
            if sys.platform == 'win32':
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, 'Phantom Cine Analyzer — startup', 16)
            else:
                sys.stderr.write(message+'\n'+details)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
