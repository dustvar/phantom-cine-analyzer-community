import sys, json, os, logging
import warnings
import signal
import atexit
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *

import simplemeas_vm
import measmodel
import reportmodel
from pyphantom_cine.cine_handler import CineHandler
from pyphantom_config.config import JsonConfig
from pyphantom_utils.logging_utils import init_app_log
import simplemeas_ui


# constants
MINIGRAPH_SIZE = 160
VERSION = '1.0.0'
dir_path = os.path.dirname(os.path.abspath(__file__))


def launch():
    global cine_handler_instance
    cine_handler_instance = None
    
    def cleanup_on_exit():
        if cine_handler_instance:
            cine_handler_instance.close()
            logging.info("Exit cleanup: cache cleared")
    
    def signal_handler(signum, frame):
        if cine_handler_instance:
            cine_handler_instance.close()
        sys.exit(0)
    
    atexit.register(cleanup_on_exit)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
        
    #region CUSTOM WIDGETS
    init_app_log('TrackMeasure', clear_log=True)
    logging.info(f'Track & Measure App v{VERSION} started')

    cfg = {}
    try:
        if len(sys.argv) > 1:
            with open(sys.argv[1], encoding='utf-8') as f:
                cfg = json.load(f)
                logging.info(f'Configuration file: {cfg}')
        else:
            logging.info('No configuration file provided, using defaults')
    except Exception as e:
        logging.error(f"Failed to load configuration file: {e}")

    mm = measmodel.MeasModel()
    cfg_path = os.path.join(dir_path, 'config.json')
    if not os.path.exists(cfg_path):
        logging.error(f"Configuration file not found: {cfg_path}")
    cm = JsonConfig(cfg_path)
    rh = reportmodel.ReportHandler()
    
    #cache
    cache_enabled = cm.get('cache_enabled', True)
    max_cache_ram_mb = cm.get('max_cache_ram_mb', 1024)
    ch = CineHandler()
    cine_handler_instance = ch
    logging.info(f"Image cache initialized: enabled={cache_enabled}, max_ram={max_cache_ram_mb}MB")

    
    if sys.platform == "darwin":
        icon_path = os.path.join(dir_path, "images", "icon.png")
    elif sys.platform == "win32":
        icon_path = os.path.join(dir_path, "images", "icon.ico")
    else:  # Linux
        icon_path = os.path.join(dir_path, "images", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    sty_path = os.path.join(dir_path, 'styles.css')
    with open(sty_path, 'r') as f:
        style = f.read()

    app.setStyle('Fusion')
    app.setStyleSheet(style)
    main_window = simplemeas_ui.MainWindow()
    main_window.setObjectName('main_window')

    vm = simplemeas_vm.SimpleMeasVM(main_window, mm, rh, cm, ch, cfg)
    main_window.vm = vm
    main_window.connect_events()
    main_window.initialize_theme()
    vm.init_ui()

    main_window.show()

    Splash.finish(main_window)
    sys.exit(app.exec())

if __name__ == '__main__':
    app = QApplication(sys.argv)
    splash_img = os.path.join(dir_path, 'images', 'SplashPCA.png')
    if os.path.exists(splash_img):
        Splash = QSplashScreen(QPixmap(splash_img))
        Splash.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        Splash.setWindowFlag(Qt.WindowCloseButtonHint, False)
        Splash.setWindowFlag(Qt.WindowMinMaxButtonsHint, False)
        Splash.show()
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", category=UserWarning)
    launch()
    
