"""Read Phantom raw Ethernet image packets on a directly attached Mac Ethernet port."""

from collections import deque
from concurrent.futures import CancelledError
from dataclasses import dataclass
import glob
import ipaddress
import os
from pathlib import Path
import re
import select
import shlex
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time


class ReceiverError(OSError):
    """The Mac capture interface could not deliver a usable Ethernet stream."""


@dataclass(frozen=True)
class CameraLink:
    interface: str
    source: str  # camera MAC, compact hexadecimal
    dest: str    # local adapter MAC


def _system_info(*args):
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.DEVNULL, timeout=2)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReceiverError('The Mac could not resolve the camera Ethernet link.') from exc


def _mac(value):
    fields = ([value[i:i+2] for i in range(0, 12, 2)]
              if re.fullmatch(r'[0-9a-fA-F]{12}', value) else value.split(':'))
    if len(fields) != 6 or any(not re.fullmatch(r'[0-9a-fA-F]{1,2}', x) for x in fields):
        raise ReceiverError('The wired adapter has no usable Ethernet address.')
    addr = bytes(int(x, 16) for x in fields)
    if not any(addr) or addr[0] & 1 or addr.hex() == '020000000000':
        raise ReceiverError('Expected an individual camera or adapter Ethernet address.')
    return addr.hex()


def resolve_link(host, camera_mac=None):
    if sys.platform != 'darwin':
        raise ReceiverError('Raw Ethernet image transfer requires a Mac Ethernet adapter.')
    address = str(ipaddress.IPv4Address(host))
    route = _system_info('/sbin/route', '-n', 'get', address)
    iface = re.search(r'^\s*interface:\s*(en\d+)\s*$', route, re.M)
    flags = re.search(r'^\s*flags:\s*<([^>]*)>', route, re.M | re.I)
    if not iface or not flags or 'GATEWAY' in flags[1].upper().split(','):
        raise ReceiverError('The camera must be connected directly to a wired Ethernet adapter.')
    iface = iface[1]
    adapter = _system_info('/sbin/ifconfig', iface)
    local = re.search(r'^\s*ether\s+(\S+)', adapter, re.M)
    media = re.search(r'^\s*media:.*?\b(\d+(?:\.\d+)?)([GM]?)base[-\w]+', adapter, re.I | re.M)
    active = re.search(r'^\s*status:\s*active\s*$', adapter, re.I | re.M)
    speed_mbps = float(media[1]) * (1000 if media[2].upper() == 'G' else 1) if media else 0
    if not active or speed_mbps < 10000:
        raise ReceiverError('Camera ximg requires an active 10 Gigabit or faster Ethernet link.')
    # GUI apps can see a privacy placeholder from ifconfig even on a wired NIC.
    # Ask for the one routed interface by name and check the utility echoed it.
    try:
        dest = _mac(local[1] if local else '')
    except ReceiverError:
        output = _system_info('/usr/sbin/networksetup', '-getmacaddress', iface)
        hardware = re.fullmatch(r'\s*Ethernet Address:\s*([0-9a-fA-F:]+)\s*'
                                r'\(Device:\s*' + re.escape(iface) + r'\)\s*', output, re.I)
        if not hardware:
            raise ReceiverError('Could not identify the selected Ethernet adapter address.')
        dest = _mac(hardware[1])
    # Finder-launched Mac applications may see an empty neighbor table even
    # with a working camera connection. PH16 can supply the MAC for the exact
    # camera IP from its own interface list; the Mac still verifies the route.
    if camera_mac is None:
        neighbor = _system_info('/usr/sbin/arp', '-n', address)
        peer = re.search(r'\(' + re.escape(address) + r'\) at ([\da-f:]+) on '
                         + re.escape(iface) + r'\b', neighbor, re.I)
        if not peer:
            raise ReceiverError('The Ethernet address of the directly attached camera is unavailable.')
        camera_mac = peer[1]
    source = _mac(camera_mac)
    if source == dest:
        raise ReceiverError('The camera and the adapter must have different Ethernet addresses.')
    return CameraLink(iface, source, dest)


def can_receive(host, camera_mac=None):
    try:
        resolve_link(host, camera_mac)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _tcpdump_args(link):
    if not re.fullmatch(r'en\d+', link.interface):
        raise ValueError('Unexpected Ethernet interface name')
    macs = []
    for value in (link.source, link.dest):
        if not re.fullmatch(r'[0-9a-f]{12}', value):
            raise ValueError('Invalid Ethernet address')
        macs.append(':'.join(value[i:i+2] for i in range(0, 12, 2)))
    filt = f'ether src {macs[0]} and ether dst {macs[1]} and ether proto 0x88b7'
    return ['/usr/sbin/tcpdump', '-n', '-p', '-Q', 'in', '-y', 'EN10MB',
            '--immediate-mode', '-U', '-B', '512', '-s', '65535', '-i', link.interface,
            '-w', '-', filt]


def _capture_command(link, control_path, uid):
    # Only Apple executables run with authorization. Root owns the unique directory
    # and its entries throughout; the app user can only read the named pipe.
    # nc exits on control socket EOF even with no camera packets. The shell then
    # stops its own tcpdump child. No reusable BPF permission or process remains.
    args = _tcpdump_args(link)
    args[1:1] = ['-Z', 'nobody']
    return '\n'.join([
        'set -eu', 'umask 077',
        'd=$(/usr/bin/mktemp -d /private/tmp/pca-ximg.XXXXXXXXXX)',
        'cap=',
        'cleanup() { if [ -n "$cap" ] && [ "$(/bin/ps -o ppid= -p "$cap" | /usr/bin/tr -d " ")" = "$$" ]; then kill -KILL "$cap" 2>/dev/null || :; fi; /bin/rm -f "$d/frames" "$d/status"; /bin/rmdir "$d"; }',
        'trap cleanup EXIT', "trap 'exit 1' HUP INT TERM",
        '/usr/bin/mkfifo "$d/frames"', ': > "$d/status"',
        f'/usr/sbin/chown {int(uid)} "$d/status" "$d/frames"',
        '/bin/chmod 400 "$d/status" "$d/frames"', '/bin/chmod 711 "$d"',
        shlex.join(args) + ' 2>"$d/status" >"$d/frames" & cap=$!',
        'printf "%s\n" "$d/frames" | ' +
        shlex.join(['/usr/bin/sudo', '-n', '-u', f'#{int(uid)}', '--', '/usr/bin/nc',
                    '-U', str(control_path)]) + ' >/dev/null',
    ])


class PacketReceiver:
    """A bounded in-memory pcap reader, owned by a single camera scan."""

    def __init__(self, host, cancelled, camera_mac=None):
        self.cancelled = cancelled
        self._closed = threading.Event()
        self._cv = threading.Condition()
        self._chunks = deque()
        self._queued = 0
        self._error = None
        self._buffer = bytearray()
        self._offset = 0
        self._packet_header = None
        self._process = self._stream = self._log = self._socket = self._reader = None
        self._tmp = None
        try:
            link = resolve_link(host, camera_mac)
            self.source, self.dest = link.source, link.dest
            self._check()
            user_bpf = any(os.access(dev, os.R_OK) for dev in glob.glob('/dev/bpf[0-9]*'))
            if user_bpf:
                self._log = tempfile.TemporaryFile()
                self._process = subprocess.Popen(_tcpdump_args(link), stdout=subprocess.PIPE,
                    stderr=self._log, stdin=subprocess.DEVNULL, bufsize=0)
                self._stream = self._process.stdout
            else:
                self._start_authorized(link)
            self._await_capture()
            self._reader = threading.Thread(target=self._pump, name='PCA camera packets', daemon=True)
            self._reader.start()
        except BaseException:
            self.close()
            raise

    def _check(self):
        if self.cancelled() or self._closed.is_set():
            raise CancelledError()

    def _start_authorized(self, link):
        self._tmp = tempfile.TemporaryDirectory(prefix='pca-wire-', dir='/private/tmp')
        local_path = str(Path(self._tmp.name) / 'lifetime.sock')
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(local_path)
            server.listen(1)
            script = ('on run argv\n'
                      'do shell script (item 1 of argv) with administrator privileges '
                      'with prompt "Allow PCA to receive high-speed images from the selected '
                      'Phantom camera on its wired Ethernet adapter."\nend run')
            self._process = subprocess.Popen(['/usr/bin/osascript', '-e', script,
                    _capture_command(link, local_path, os.getuid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            deadline = time.monotonic() + 120
            while not select.select([server], [], [], .1)[0]:
                self._check()
                if self._process.poll() is not None or time.monotonic() >= deadline:
                    raise ReceiverError('Mac permission to receive high-speed camera images was unavailable.')
            self._socket, _ = server.accept()
        self._socket.settimeout(.1)
        message = bytearray()
        deadline = time.monotonic()+5
        while b'\n' not in message and len(message) < 128:
            self._check()
            if time.monotonic() >= deadline:
                raise ReceiverError('The camera packet receiver did not start.')
            try:
                b = self._socket.recv(128)
            except socket.timeout:
                continue
            if not b:
                break
            message.extend(b)
        if not re.fullmatch(rb'/private/tmp/pca-ximg\.[A-Za-z0-9]{10}/frames\n', message):
            raise ReceiverError('The Mac camera receiver returned an unexpected connection.')
        fifo = Path(os.fsdecode(bytes(message[:-1])))
        directory = fifo.parent.stat()
        if directory.st_uid != 0 or directory.st_mode & 0o022:
            raise ReceiverError('The Mac camera receiver directory is unavailable.')
        fd = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        self._stream = os.fdopen(fd, 'rb', buffering=0)
        details = os.fstat(fd)
        if not stat.S_ISFIFO(details.st_mode) or details.st_uid != os.getuid():
            raise ReceiverError('The Mac camera receiver pipe is unavailable.')
        self._log = (fifo.parent/'status').open('rb')

    def _await_capture(self):
        # Wait for activation before issuing ximg: a successful control response
        # does not guarantee that macOS had already installed its packet filter.
        deadline = time.monotonic()+4
        while True:
            self._check()
            details = os.pread(self._log.fileno(), 4096, 0).lower()
            if b'listening on' in details:
                return
            if self._process.poll() is not None or time.monotonic() >= deadline:
                raise ReceiverError('The Mac could not start image capture on the selected Ethernet adapter.')
            time.sleep(.02)

    def _pump(self):
        try:
            fd = self._stream.fileno()
            while not self._closed.is_set():
                if not select.select([fd], [], [], .1)[0]:
                    continue
                try:
                    chunk = os.read(fd, 256 << 10)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise ReceiverError('The Mac camera packet stream closed.')
                with self._cv:
                    if self._queued + len(chunk) > 32 << 20:
                        raise ReceiverError('The Mac camera packet buffer filled.')
                    self._chunks.append(chunk)
                    self._queued += len(chunk)
                    self._cv.notify()
        except (OSError, ValueError) as exc:
            with self._cv:
                self._error = exc
                self._cv.notify_all()

    def _fill(self, length, deadline):
        if len(self._buffer) - self._offset >= length:
            return True
        del self._buffer[:self._offset]
        self._offset = 0
        with self._cv:
            while len(self._buffer) < length:
                self._check()
                if self._chunks:
                    chunk = self._chunks.popleft()
                    self._queued -= len(chunk)
                    self._buffer.extend(chunk)
                elif self._error:
                    raise ReceiverError(str(self._error))
                else:
                    left = deadline-time.monotonic()
                    if left <= 0:
                        return False
                    self._cv.wait(min(left,.1))
        return True

    def next_packet(self, timeout):
        self._check()
        deadline = time.monotonic()+max(0, timeout)
        if self._packet_header is None:
            if not self._fill(24, deadline):
                return None
            order = {b'\xd4\xc3\xb2\xa1':'<', b'\xa1\xb2\xc3\xd4':'>',
                     b'M<\xb2\xa1':'<', b'\xa1\xb2<M':'>'}.get(bytes(self._buffer[:4]))
            if not order or struct.unpack_from(order+'HH', self._buffer, 4) != (2,4) or struct.unpack_from(order+'I', self._buffer, 20)[0] != 1:
                raise ReceiverError('Expected plain Ethernet image packets from the wired adapter.')
            self._packet_header = struct.Struct(order+'IIII')
            self._offset = 24
        if not self._fill(16, deadline):
            return None
        _, _, captured, wire = self._packet_header.unpack_from(self._buffer, self._offset)
        if captured != wire or not 32 <= wire <= 65535:
            raise ReceiverError('An Ethernet image packet was truncated or malformed.')
        if not self._fill(16+wire, deadline):
            return None
        start = self._offset + 16
        self._offset = start+wire
        return bytes(self._buffer[start:self._offset])

    def close(self):
        self._closed.set()
        with self._cv:
            self._cv.notify_all()
        permission_pending = self._tmp is not None and self._socket is None
        if self._socket:
            self._socket.close()
            self._socket = None
        if self._reader:
            self._reader.join(.5)
        if self._stream:
            self._stream.close()
            self._stream = None
        if self._log:
            self._log.close()
            self._log = None
        if self._process:
            if self._process.poll() is None and (self._tmp is None or permission_pending):
                # When authorization is still pending, dismiss the requester. If
                # authorization races with cancellation, its control socket is
                # already gone and the supervisor immediately removes the capture.
                self._process.terminate()
            try:
                self._process.wait(timeout=.75)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=2)
            self._process = None
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None
