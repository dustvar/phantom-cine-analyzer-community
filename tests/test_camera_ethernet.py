"""Network settings and admin arguments can be validated without changing an adapter."""
from ipaddress import IPv4Interface
import unittest

from camera_ethernet_ui import alias_arguments, subnet_hint


class EthernetSetupTests(unittest.TestCase):
    def test_address_check_flags_host_conflicts_and_different_subnets(self):
        addresses = [IPv4Interface('172.16.6.7/16')]
        self.assertIn('Different subnet', subnet_hint('100.100.4.50', addresses))
        self.assertIn('different IPv4 addresses', subnet_hint('172.16.6.7', addresses))
        self.assertIn('broadcast address', subnet_hint('172.16.255.255', addresses))
        self.assertIn('on this adapter', subnet_hint('172.16.4.50', addresses))
        self.assertIn('all four numbers', subnet_hint('172.16.', addresses))

    def test_elevated_alias_command_is_limited_to_secondary_phantom_addresses(self):
        add = alias_arguments('en14', '100.100.6.7')
        self.assertIn('/sbin/ifconfig en14 inet 100.100.6.7 netmask 255.255.0.0 alias', add[-1])
        remove = alias_arguments('en14', '100.100.6.7', True)
        self.assertIn('/sbin/ifconfig en14 inet 100.100.6.7 -alias', remove[-1])
        for device, address in [('lo0', '100.100.6.7'), ('en14;id', '100.100.6.7'),
                                ('en14', '192.168.1.251'), ('en14', '100.100.6.7;i'),
                                ('en14', '100.100.0.0'), ('en14', '172.16.255.255')]:
            with self.subTest(device=device, address=address), self.assertRaises(ValueError):
                alias_arguments(device, address)


class ConnectionSummaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_mac_reports_service_name_and_highest_media_independently_of_active_link(self):
        from camera_ethernet_ui import mac_service_names, mac_maximum_speed
        output = ('An asterisk denotes an inactive service.\n(1) Office LAN\n'
                  '(Hardware Port: Thunderbolt Ethernet Slot 0, Device: en7)\n'
                  '(2) Wi-Fi\n(Hardware Port: Wi-Fi, Device: en0)\n')
        self.assertEqual(mac_service_names(output)['en7'], 'Office LAN')
        helper = '(3) PCA Camera en7 (172.16)\n(Hardware Port: Thunderbolt Ethernet Slot 0, Device: en7)\n'
        self.assertEqual(mac_service_names(output + helper)['en7'], 'Office LAN')
        self.assertEqual(mac_service_names(helper + output)['en7'], 'Office LAN')
        self.assertEqual(mac_maximum_speed('autoselect\n1000baseT <full-duplex>\n10Gbase-T <full-duplex>'), 10000)
        self.assertEqual(mac_maximum_speed('2500Base-T <full-duplex>\n'), 2500)
        self.assertEqual(mac_maximum_speed('Unavailable'), 0)

    def test_selects_routed_adapter_and_only_falls_back_on_a_unique_subnet(self):
        from ipaddress import IPv4Address
        from camera_ethernet_ui import camera_adapter
        records = {'en6': (1000, [IPv4Interface('172.16.6.7/16')]),
                   'en9': (10000, [IPv4Interface('172.16.20.4/16')])}
        self.assertEqual(camera_adapter('172.16.11.4', records, IPv4Address('172.16.20.4')), 'en9')
        self.assertEqual(camera_adapter('172.16.11.4', records, None), '')
        self.assertEqual(camera_adapter('172.16.11.4', records, IPv4Address('10.9.8.1')), '')
        self.assertEqual(camera_adapter('172.16.11.4', {'en6': records['en6']}, None), 'en6')

    def test_connected_cable_is_the_only_default_mac_choice_including_before_dhcp(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import camera_ethernet_ui as ethernet
        devices = ['anpi0', 'en1', 'en4', 'en13', 'en14']
        inventory = []
        for name in devices:
            nic = MagicMock()
            nic.name.return_value = nic.humanReadableName.return_value = name
            inventory.append((nic, ()))
        stats = {name: SimpleNamespace(isup=True, speed=0) for name in devices}
        links = '\n'.join(f'{d}: flags=...\n\tstatus: {"active" if d == "en14" else "inactive"}'
                          for d in devices)
        with (patch.object(ethernet.sys, 'platform', 'darwin'),
              patch.object(ethernet, 'wired_interfaces', return_value=inventory),
              patch.object(ethernet.psutil, 'net_if_stats', return_value=stats),
              patch.object(ethernet.CameraEthernetDialog, '_mac_probe')):
            dialog = ethernet.CameraEthernetDialog()
            self.addCleanup(dialog.close)
            dialog._got_names('(1) USB Ethernet\n(Hardware Port: USB Ethernet, Device: en13)\n'
                              '(2) Thunderbolt Ethernet Slot 1\n(Hardware Port: Thunderbolt Ethernet Slot 1, Device: en14)')
            dialog._got_links(links)
            dialog.show()
            self.app.processEvents()
            self.assertEqual(dialog.interface.count(), 1)
            self.assertEqual(dialog.interface.currentData(), 'en14')
            self.assertEqual(dialog.adapter_summary.text(), 'Thunderbolt Ethernet Slot 1')
            self.assertFalse(dialog.interface.isVisible())
            self.assertEqual(dialog.link.text(), 'Connected')
            self.assertTrue(dialog.setup.isVisible())
            dialog.set_candidate('100.100.40.1', 'Lab camera', '42', 'en14')
            self.assertEqual(dialog.identity.text(), 'Lab camera')
            self.assertIn('Discovered', dialog.metadata.text())
            self.assertEqual(dialog.camera_ip.text(), '100.100.40.1')
            self.assertFalse(dialog.details.isVisible())
            dialog.advanced.click()
            dialog.show_unlinked.click()
            self.assertEqual([dialog.interface.itemData(i) for i in range(dialog.interface.count())],
                             ['en14', 'en13'])
            dialog.interface.setCurrentIndex(1)
            dialog._select_device()
            dialog._refresh()
            self.assertEqual(dialog.interface.currentData(), 'en13')
            self.assertEqual(dialog.link.text(), 'No Ethernet link')
            self.assertFalse(dialog.setup.isVisible())
            dialog.advanced.click()
            self.assertEqual(dialog.interface.currentData(), 'en14')
            inventory.pop()  # Link unplugged; configured USB adapter remains, plus Apple's internal ports.
            dialog._refresh()
            self.assertEqual(dialog.interface.count(), 1)
            self.assertEqual(dialog.adapter_summary.text(), 'USB Ethernet')
            self.assertEqual(dialog.link.text(), 'No Ethernet link')
            inventory.clear()
            dialog._refresh()
            self.assertEqual(dialog.interface.count(), 0)
            self.assertEqual(dialog.adapter_summary.text(), 'No wired adapter detected')
            self.assertFalse(dialog.setup.isVisible())

    def test_setup_offers_to_save_a_temporary_alias_and_rediscovers_after_approval(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import camera_ethernet_ui as ethernet
        nick = MagicMock()
        nick.name.return_value = nick.humanReadableName.return_value = 'en14'
        base = IPv4Interface('172.16.6.7/16')
        alias = IPv4Interface('100.100.6.7/16')
        with (patch.object(ethernet.sys, 'platform', 'darwin'),
              patch.object(ethernet, 'wired_interfaces', return_value=[(nick, (base, alias))]),
              patch.object(ethernet.psutil, 'net_if_stats', return_value={'en14': SimpleNamespace(isup=True, speed=10000)}),
              patch.object(ethernet.CameraEthernetDialog, '_mac_probe'),
              patch.object(ethernet, 'setup_arguments', return_value=['example']) as arguments,
              patch.object(ethernet, 'QProcess') as process):
            dialog = ethernet.CameraEthernetDialog()
            self.addCleanup(dialog.close)
            dialog.show()
            dialog._got_names('(1) Lab adapter\n(Hardware Port: Thunderbolt Ethernet Slot 1, Device: en14)')
            dialog._got_address('Lab adapter', 'Manual Configuration\nIP address: 172.16.6.7\nSubnet mask: 255.255.0.0\n')
            self.assertTrue(dialog.setup.isVisible())
            self.assertTrue(dialog.details.isHidden())
            process.assert_not_called()
            updated = MagicMock()
            dialog.networkChanged.connect(updated)
            dialog.setup.click()
            self.assertEqual(arguments.call_args.args[:2], ('en14', (base,)))
            self.assertTrue(dialog.setup_process)
            self.assertFalse(dialog.setup.isEnabled())
            updated.assert_not_called()
            job = process.return_value
            job.readAllStandardError.return_value = b'User canceled. (-128)'
            job.finished.connect.call_args.args[0](1, process.NormalExit)
            dialog._poll_adapters()
            self.app.processEvents()
            updated.assert_not_called()
            self.assertIsNone(dialog.setup_process)
            self.assertEqual(process.call_count, 1)
            dialog._got_address('Lab adapter', 'Manual Configuration\nIP address: 172.16.6.7\nSubnet mask: 255.255.0.0\n')
            dialog.setup.click()
            job.readAllStandardError.return_value = b''
            finished = job.finished.connect.call_args.args[0]
            finished(0, process.NormalExit)
            updated.assert_called_once()
            dialog._got_names('(1) Lab adapter\n(Hardware Port: Thunderbolt Ethernet Slot 1, Device: en14)\n'
                              '(2) PCA Camera en14 (100.100)\n(Hardware Port: Thunderbolt Ethernet Slot 1, Device: en14)')
            dialog._got_address('Lab adapter', 'Manual Configuration\nIP address: 172.16.6.7\nSubnet mask: 255.255.0.0\n')
            dialog._got_address('PCA Camera en14 (100.100)', 'Manual Configuration\nIP address: 100.100.6.7\nSubnet mask: 255.255.0.0\n')
            self.assertTrue(dialog.setup.isHidden())
            self.assertEqual(dialog.adapter_summary.text(), 'Lab adapter')

    def test_connected_disconnected_and_other_adapter_views(self):
        from ipaddress import IPv4Address
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        import camera_ethernet_ui as ethernet
        def interface(device):
            obj = MagicMock()
            obj.name.return_value = obj.humanReadableName.return_value = device
            return obj
        found = [(interface('en3'), [IPv4Interface('10.2.3.4/24')]),
                 (interface('en7'), [IPv4Interface('172.16.2.1/16')])]
        speeds = {'en3': SimpleNamespace(speed=100, isup=True), 'en7': SimpleNamespace(speed=1000, isup=True)}
        with (patch.object(ethernet, 'wired_interfaces', return_value=found),
              patch.object(ethernet, 'routed_address', side_effect=lambda host: IPv4Address('172.16.2.1') if host else None),
              patch.object(ethernet.psutil, 'net_if_stats', return_value=speeds),
              patch.object(ethernet.CameraEthernetDialog, '_mac_probe')):
            dialog = ethernet.CameraEthernetDialog()
            self.addCleanup(dialog.close)
            dialog.service_names = {'en7':'Lab Ethernet'}
            dialog.set_camera('172.16.2.4', 'Phantom C980', '12345', 'Monochrome · 28 GB RAM', 'Installed RAM: 28 GB')
            dialog.show()
            self.app.processEvents()
            dialog._got_media('en7', '1000baseT\n10Gbase-T')
            self.assertEqual(dialog.interface.currentData(), 'en7')
            self.assertEqual(dialog.interface.currentText(), 'Lab Ethernet')
            self.assertEqual(dialog.link.text(), '1 Gb/s')
            self.assertIn('10 Gb/s', dialog.maximum.text())
            self.assertIn('full speed', dialog.guidance.text())
            self.app.processEvents()
            self.assertLess(dialog.interface.geometry().bottom(), dialog.link.geometry().top())
            self.assertIn('Monochrome', dialog.metadata.text())
            self.assertTrue(dialog.details.isHidden())
            dialog.advanced.click()
            self.assertTrue(dialog.details.isVisible())
            self.assertIn('28 GB', dialog.memory.text())
            self.assertEqual(dialog.camera_ip.text(), '172.16.2.4')
            self.assertGreaterEqual(dialog.ipv4.height(), dialog.ipv4.sizeHint().height())
            self.assertIn('on this adapter', dialog.note.text())
            dialog.interface.setCurrentIndex(0)
            dialog._select_device()
            dialog._refresh()
            self.assertEqual(dialog.interface.currentData(), 'en3')
            self.assertIn('Lab Ethernet', dialog.guidance.text())
            self.assertIn('Different subnet', dialog.note.text())
            dialog.set_camera('')
            self.assertEqual(dialog.identity.text(), 'No camera connected')
            self.assertTrue(dialog.memory.isHidden())
            self.assertTrue(dialog.metadata.isHidden())
            self.assertEqual(dialog.camera_ip.text(), '')
