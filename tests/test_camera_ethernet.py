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
