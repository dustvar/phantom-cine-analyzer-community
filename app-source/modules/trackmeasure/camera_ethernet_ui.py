"""Inspect the wired connection and compare its IPv4 subnet with a camera label."""
from ipaddress import IPv4Address, IPv4Network
import re
import sys

import psutil
from PySide6.QtCore import QProcess, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkInterface
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout)

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


class CameraEthernetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Ethernet setup')
        self.resize(480, 260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.interface = QComboBox()
        self.link = QLabel()
        self.ipv4 = QLabel()
        self.ipv4.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ipv4.setWordWrap(True)
        self.camera_ip = QLineEdit()
        self.camera_ip.setPlaceholderText('IP or XIP from camera label')
        form.addRow('&Adapter', self.interface)
        form.addRow('Negotiated link', self.link)
        form.addRow('Adapter IPv4', self.ipv4)
        form.addRow('&Camera IPv4', self.camera_ip)
        layout.addLayout(form)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setAccessibleName('Camera subnet check')
        layout.addWidget(self.note)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.settings_url = {'darwin': 'x-apple.systempreferences:com.apple.Network-Settings.extension',
                             'win32': 'ms-settings:network-ethernet'}.get(sys.platform)
        if self.settings_url:
            settings = buttons.addButton('Open network settings…', QDialogButtonBox.ActionRole)
            settings.clicked.connect(self._open_settings)
        self.temporary = None
        self.alias_process = None
        self.owned = {}
        self.devices = {}
        if sys.platform == 'darwin':
            self.temporary = buttons.addButton('Temporary address…', QDialogButtonBox.ActionRole)
            self.temporary.clicked.connect(self._edit_alias)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.interface.currentIndexChanged.connect(self._update_details)
        self.camera_ip.textChanged.connect(self._update_details)
        self.poll = QTimer(self)
        self.poll.setInterval(2000)
        self.poll.timeout.connect(self._refresh)
        self.status_by_name = {}

    def _open_settings(self):
        if not QDesktopServices.openUrl(QUrl(self.settings_url)):
            self.note.setText('Open your computer’s network settings and select the wired adapter.')

    def _edit_alias(self):
        if self.alias_process is not None:
            return
        self._refresh()
        name = self.interface.currentText()
        device = self.devices.get(name)
        if not device:
            return
        _, addresses = self.status_by_name[name]
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
        self.devices = {}
        for adapter, addresses in wired_interfaces():
            name = adapter.humanReadableName() or adapter.name()
            status = stats.get(name) or stats.get(adapter.name())
            speed = status.speed if status and status.isup else 0
            if speed or addresses:
                found[name] = (speed, addresses)
                self.devices[name] = adapter.name()
        active_addresses = {self.devices[name]: {str(a.ip) for a in entries}
                            for name, (_, entries) in found.items()}
        self.owned = {device: ip for device, ip in self.owned.items() if ip in active_addresses.get(device, ())}
        prior = self.interface.currentText()
        if list(found) != list(self.status_by_name):
            self.interface.blockSignals(True)
            self.interface.clear()
            self.interface.addItems(found.keys())
            if prior in found:
                self.interface.setCurrentText(prior)
            self.interface.blockSignals(False)
        self.status_by_name = found
        self._update_details()

    def _update_details(self):
        speed, addresses = self.status_by_name.get(self.interface.currentText(), (0, ()))
        self.link.setText(f'{speed / 1000:g} Gb/s' if speed >= 1000 else
                          f'{speed:g} Mb/s' if speed > 0 else 'Unavailable')
        self.ipv4.setText(' · '.join(map(str, addresses)) or 'No wired IPv4 address')
        self.note.setText(subnet_hint(self.camera_ip.text(), addresses) if self.status_by_name
                          else 'Connect a powered camera to the wired adapter.')
        if self.temporary is not None:
            device = self.devices.get(self.interface.currentText(), '')
            self.temporary.setText('Remove address…' if device in self.owned else 'Temporary address…')
            missing = any(not any(a.ip in net for a in addresses) for net in FACTORY_SUBNETS)
            hint = ('Remove the secondary address added by PCA.' if device in self.owned else
                    'Add a secondary IPv4 address with macOS approval.' if missing else
                    'Both Phantom networks are already configured.')
            self.temporary.setToolTip(hint)
            self.temporary.setEnabled(bool(re.fullmatch(r'en[0-9]{1,4}', device))
                                      and (missing or device in self.owned) and self.alias_process is None)
