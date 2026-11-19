"""Direct native startup, document opening and single-instance forwarding."""
import json
import logging
import os
from pathlib import Path
import signal
import sys
import time
import warnings
import hashlib
from PySide6.QtWidgets import QApplication, QSplashScreen, QMessageBox
from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from app_runtime import (APP_VERSION, APP_NAME, configure_runtime, launch_config,
                         resource_dir, settings_path, user_data_dir)

VERSION = APP_VERSION
app = None
Splash = None
main_window = None


def _clone_prepared_bos_cache(source):
    """Restore an opt-in Mac handoff without giving the player the archive itself.

    The preserved directory is read-only input. APFS clones keep a large result
    cheap to reopen; closing the new window deletes only its fresh private copy.
    """
    import shutil
    import subprocess
    import tempfile
    from bos_preview import CachedBOSClip, FORMAT, cache_root

    if sys.platform != 'darwin':
        raise ValueError('Prepared-result handoff is available on this Mac only.')
    source = Path(source).expanduser()
    if source.is_symlink() or not source.is_dir():
        raise ValueError('The preserved BOS result must be a regular directory.')
    names = ('manifest.json', 'index.npy', 'times.npy', 'frames.bin',
             'original.bin', 'original-index.npy', 'measured.npy', 'vectors.bin', 'vector-index.npy')
    for name in names:
        item = source/name
        if item.is_symlink() or (item.exists() and not item.is_file()):
            raise ValueError('The preserved BOS result contains an invalid file.')
    # Metadata/index validation does not own or remove the preserved directory.
    original = CachedBOSClip(source)
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    destination = Path(tempfile.mkdtemp(prefix='bos-', dir=root))
    try:
        for name in names:
            if (source/name).is_file():
                subprocess.run(['/bin/cp', '-c', str(source/name), str(destination/name)],
                               check=True, capture_output=True)
        (destination/'owner.json').write_text(json.dumps(dict(format=FORMAT, pid=os.getpid())))
        restored = CachedBOSClip(destination)
        if restored.count != original.count:
            raise ValueError('The prepared BOS result changed during restoration.')
        return restored
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _restore_prepared_bos(window, source, replay_fps=None, view_state=None):
    """One explicit startup handoff into the normal user session; no recompute."""
    from bos_preview_ui import BOSPreviewWindow
    from bos_studio_model import MAX_PLAYBACK_FPS
    from bos_compass import CompassPlacement

    clip = None
    try:
        if replay_fps is not None:
            import math
            replay_fps = float(replay_fps)
            if not math.isfinite(replay_fps) or not 1 <= replay_fps <= MAX_PLAYBACK_FPS:
                raise ValueError(f'The restored replay rate must be between 1 and {MAX_PLAYBACK_FPS:,} fps.')
        state = json.loads(view_state) if isinstance(view_state, str) else (view_state or {})
        if not isinstance(state, dict) or set(state)-{'position', 'compass'}:
            raise ValueError('The restored view state contains unknown fields.')
        position = state.get('position', 0)
        if type(position) is not int or position < 0:
            raise ValueError('The restored frame must be a non-negative whole index.')
        placement = None
        if state.get('compass') is not None:
            import math
            values = state['compass']
            if not isinstance(values, dict) or set(values) != {'x', 'y', 'size'}:
                raise ValueError('The restored compass needs x, y and size.')
            if not all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 1
                       for v in values.values()):
                raise ValueError('The restored compass uses normalized image coordinates.')
            placement = CompassPlacement(**values)
        clip = _clone_prepared_bos_cache(source)
        if position >= clip.count:
            raise ValueError('The restored frame is outside the analyzed result.')
        panel = window.bos_panel
        preview = BOSPreviewWindow(panel, clip)
        if replay_fps is not None:
            preview.fps.setValue(replay_fps)
        if 'compass' in state:
            preview.compass_button.setChecked(placement is not None)
            if placement is not None:
                preview.view.compass = preview.view._last_placement = placement
                preview.view.deselect_compass()
        preview.seek(position)
        windows = getattr(panel, '_bos_preview_windows', None)
        if windows is None:
            panel._bos_preview_windows = windows = []
        windows.append(preview)
        preview.destroyed.connect(lambda: windows.remove(preview) if preview in windows else None)
        preview.show()
        panel.status.setText(f'Restored {clip.count:,} analyzed frames without recalculating BOS.')
        logging.info('Restored prepared BOS result from %s into %s', source, clip.directory)
        return preview
    except Exception as error:
        if clip is not None:
            clip.close()
        logging.exception('Could not restore the prepared BOS result')
        QMessageBox.warning(window, 'Restore analyzed result',
                            'The preserved result is unchanged. ' + str(error))
        return None


class DesktopApplication(QApplication):
    def __init__(self, arguments):
        super().__init__(arguments)
        self.pending_documents = []
        self.document_handler = None
        self.local_server = None
        self.clients = set()

    def event(self, event):
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or event.url().toLocalFile()
            if path:
                self.deliver([path])
            return True
        return super().event(event)

    def deliver(self, documents):
        if self.document_handler is None:
            self.pending_documents.extend(documents)
        else:
            self.document_handler(documents)

    def become_primary(self, documents):
        # Separate user profiles/test sessions never forward to each other.
        name = 'phantom-analyzer-' + hashlib.sha256(str(user_data_dir()).encode()).hexdigest()[:24]
        client = QLocalSocket(self)
        client.connectToServer(name)
        if client.waitForConnected(500):
            client.write(json.dumps(documents).encode('utf-8')+b'\n')
            if not client.waitForBytesWritten(2000):
                raise RuntimeError('The running analyzer did not accept the request. Please close it and try again.')
            client.disconnectFromServer()
            return False
        # A refused/not-found endpoint is left behind after a crash.
        if client.error() not in (QLocalSocket.LocalSocketError.ServerNotFoundError,
                                  QLocalSocket.LocalSocketError.ConnectionRefusedError):
            raise RuntimeError('Unable to contact the running analyzer: '+client.errorString())
        QLocalServer.removeServer(name)
        self.local_server = QLocalServer(self)
        self.local_server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.local_server.listen(name):
            raise RuntimeError('Unable to start the local document service: '+self.local_server.errorString())
        self.local_server.newConnection.connect(self._connection)
        return True

    def _connection(self):
        while self.local_server.hasPendingConnections():
            client = self.local_server.nextPendingConnection()
            self.clients.add(client)
            data = bytearray()
            def consume(client=client, data=data):
                data.extend(bytes(client.readAll()))
                if len(data) > 65536:
                    client.abort()
                    return
                if b'\n' in data:
                    try:
                        documents = json.loads(bytes(data).split(b'\n', 1)[0])
                        if not isinstance(documents, list) or not all(isinstance(p, str) for p in documents):
                            raise ValueError('Invalid document request')
                        self.deliver(documents)
                    except Exception:
                        logging.exception('Could not handle document request')
                    client.disconnectFromServer()
            def finished(client=client):
                self.clients.discard(client)
                client.deleteLater()
            client.readyRead.connect(consume)
            client.disconnected.connect(finished)
            consume()


def launch():
    """Retain the legacy launcher entry point while opening the editor directly."""
    global main_window, app
    app = app or QApplication.instance() or DesktopApplication(sys.argv)
    configure_runtime()
    started = time.perf_counter()
    logging.info('Starting analyzer %s; Python %s; executable %s', VERSION, sys.version.split()[0], sys.executable)
    # Heavy scientific imports happen after a visible splash has appeared.
    import simplemeas_vm
    import simplemeas_ui
    import measmodel
    import reportmodel
    from cine_metadata import CineHandler
    from pyphantom_config.config import JsonConfig
    cfg = launch_config(sys.argv[1:])
    cm = JsonConfig(settings_path())
    main_window = simplemeas_ui.MainWindow()
    main_window.setObjectName('main_window')
    main_window.vm = simplemeas_vm.SimpleMeasVM(
        main_window, measmodel.MeasModel(), reportmodel.ReportHandler(), cm, CineHandler(), cfg)
    main_window.connect_events()
    main_window.initialize_theme()
    main_window.show()
    # Give the window a paint event before loading a large command-line Cine.
    def ready():
        try:
            main_window.vm.init_ui()
            if cfg.get('job_path'):
                main_window.request_open_job(cfg['job_path'])
            if isinstance(app, DesktopApplication):
                def open_documents(documents):
                    main_window.showNormal()
                    main_window.raise_()
                    main_window.activateWindow()
                    if not documents:
                        return
                    try:
                        incoming = launch_config(documents)
                        if incoming.get('job_path'):
                            main_window.request_open_job(incoming['job_path'])
                        elif incoming.get('cine_paths'):
                            main_window._choose_image(incoming['cine_paths'])
                    except Exception as error:
                        QMessageBox.warning(main_window, 'Open footage', str(error))
                app.document_handler = open_documents
                queued, app.pending_documents = app.pending_documents, []
                if queued:
                    open_documents(list(dict.fromkeys(queued)))
            logging.info('READY in %.2f seconds; %s', time.perf_counter()-started, main_window.cine_path)
            preserved_bos = os.environ.pop('PCA_RESTORE_BOS_CACHE', '')
            preserved_bos_rate = os.environ.pop('PCA_RESTORE_BOS_FPS', None)
            preserved_bos_view = os.environ.pop('PCA_RESTORE_BOS_VIEW', None)
            if preserved_bos:
                QTimer.singleShot(0, main_window,
                                  lambda: _restore_prepared_bos(main_window, preserved_bos,
                                                                preserved_bos_rate, preserved_bos_view))
            if os.environ.get('PCA_STARTUP_TEST_REPORT'):
                from startup_diagnostics import record_ready
                record_ready(main_window, started)
        except Exception as error:
            logging.exception('Could not open startup footage')
            QMessageBox.warning(main_window, 'Open footage', str(error))
        finally:
            if Splash is not None:
                Splash.finish(main_window)
    QTimer.singleShot(0, ready)
    def stop(signum, frame):
        main_window.close()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    return app.exec()


def main():
    global app, Splash
    configure_runtime()
    warnings.filterwarnings('ignore', category=DeprecationWarning)
    app = DesktopApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName('PhantomCineAnalyzer')
    if not app.become_primary(sys.argv[1:]):
        return 0
    path = resource_dir()
    app.setStyle('Fusion')
    app.setStyleSheet((path/'styles.css').read_text(encoding='utf-8'))
    app.setWindowIcon(QIcon(str(path/'images/icon.png')))
    image = QPixmap(str(path/'images/SplashPCA.png'))
    if not image.isNull():
        if image.width() > 650:
            image = image.scaledToWidth(650, Qt.TransformationMode.SmoothTransformation)
        Splash = QSplashScreen(image)
        Splash.showMessage('Opening Phantom Cine Analyzer…', Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, Qt.GlobalColor.white)
        Splash.show()
        app.processEvents()
    return launch()


if __name__ == '__main__':
    raise SystemExit(main())
