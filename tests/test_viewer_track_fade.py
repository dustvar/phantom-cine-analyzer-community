"""A source frame starts the tracking reveal; its duration belongs to replay time."""
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest

import cv2
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from viewer_track_fade import default_track_fade, draw_track_fade
from viewer_sequence_mixin import ViewerSequenceMixin
from viewer_effects import VisualTimeline, apply_effects
from viewer_timeline import default_edit, append_video, validate_edit
from viewer_processing import default_grade
from video_export import export_clip
from test_cine_metadata import write_cine
from test_viewer_export import SETTINGS


def track(frames, xy, color):
    return dict(name='Ice fragment', color=color, enabled=True, points=np.array(xy), frames=np.array(frames))


class TrackFadeTests(unittest.TestCase):
    def test_held_frame_fades_in_playback_seconds_and_hides_future_or_disabled_tracks(self):
        edit = default_edit(16)
        edit['sections'][0]['fps'] = 2
        edit['track_fade'] = dict(default_track_fade(3), enabled=True, duration=.5)
        tracks = {0: track([3], [(8, 8)], '#ff0000'),
                  1: dict(track([3], [(26, 8)], '#00ff00'), enabled=False),
                  2: track([5], [(20, 20)], '#0000ff')}
        plan = VisualTimeline(edit)
        image = np.zeros((32, 32, 3), np.uint8)
        def render(t):
            frame, rate = plan.plan.sample(t)
            return apply_effects(image, edit, SimpleNamespace(), plan, t, frame, rate, tracks=tracks)[0]
        self.assertFalse(render(1.49).any())
        self.assertFalse(render(1.5).any())
        partial = render(1.75)
        self.assertAlmostEqual(int(partial[8,8,0]), 128, delta=2)
        self.assertFalse(partial[8,26].any())
        self.assertFalse(partial[20,20].any())
        self.assertFalse(image.any())

    def test_tracks_follow_source_across_repeated_passes_and_never_leak_onto_an_added_file(self):
        edit = append_video(append_video(default_edit(6)), media=dict(frame_count=6, source_path='/tmp/b.cine'))
        edit = append_video(edit, media=dict(frame_count=6, source_path='/tmp/untracked.cine'))
        tracks_a = {0: track(range(6), [(10, 10)] * 6, '#ff0000')}
        tracks_b = {0: track(range(6), [(20, 20)] * 6, '#00ff00')}
        for row in [edit, *edit['copies'][1:]]:
            row['track_fade'] = dict(default_track_fade(1), enabled=True)
        ui = ViewerSequenceMixin()
        ui.vm = SimpleNamespace(viewer_edit=edit, active_cine_index=0, track_data=tracks_a,
            workspace_contexts=[dict(cine_path='/tmp/a.cine',track_data=tracks_a),
                                dict(cine_path='/tmp/b.cine',track_data=tracks_b)])
        snapshots = ui._movie_tracks_snapshot()
        self.assertIs(snapshots[0], snapshots[1])
        self.assertEqual(snapshots[2][0]['color'], '#00ff00')
        self.assertFalse(snapshots[3])
        tracks_a[0]['points'][0,0] = 40
        self.assertEqual(snapshots[0][0]['points'][0,0], 10)
        # A fresh repeated pass starts clean until its own reveal is enabled.
        reset = append_video(edit)
        self.assertNotIn('track_fade', reset['copies'][-1])
        self.assertEqual(validate_edit(edit,6), edit)

    def test_trimmed_movie_keeps_fade_phase_from_before_in_point(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'original.cine'
            movie = Path(directory)/'impact.mp4'
            write_cine(source, frames=12, width=128, height=88)
            edit = default_edit(12, 30)
            edit['sections'][0]['fps'] = 10
            edit['track_fade'] = dict(default_track_fade(4), enabled=True, duration=.4)
            records = {0: track(range(12), [(60, 40)] * 12, '#ff0000')}
            export_clip(str(source), str(movie), 6, 9, 30, SETTINGS, default_grade(),
                        edit=edit, tracks_by_copy={0: records})
            stream = cv2.VideoCapture(str(movie))
            try:
                frames = []
                while True:
                    ok, frame = stream.read()
                    if not ok: break
                    patch = frame[36:45,56:65].astype(float)
                    frames.append(float((patch[:,:,2] - patch[:,:,1]).mean()))
            finally:
                stream.release()
            self.assertEqual(len(frames), 12)
            self.assertGreater(frames[0], 15)
            self.assertGreater(frames[6], frames[0] * 1.3)

    def test_sparse_track_and_zero_duration(self):
        options = dict(default_track_fade(1), enabled=True, duration=0, trail=12)
        image = np.zeros((40,40,3), np.uint8)
        tracks = {0: track([1, 6], [(5,5),(35,35)], '#ffffff')}
        marked = draw_track_fade(image, tracks, options, 6, 0)
        self.assertTrue(marked[5,5].any())
        self.assertTrue(marked[35,35].any())
        self.assertFalse(marked[20,20].any())
        options['trail'] = 0
        self.assertIs(draw_track_fade(image, tracks, options, 4, 1), image)
