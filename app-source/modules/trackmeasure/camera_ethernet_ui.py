"""Inspect the wired connection and compare its IPv4 subnet with a camera label."""
from ipaddress import IPv4Address, IPv4Network
import re
import socket
import sys

import psutil
from PySide6.QtCore import QProcess, QSignalBlocker, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QToolButton,
                               QVBoxLayout, QWidget)

from event_camera_discovery import wired_interfaces

FACTORY_SUBNETS = (IPv4Network('100.100.0.0/16'), IPv4Network('172.16.0.0/16'))


def alias_arguments(device, text, remove=False):
    # Only the two conventional camera networks are configured here. macOS
    # handles custom masks and persistent profiles through its Network settings.
    address = IPv4Address(text.strip())
    if (not re.fullmatch(r'en[0-9]{1,4}', device)
            or not any(address in net and address not in (net.broadcast_address, net.network_address)
                       for net in FACTORY_SUBNETS)):
        raise ValueError('Enter an unused host address in 100.100.x.x or 172.16.x.x.')
    action = '-alias' if remove else 'netmask 255.255.0.0 alias'
    # At this point the device is a BSD interface name and the address is a
    # parsed IPv4Address; no free-form text is interpreted by the shell.
    wire = f'/sbin/ifconfig {device} inet {address} {action}'
    verb = 'Remove' if remove else 'Add'
    prompt = f'{verb} the temporary Phantom address {address} on wired adapter {device}.'
    return ['-e', f'do shell script "{wire}" with administrator privileges with prompt "{prompt}"']

def subnet_hint(text, addresses):
    if not text.strip():
        return 'Enter the camera’s printed IP or XIP to check its subnet.'
    try:
        ip = IPv4Address(text.strip())
    except ValueError:
        return 'Enter all four numbers from the camera’s IPv4 address.'
    if any(ip == address.ip for address in addresses):
        return 'The camera and adapter must have different IPv4 addresses.'
    if ip.is_loopback or ip.is_multicast or ip.is_unspecified or int(ip) == 0xffffffff:
        return 'Enter the individual camera’s IPv4 address.'
    for address in addresses:
        if ip in address.network:
            if ip in (address.network.network_address, address.network.broadcast_address):
                return 'This is a network or broadcast address. Enter the camera’s own address.'
            return f'The camera address is on this adapter’s subnet ({address.network}).'
    return ('Different subnet. In network settings, give the wired adapter an unused '
            'address on the camera’s subnet, using the camera’s subnet mask.')


def mac_service_names(output):
    names = {}
    service = ''
    for line in output.splitlines():
        heading = re.match(r'^\(\d+\)\s+(.+)$', line)
        if heading:
            service = heading.group(1).strip()
            continue
        device = re.search(r', Device:\s*(en\d+)\)', line)
        if device and service:
            names[device.group(1)] = service
    return names


def mac_maximum_speed(output):
    speeds = []
    for rate, unit in re.findall(r'^\s*(\d+(?:\.\d+)?)(g?)base', output, re.MULTILINE | re.IGNORECASE):
        speeds.append(int(float(rate) * (1000 if unit else 1)))
    return max(speeds, default=0)


def speed_text(mbps):
    if mbps >= 1000:
        return f'{mbps / 1000:g} Gb/s'
    return f'{mbps:g} Mb/s' if mbps > 0 else 'Unknown'


def routed_address(host):
    if host:
        try:
            # UDP connect only asks the local routing table for the source IP.
            # No payload is sent and no camera session is opened.
            ip = str(IPv4Address(host))
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
                route.connect((ip, 7115))
                return IPv4Address(route.getsockname()[0])
        except (ValueError, OSError):
            pass
    return None


def camera_adapter(host, adapters, local_ip):
    if local_ip is not None:
        return next((device for device, (_, addrs) in adapters.items()
                     if any(a.ip == local_ip for a in addrs)), '')
    try:
        ip = IPv4Address(host)
    except ValueError:
        return ''
    candidates = [device for device, (_, addrs) in adapters.items() if any(ip in a.network for a in addrs)]
    return candidates[0] if len(candidates) == 1 else ''


class CameraEthernetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Camera connection')
        self.setMinimumWidth(418)
        self.setMaximumWidth(700)
        self.host = self.model = self.serial = self.camera_details = self.memory_details = ''
        self.active_device = ''
        self.follow_camera = True
        self.service_names = {}
        self.maximums = {}
        self.probes = {}
        self.adapters = {}
        self._content = None
        self.owned = {}
        self.alias_process = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(8)
        self.identity = QLabel('No camera connected')
        self.identity.setTextFormat(Qt.PlainText)
        self.identity.setWordWrap(True)
        self.identity.setStyleSheet('font-size: 18px; font-weight: bold;')
        layout.addWidget(self.identity)
        self.metadata = QLabel()
        self.metadata.setTextFormat(Qt.PlainText)
        self.metadata.setWordWrap(True)
        self.metadata.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.metadata)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.interface = QComboBox()
        self.interface.setMinimumContentsLength(12)
        self.interface.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        form.addRow('&Adapter', self.interface)
        self.link = QLabel()
        form.addRow('Link speed', self.link)
        self.maximum = QLabel()
        form.addRow('Adapter capability', self.maximum)
        self.summary_form = form
        layout.addLayout(form)
        self.guidance = QLabel()
        self.guidance.setTextFormat(Qt.PlainText)
        self.guidance.setWordWrap(True)
        layout.addWidget(self.guidance)

        self.advanced = QToolButton()
        self.advanced.setText('Network details')
        self.advanced.setCheckable(True)
        self.advanced.setArrowType(Qt.RightArrow)
        self.advanced.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        layout.addWidget(self.advanced, 0, Qt.AlignLeft)
        self.details = QWidget()
        detail_layout = QVBoxLayout(self.details)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        addresses = QFormLayout()
        addresses.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.ipv4 = QLabel()
        self.ipv4.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.memory = QLabel()
        self.memory.setTextFormat(Qt.PlainText)
        self.memory.setWordWrap(True)
        self.memory.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.memory)
        self.memory.hide()
        self.camera_ip = QLineEdit()
        self.camera_ip.setPlaceholderText('IP or XIP from camera label')
        addresses.addRow('Adapter addresses', self.ipv4)
        addresses.addRow('&Camera address', self.camera_ip)
        detail_layout.addLayout(addresses)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.PlainText)
        self.note.setAccessibleName('Camera subnet check')
        detail_layout.addWidget(self.note)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        self.settings_url = {'darwin': 'x-apple.systempreferences:com.apple.Network-Settings.extension',
                             'win32': 'ms-settings:network-ethernet'}.get(sys.platform)
        if self.settings_url:
            settings = QPushButton('System network settings…')
            settings.setAutoDefault(False)
            settings.clicked.connect(self._open_settings)
            actions.addWidget(settings)
        self.temporary = None
        if sys.platform == 'darwin':
            self.temporary = QPushButton()
            self.temporary.setAutoDefault(False)
            self.temporary.clicked.connect(self._edit_alias)
            actions.addWidget(self.temporary)
        actions.addStretch()
        detail_layout.addLayout(actions)
        self.details.hide()
        layout.addWidget(self.details)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.advanced.toggled.connect(self._toggle_details)
        self.interface.activated.connect(self._select_device)
        self.interface.currentIndexChanged.connect(self._update_details)
        self.camera_ip.textChanged.connect(self._update_details)
        self.poll = QTimer(self, interval=2000)
        self.poll.timeout.connect(self._refresh)

    def _toggle_details(self, expanded):
        self.advanced.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.details.setVisible(expanded)
        self._fit_height()

    def set_camera(self, host, model='', serial='', details='', memory_details=''):
        values = (host or '', model or '', serial or '', details or '', memory_details or '')
        if values == (self.host, self.model, self.serial, self.camera_details, self.memory_details):
            return
        if self.host != values[0]:
            self.follow_camera = True
            self.camera_ip.setText(values[0])
        self.host, self.model, self.serial, self.camera_details, self.memory_details = values
        self.memory.setText(self.memory_details)
        self.memory.setVisible(bool(self.memory_details))
        self.identity.setText(self.model or ('Connected Phantom camera' if self.host else 'No camera connected'))
        meta = [s for s in (self.camera_details, f'S/N {self.serial}' if self.serial else '') if s]
        self.metadata.setText('  ·  '.join(meta))
        self.metadata.setVisible(bool(meta))
        if self.isVisible():
            self._refresh()

    def _select_device(self):
        self.follow_camera = self.interface.currentData() == self.active_device
        self._update_details()

    def _mac_probe(self, key, args, callback):
        if key in self.probes:
            return
        job = QProcess(self)
        self.probes[key] = job
        def finished():
            if self.probes.pop(key, None) is not job:
                return
            output = bytes(job.readAllStandardOutput()).decode('utf-8', 'replace')
            job.deleteLater()
            callback(output)
        job.finished.connect(finished)
        job.errorOccurred.connect(lambda error: finished() if error == QProcess.FailedToStart else None)
        job.start('/usr/sbin/networksetup', args)

    def _got_names(self, output):
        self.service_names = mac_service_names(output)
        self._refresh()

    def _got_media(self, device, output):
        self.maximums[device] = mac_maximum_speed(output)
        self._update_details()

    def _open_settings(self):
        if not QDesktopServices.openUrl(QUrl(self.settings_url)):
            self.note.setText('Open your computer’s network settings and select the wired adapter.')

    def _edit_alias(self):
        if self.alias_process is not None:
            return
        self._refresh()
        name = self.interface.currentText()
        device = self.interface.currentData()
        if not device:
            return
        _, addresses = self.adapters[device]
        removing = device in self.owned
        if removing:
            address = self.owned[device]
            question = f'Remove {address} from {name}? This may disconnect the camera.'
            if QMessageBox.question(self, 'Remove temporary address', question) != QMessageBox.Yes:
                return
        else:
            try:
                camera = IPv4Address(self.camera_ip.text().strip())
            except ValueError:
                camera = None
            network = next((net for net in FACTORY_SUBNETS if camera in net), None) if camera else None
            if network is None:
                network = next((net for net in FACTORY_SUBNETS
                                if not any(entry.ip in net for entry in addresses)), FACTORY_SUBNETS[0])
            host_number = int(addresses[0].ip) % 65536 if addresses else 25607
            suggested = network.network_address + host_number
            if suggested in (camera, network.network_address, network.broadcast_address):
                suggested = network.network_address + 25607
                if suggested == camera:
                    suggested += 1
            address, accepted = QInputDialog.getText(
                self, 'Add temporary address',
                f'Unused IPv4 address for {name} (/16). Your saved profile stays in place.\n'
                'Use a dedicated camera link. The address lasts until removed or the adapter resets.',
                text=str(suggested))
            if not accepted:
                return
        try:
            args = alias_arguments(device, address, removing)
            ip = IPv4Address(address.strip())
            if not removing:
                if ip == camera:
                    raise ValueError('Give the adapter a different address from the camera.')
                subnet = IPv4Network((ip, 16), strict=False)
                if any(IPv4Address(host.toIPv4Address()) in subnet for host in QNetworkInterface.allAddresses()):
                    raise ValueError(f'An interface on this Mac already uses {subnet}. Choose another subnet or review network settings.')
        except ValueError as exc:
            QMessageBox.information(self, 'Temporary address', str(exc))
            return
        process = self.alias_process = QProcess(self)
        process.setProgram('/usr/bin/osascript')
        process.setArguments(args)
        self.temporary.setEnabled(False)

        def finished(ok):
            if self.alias_process is not process:
                return
            self.alias_process = None
            if ok:
                if removing:
                    self.owned.pop(device, None)
                else:
                    self.owned[device] = str(ip)
            else:
                QMessageBox.information(self, 'Temporary address',
                                        'macOS did not complete the address change. Check the adapter and administrator approval.')
            process.deleteLater()
            self._refresh()
        process.finished.connect(lambda code, status: finished(code == 0 and status == QProcess.NormalExit))
        process.errorOccurred.connect(lambda error: finished(False) if error == QProcess.FailedToStart else None)
        process.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.follow_camera = True
        self.metadata.setVisible(bool(self.metadata.text()))
        self.maximums.clear()
        if sys.platform == 'darwin':
            self._mac_probe('names', ['-listnetworkserviceorder'], self._got_names)
        self._refresh()
        self.poll.start()

    def hideEvent(self, event):
        self.poll.stop()
        super().hideEvent(event)

    def _refresh(self):
        try:
            stats = psutil.net_if_stats()
        except (OSError, psutil.Error):
            stats = {}
        found = {}
        choices = []
        for adapter, addresses in wired_interfaces():
            device = adapter.name()
            alias = adapter.humanReadableName() or device
            label = self.service_names.get(device) or alias
            status = stats.get(alias) or stats.get(device)
            speed = status.speed if status and status.isup else 0
            found[device] = (speed, addresses)
            choices.append((label, device))
        self.adapters = found
        self.owned = {device: ip for device, ip in self.owned.items() if
                      any(str(a.ip) == ip for a in found.get(device, (0, ()))[1])}
        self.active_device = camera_adapter(self.host, found, routed_address(self.host))
        selected = self.interface.currentData()
        if self.follow_camera and self.active_device:
            selected = self.active_device
        with QSignalBlocker(self.interface):
            if choices != [(self.interface.itemText(i), self.interface.itemData(i)) for i in range(self.interface.count())]:
                self.interface.clear()
                for label, device in choices:
                    self.interface.addItem(label, device)
            if selected in found:
                self.interface.setCurrentIndex(self.interface.findData(selected))
        self._update_details()

    def _update_details(self):
        device = self.interface.currentData() or ''
        speed, addresses = self.adapters.get(device, (0, ()))
        self.interface.setToolTip(device)
        self.interface.setPlaceholderText('No active Ethernet adapter')
        self.link.setText(speed_text(speed) if device else 'No Ethernet link')
        if sys.platform == 'darwin' and self.isVisible() and device and device not in self.maximums:
            self._mac_probe('media:' + device, ['-listvalidmedia', device],
                            lambda output, d=device: self._got_media(d, output))
        maximum = self.maximums.get(device, 0)
        self.maximum.setText(f'Up to {speed_text(maximum)}' if maximum else '')
        self.summary_form.setRowVisible(self.maximum, bool(maximum))
        if not device:
            hint = 'Connect a powered camera to a wired Ethernet adapter.'
        elif self.host and not self.active_device:
            hint = 'The wired adapter carrying camera traffic could not be identified. Check Network details below.'
        elif self.active_device and device != self.active_device:
            hint = f'Camera traffic uses {self.interface.itemText(self.interface.findData(self.active_device))}.'
        elif self.host and re.fullmatch(r'(?:Phantom\s+)?C980', self.model.strip(), re.I) and speed <= 1000:
            hint = 'The standalone C980 has a 1 Gb/s Ethernet port.'
            if speed == 1000:
                hint += ' This link is running at its full speed.'
        else:
            hint = 'Ethernet speed is selected automatically by the ports at both ends of the cable.'
        self.guidance.setText(hint)
        self.ipv4.setText('\n'.join(map(str, addresses)) or 'No wired IPv4 address')
        self.note.setText(subnet_hint(self.camera_ip.text(), addresses) if self.adapters
                          else 'Connect an Ethernet adapter to check addresses.')
        if self.temporary is not None:
            self.temporary.setText('Remove address…' if device in self.owned else 'Temporary address…')
            missing = any(not any(a.ip in net for a in addresses) for net in FACTORY_SUBNETS)
            hint = ('Remove the secondary address added by PCA.' if device in self.owned else
                    'Add a secondary IPv4 address with macOS approval.' if missing else
                    'Both Phantom networks are already configured.')
            self.temporary.setToolTip(hint)
            self.temporary.setEnabled(bool(re.fullmatch(r'en[0-9]{1,4}', device))
                                      and (missing or device in self.owned) and self.alias_process is None)

        content = (self.identity.text(), self.metadata.text(), self.memory_details, self.link.text(),
                   self.maximum.text(), hint, self.ipv4.text(), self.note.text())
        if self.isVisible() and content != self._content:
            self._content = content
            self._fit_height()

    def _fit_height(self):
        # The native macOS form has height-for-width rows. Refit when the
        # asynchronous adapter result adds a row or when details are disclosed.
        layout = self.layout()
        layout.invalidate()
        layout.activate()
        height = layout.totalHeightForWidth(self.width())
        if height > 0:
            self.resize(self.width(), height)
