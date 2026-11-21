import os
import threading
import time
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from event_camera import CameraRecording
from event_camera_discovery import DiscoveredCamera
from event_camera_picker import CameraRecordingDialog


def recording(host, serial='123', model='Phantom test'):
    return CameraRecording(host, model, serial, 1, -42, 77, 640, 480, 10000, (1, 0), 'P16')


class CameraPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        profile = TemporaryDirectory()
        self.addCleanup(profile.cleanup)
        self.enterContext(patch.dict(os.environ, {'PCA_USER_DATA_DIR': profile.name}))
        self.discover = self.enterContext(patch('event_camera_picker.discover_cameras', return_value=[]))
        self.listing = self.enterContext(patch('event_camera_picker.list_recordings', return_value=[]))
        self.dialog = CameraRecordingDialog()

    def tearDown(self):
        self.dialog.reject()
        self.wait(lambda: not self.dialog.workers)
        self.dialog.deleteLater()
        self.app.processEvents()

    def wait(self, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if condition():
                return
            time.sleep(.002)
        self.fail(self.dialog.discovery_note.text() + ' ' + self.dialog.status.text())

    def test_unique_camera_automatically_lists_recordings_and_remembers_only_on_open(self):
        d = self.dialog
        host = '192.0.2.11'
        self.discover.return_value = [DiscoveredCamera(host, '123', 'Bubble bench')]
        self.listing.return_value = [recording(host)]
        d.open()
        self.wait(d.use.isEnabled)
        self.assertEqual(d.cameras.count(), 1)
        self.assertIn('Bubble bench', d.cameras.item(0).text())
        self.assertIn('Phantom test', d.cameras.item(0).text())
        self.assertIn('123', d.cameras.item(0).text())
        self.assertIn('Frames -42 to 77', d.list.item(0).text())
        self.assertTrue(d.network.isHidden())
        self.assertFalse(d.settings.contains('last/host'))
        chosen = QSignalSpy(d.recordingSelected)
        d.use.click()
        self.assertEqual(chosen.at(0), [recording(host)])
        self.assertEqual(d.settings.value('last/host'), host)
        self.discover.return_value = []
        self.listing.reset_mock()
        d.open()
        self.wait(lambda: not d.workers)
        self.assertIn('Previously used', d.cameras.item(0).text())
        self.assertEqual(d.cameras.currentRow(), -1)
        self.listing.assert_not_called()
        d.cameras.setCurrentRow(0)
        self.wait(d.use.isEnabled)
        self.assertNotIn('Previously used', d.cameras.item(0).text())

    def test_remembered_model_and_click_to_retry_existing_row(self):
        d = self.dialog
        last, current, other = '192.0.2.70', '192.0.2.71', '192.0.2.72'
        for key, value in [('host', last), ('serial', '123'), ('model', 'Remembered model')]:
            d.settings.setValue(f'last/{key}', value)
        self.discover.return_value = [DiscoveredCamera(current, '123'), DiscoveredCamera(other, '456')]
        self.listing.side_effect = OSError('Camera did not reply')
        d.show()
        self.wait(lambda: not d.workers)
        self.assertEqual(d.cameras.count(), 2)
        item = d.cameras.item(0)
        self.assertIn('Remembered model', item.text())
        self.assertNotIn('Previously used', item.text())
        d.cameras.setCurrentRow(0)
        self.wait(lambda: 'did not reply' in d.status.text())
        self.assertEqual([call.args[0] for call in self.listing.call_args_list], [current, last])
        self.listing.side_effect = None
        self.listing.return_value = [recording(current)]
        QTest.mouseClick(d.cameras.viewport(), Qt.LeftButton, pos=d.cameras.visualItemRect(item).center())
        self.wait(d.use.isEnabled)
        self.assertEqual(d.items[0].host, current)

    def test_multiple_cameras_and_duplicate_ports_with_identity_checked_fallback(self):
        d = self.dialog
        fast, standard, other = '172.16.1.40', '100.100.1.40', '192.0.2.61'
        self.discover.return_value = [DiscoveredCamera(fast, '123'), DiscoveredCamera(standard, '123'),
                                      DiscoveredCamera(standard, '123'), DiscoveredCamera(other, '456')]
        def recordings(host, cancelled):
            self.assertFalse(cancelled())
            if host == fast:
                return [recording(host, serial='999')]
            return [recording(host)]
        self.listing.side_effect = recordings
        d.show()
        self.wait(lambda: d.cameras.count() == 2)
        self.listing.assert_not_called()
        self.assertEqual(d.cameras.currentRow(), -1)
        d.cameras.setCurrentRow(0)
        self.wait(d.use.isEnabled)
        self.assertEqual([call.args[0] for call in self.listing.call_args_list], [fast, standard])
        self.assertEqual(d.items[0].host, standard)
        self.assertIn(standard, d.cameras.item(0).text())
        d.cameras.setCurrentRow(1)
        self.wait(lambda: not d.workers)
        self.assertIn('different camera', d.status.text())
        self.assertFalse(d.use.isEnabled())

    def test_empty_port_tries_alternate_and_survives_alternate_timeout(self):
        d = self.dialog
        first, second = '192.0.2.41', '192.0.2.42'
        self.discover.return_value = [DiscoveredCamera(first, '123'), DiscoveredCamera(second, '123')]
        self.listing.side_effect = [[], [recording(second)]]
        d.open()
        self.wait(d.use.isEnabled)
        self.assertEqual(d.items[0].host, second)
        self.listing.side_effect = [[], OSError('Alternate timed out')]
        d.refresh.click()
        self.wait(lambda: not d.workers)
        self.assertFalse(d.use.isEnabled())
        self.assertIn('No completed recordings', d.status.text())
        self.assertEqual([call.args[0] for call in self.listing.call_args_list], [first, second, first, second])

    def test_manual_entry_during_discovery_and_empty_ram(self):
        d = self.dialog
        host = '192.0.2.70'
        release = threading.Event()
        self.addCleanup(release.set)
        def discover(cancelled):
            release.wait(1.5)
            return [DiscoveredCamera(host, '123')]
        self.discover.side_effect = discover
        d.open()
        d.manual.click()
        d.address.setText('invalid')
        d.connect_button.click()
        self.assertIn('IPv4', d.status.text())
        self.listing.assert_not_called()
        d.address.setText(host)
        QTest.keyClick(d.address, Qt.Key_Return)
        self.wait(lambda: 'No completed recordings' in d.status.text())
        release.set()
        self.wait(lambda: not d.workers)
        self.assertTrue(d.manual.isChecked())
        self.assertEqual(d.cameras.currentRow(), -1)
        self.assertEqual(self.listing.call_count, 1)
        self.assertTrue(d.isVisible())

    def test_outdated_lookup_cannot_replace_new_selection(self):
        d = self.dialog
        first, second = '192.0.2.14', '192.0.2.15'
        self.discover.return_value = [DiscoveredCamera(first, '123'), DiscoveredCamera(second, '456')]
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def recordings(host, cancelled):
            if host == first:
                started.set()
                release.wait(2)
                return [recording(first)]
            return [recording(second, '456')]
        self.listing.side_effect = recordings
        d.open()
        self.wait(lambda: d.cameras.count() == 2)
        d.cameras.setCurrentRow(0)
        self.wait(started.is_set)
        d.cameras.setCurrentRow(1)
        self.wait(d.use.isEnabled)
        release.set()
        self.wait(lambda: not d.workers)
        self.assertEqual(d.items, [recording(second, '456')])

    def test_reopen_ignores_cancelled_search_and_refresh_handles_failure(self):
        d = self.dialog
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def slow(cancelled):
            started.set()
            release.wait(2)
            return [DiscoveredCamera('192.0.2.66', '123')]
        self.discover.side_effect = slow
        d.open()
        self.wait(started.is_set)
        self.assertFalse(d.shutdown())
        d.reject()
        self.discover.side_effect = OSError('No wired connection.')
        d.open()
        self.wait(lambda: 'No wired connection' in d.discovery_note.text())
        release.set()
        self.wait(lambda: not d.workers)
        self.assertEqual(d.cameras.count(), 0)
        self.listing.assert_not_called()
        self.discover.side_effect = None
        self.discover.return_value = [DiscoveredCamera('192.0.2.12', '321')]
        d.refresh.click()
        self.wait(lambda: d.cameras.count() == 1 and not d.workers)
        self.assertIn('321', d.cameras.item(0).text())
