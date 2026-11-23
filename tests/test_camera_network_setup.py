"""Preflight the saved adapter setup without touching system preferences."""
from ipaddress import IPv4Interface
import shlex
import unittest

from camera_network_setup import setup_arguments


class CameraNetworkSetupTests(unittest.TestCase):
    def requested(self, device, addresses, others=()):
        args = setup_arguments(device, addresses, others)
        return shlex.split(args[-1])[5:]

    def test_existing_veo_service_only_adds_kt_subnet(self):
        self.assertEqual(self.requested('en14', [IPv4Interface('172.16.6.7/16')]),
                         ['100.100.6.7'])

    def test_first_time_adapter_needs_no_camera_label(self):
        self.assertEqual(self.requested('en8', []), ['100.100.6.7', '172.16.6.7'])

    def test_adapter_takes_different_address_from_detected_camera(self):
        args = setup_arguments('en14', ['172.16.6.7/16'], [], camera_host='100.100.6.7')
        self.assertEqual(shlex.split(args[-1])[5:], ['100.100.6.8'])

    def test_already_configured_and_conflicting_subnets(self):
        with self.assertRaisesRegex(ValueError, 'already covers'):
            self.requested('en8', ['100.100.6.7/16', '172.16.6.7/16'])
        with self.assertRaisesRegex(ValueError, 'Another network interface'):
            self.requested('en8', [], ['100.100.42.6/24'])
        with self.assertRaisesRegex(ValueError, 'different subnet mask'):
            self.requested('en8', ['172.16.6.7/24'])
        with self.assertRaisesRegex(ValueError, 'wired|Ethernet'):
            self.requested('en14;echo invalid', [])
