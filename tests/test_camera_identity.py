"""Static camera identity and hot-swappable media on the read-only channel."""
import unittest
from unittest.mock import Mock

from camera_identity import CameraIdentity, read_camera_identity
from event_camera import CameraCommandError, CameraReadError


class CameraIdentityTests(unittest.TestCase):
    def setUp(self):
        self.fields = {}
        self.channel = Mock()
        def get(key):
            if key not in self.fields:
                raise CameraCommandError('ERR: unknown field')
            return self.fields[key]
        self.channel.get.side_effect = get

    def test_fractional_ram_is_consistent_in_summary_and_network_details(self):
        identity = CameraIdentity(installed_mb=28416, recording_mb=27212)
        self.assertEqual(identity.summary, '27.75 GB RAM')
        self.assertIn('Installed RAM: 27.75 GB', identity.details)
        self.assertIn('Cine recording RAM: 26.57 GB', identity.details)
        self.assertEqual(CameraIdentity(recording_mb=1).recording_memory_label, '<0.01 GB')

    def test_installed_and_cine_ram_have_distinct_labels_and_camera_units(self):
        self.fields.update({'info.cfa': '0', 'info.memsz': '8192', 'info.cinemem': '8032'})
        identity = read_camera_identity(self.channel)
        self.assertEqual(identity.summary, 'Monochrome · 8 GB RAM')
        self.assertIn('Installed RAM: 8 GB', identity.details)
        self.assertIn('Cine recording RAM: 7.84 GB', identity.details)
        self.assertIn('Storage media: Not reported', identity.details)

    def test_missing_or_unknown_fields_do_not_guess_the_sensor_or_memory(self):
        self.assertEqual(read_camera_identity(self.channel), CameraIdentity())
        self.assertEqual(CameraIdentity().summary, '')
        self.fields.update({'info.cfa': '0xffffffff', 'info.memsz': '-1', 'info.cinemem': '12 MB'})
        unknown = read_camera_identity(self.channel)
        self.assertIn('Unknown CFA', unknown.sensor_label)
        self.assertEqual(unknown.summary, '')
        self.fields.update({'info.cfa': '0x3', 'info.cinemem': '0'})
        self.assertEqual(read_camera_identity(self.channel).summary, 'Color · 0 GB Cine RAM')

    def test_reinserted_card_reports_only_current_media_capacity(self):
        identity = CameraIdentity(cfa=0, installed_mb=2048)
        self.fields['cf'] = '{ state : 0, size : 99999 }'
        self.assertEqual(identity.with_current_media(self.channel, {'CF'}).media_label, 'No media inserted')
        self.fields['cf'] = '{ state : 2, size : 99999 }'
        self.assertEqual(identity.with_current_media(self.channel, {'CF'}).media_label, 'Initializing')
        self.fields['cf'] = '{ state : 4, size : 16384, used : 256 }'
        attached = identity.with_current_media(self.channel, {'CF'})
        self.assertEqual(attached.media_label, '16,384 kB')
        self.assertEqual(attached.summary, identity.summary)

    def test_multiple_storage_ports_and_cinestream_attachment(self):
        self.fields.update({'cf': '{state:0}', 'mag': '{state:4,id:"status, size:0",size:32768,type:100}'})
        self.assertEqual(CameraIdentity().with_current_media(self.channel, {'CF','MAG'}).media_label,
                         'Storage port: No media inserted · CineMag port: 32,768 kB')
        self.fields['mag'] = '{state:4,type:1010,size:1234}'
        self.assertEqual(CameraIdentity().with_current_media(self.channel, {'MAG'}).media_label,
                         'CineStream adapter')
        self.fields['mag'] = '{state:4,size:0}'
        self.assertEqual(CameraIdentity().with_current_media(self.channel, {'MAG'}).media_label,
                         'Capacity not reported')

    def test_protocol_errors_are_optional_but_network_errors_propagate(self):
        self.assertEqual(CameraIdentity().with_current_media(self.channel, {'CF'}).media_label, 'Not reported')
        self.channel.get.side_effect = CameraReadError('Connection closed')
        with self.assertRaises(CameraReadError):
            read_camera_identity(self.channel)
        with self.assertRaises(CameraReadError):
            CameraIdentity().with_current_media(self.channel, {'CF'})
