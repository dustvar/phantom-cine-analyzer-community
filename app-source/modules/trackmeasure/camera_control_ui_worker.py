"""All actions on one camera are serialized on a dedicated worker thread."""
from concurrent.futures import CancelledError
from queue import Queue
from time import monotonic

from PySide6.QtCore import QThread, Signal

from camera_control import CameraControl
from camera_monitor import needs_shadow_precision


class CameraWorker(QThread):
    # Each reply is (command, result, error). Only a confirmed status enables writes.
    reply = Signal(str, object, str)

    def __init__(self, hosts, serial, parent):
        super().__init__(parent)
        self.hosts = hosts
        self.serial = serial
        self.requests = Queue()
        self.host = ''

    def submit(self, command, argument=None):
        self.requests.put((command, argument))

    def stop(self):
        self.requestInterruption()
        self.submit('close')

    def run(self):
        session = None
        shadow_detail = False
        try:
            for host in self.hosts:
                if self.isInterruptionRequested():
                    return
                try:
                    session = CameraControl(host, expected_serial=self.serial,
                                            cancelled=self.isInterruptionRequested)
                    first = session.status()
                    self.host = host
                    break
                except (ValueError, OSError, RuntimeError) as exc:
                    if session is not None:
                        session.close()
                        session = None
                    last_error = str(exc)
            if session is None:
                self.reply.emit('connect', None, last_error)
                return
            self.reply.emit('connect', first, '')
            while not self.isInterruptionRequested():
                command, argument = self.requests.get()
                if self.isInterruptionRequested() or command == 'close':
                    return
                try:
                    if command == 'apply':
                        result = session.apply(**argument)
                    elif command == 'csr':
                        result = session.csr()
                    elif command == 'arm':
                        result = session.arm(replace=argument) if argument is not None else session.arm()
                    elif command == 'trigger':
                        slot, deadline = argument
                        if monotonic() >= deadline:
                            raise ValueError('Trigger expired while waiting for the camera. No trigger was sent.')
                        result = session.trigger(slot, deadline=deadline)
                    elif command == 'preview':
                        result = session.preview(full_precision=shadow_detail)
                        shadow_detail = needs_shadow_precision(result, shadow_detail)
                    elif command == 'stop_preview':
                        result = session.stop_preview()
                    elif command == 'status':
                        result = session.status()
                    else:
                        raise ValueError('Unknown camera action.')
                except (ValueError, OSError, RuntimeError) as exc:
                    if self.isInterruptionRequested():
                        return
                    message = str(exc) or 'The camera could not complete this action.'
                    if command in ('apply', 'arm', 'trigger', 'csr'):
                        # A command may have reached the camera before a lost reply.
                        # Never resend it. Get current state before re-enabling controls.
                        try:
                            current = session.status()
                        except (ValueError, OSError, RuntimeError) as status_error:
                            self.reply.emit(command, None, f'{message}  {status_error}')
                            return
                        self.reply.emit(command, current, message)
                    else:
                        self.reply.emit(command, None, message)
                        if command == 'status':
                            return
                else:
                    self.reply.emit(command, result, '')
        except CancelledError:
            pass
        finally:
            if session is not None:
                session.close()
