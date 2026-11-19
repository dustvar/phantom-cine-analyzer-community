import sys, unittest, tempfile, json
from types import SimpleNamespace
from copy import deepcopy
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from viewer_timeline import default_edit, validate_edit, split_at, move_cut, merge_next, Timeline, overlay_text, camera_overlay_text, default_camera_overlay
from viewer_overlay import apply_fps_overlay, apply_data_overlays
from viewer_project import save_project, load_project
from viewer_processing import default_grade
from test_cine_metadata import write_cine
from test_viewer_export import SETTINGS


def three_sections():
    edit = default_edit(300)
    edit = split_at(split_at(edit, 100), 200)
    for section, rate in zip(edit['sections'], [120, 30, 90]):
        section['fps'] = rate
    return edit


class TimelineTests(unittest.TestCase):
    def test_move_cut_preserves_partition_settings_and_input(self):
        edit = three_sections()
        edit['sections'][1].update(ramp=.4, ramp_place='center', ramp_curve='ease_in')
        original = deepcopy(edit)
        moved = move_cut(edit, 1, 150)
        self.assertEqual(edit, original)
        self.assertEqual([(s['start'], s['end']) for s in moved['sections']], [(0,149),(150,199),(200,299)])
        self.assertEqual(validate_edit(moved, 300), moved)
        for before, after in zip(edit['sections'], moved['sections']):
            self.assertEqual({k:v for k,v in before.items() if k not in ('start','end')},
                             {k:v for k,v in after.items() if k not in ('start','end')})
        self.assertEqual(moved['overlay'], edit['overlay'])

    def test_move_cut_clamps_to_nonempty_neighbors_and_never_crosses_another_cut(self):
        edit = three_sections()
        for request, expected in [(-100,101), (1000,299), (101,101), (299,299)]:
            moved = move_cut(edit, 2, request)
            self.assertEqual(moved['sections'][2]['start'], expected)
            self.assertEqual(moved['sections'][1]['end'], expected - 1)
            self.assertEqual(moved['sections'][0], edit['sections'][0])
            self.assertEqual(validate_edit(moved, 300), moved)
        for index in (-1, 0, 3):
            self.assertEqual(move_cut(edit, index, 10), edit)

    def test_move_cut_changes_speed_boundary_and_keeps_equal_rate_frame_order(self):
        edit = split_at(default_edit(12), 6)
        moved = move_cut(edit, 1, 8)
        self.assertEqual(list(Timeline(moved,0,11).samples()), list(Timeline(edit,0,11).samples()))
        moved['sections'][1]['fps'] = 15
        self.assertEqual([f for f,r in Timeline(moved,0,11).samples()], list(range(8)) + [8,8,9,9,10,10,11,11])

    def test_moving_cut_next_to_skipped_section_keeps_valid_source_partition(self):
        edit = three_sections(); edit['sections'][1]['keep'] = False
        moved = move_cut(edit, 2, 180)
        self.assertEqual(validate_edit(moved, 300), moved)
        self.assertEqual([(s['start'],s['end']) for s in moved['sections']], [(0,99),(100,179),(180,299)])
        self.assertFalse(moved['sections'][1]['keep'])

    def test_identity_includes_each_selected_frame_once(self):
        for fps in (24, 25, 29.97, 30, 60):
            plan = Timeline(default_edit(100, fps), 7, 89)
            self.assertEqual(plan.frame_count, 83)
            self.assertEqual([f for f, rate in plan.samples()], list(range(7, 90)))
            self.assertAlmostEqual(plan.duration, 83 / fps)

    def test_faster_then_slower_sections_have_expected_duration_and_sampling(self):
        edit = default_edit(12)
        edit = split_at(edit, 6)
        edit['sections'][0]['fps'] = 60
        edit['sections'][1]['fps'] = 15
        plan = Timeline(edit, 0, 11)
        self.assertAlmostEqual(plan.duration, .5)
        self.assertEqual([f for f, r in plan.samples()], [0, 2, 4, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11])

    def test_smooth_ramp_uses_finished_video_seconds_and_continuous_rate(self):
        edit = three_sections()
        edit['sections'][1]['ramp'] = .8
        edit['sections'][2]['ramp'] = 1
        plan = Timeline(edit, 0, 299)
        transition = 100 / 120
        # Integral of 120->30 smoothstep over .8 sec consumes 60 frames.
        self.assertEqual(plan.sample(transition)[0], 100)
        self.assertAlmostEqual(plan.sample(transition)[1], 120)
        self.assertAlmostEqual(plan.sample(transition + .4)[1], 75)
        self.assertAlmostEqual(plan.sample(transition + .8)[1], 30)
        self.assertEqual(plan.sample(transition + .8)[0], 160)
        self.assertAlmostEqual(plan.duration, 100/120 + .8 + 40/30 + 1 + 40/90)
        rates = [plan.sample(transition + x)[1] for x in np.linspace(0, .8, 101)]
        self.assertTrue(np.all(np.diff(rates) <= 0))

    def test_ramps_too_long_fit_and_reach_target(self):
        edit = three_sections(); edit['sections'][1]['ramp'] = 90
        plan = Timeline(edit, 0, 299)
        self.assertEqual(len(plan.ramp_limits), 1)
        self.assertAlmostEqual(plan.ramp_limits[0][2], 200/150)
        self.assertAlmostEqual(plan.parts[1][0].rate(plan.parts[1][0].duration), 30)

    def test_trimming_inside_ramp_preserves_velocity(self):
        edit = three_sections(); edit['sections'][1]['ramp'] = .8
        full = Timeline(edit, 0, 299)
        trimmed = Timeline(edit, 130, 179)
        offset = full.time_for_source(130)
        self.assertAlmostEqual(trimmed.sample(0)[1], full.sample(offset)[1], places=8)
        self.assertEqual(trimmed.sample(0)[0], 130)
        for time in np.linspace(0, trimmed.duration - 1e-8, 50):
            a, b = trimmed.sample(time), full.sample(offset + time)
            self.assertEqual(a[0], b[0]); self.assertAlmostEqual(a[1], b[1], places=7)

    def test_splitting_inside_ramp_does_not_change_video(self):
        edit = three_sections(); edit['sections'][1]['ramp'] = 1
        before = Timeline(edit, 0, 299)
        after = Timeline(split_at(edit, 130), 0, 299)
        self.assertAlmostEqual(before.duration, after.duration)
        self.assertEqual(list(before.samples()), list(after.samples()))

    def test_skip_section_and_restore_have_no_duplicate_or_missing_boundaries(self):
        edit = default_edit(12); edit = split_at(split_at(edit, 4), 8)
        edit['sections'][1]['keep'] = False
        self.assertEqual([f for f,r in Timeline(edit, 0, 11).samples()], [0,1,2,3,8,9,10,11])
        plan = Timeline(edit, 4, 7)
        self.assertEqual(plan.frame_count, 0)
        edit['sections'][1]['keep'] = True
        self.assertEqual([f for f,r in Timeline(edit, 0, 11).samples()], list(range(12)))

    def test_split_and_merge_preserve_partition_and_do_not_mutate_input(self):
        edit = default_edit(20); original = deepcopy(edit)
        split = split_at(edit, 7)
        self.assertEqual(edit, original)
        self.assertEqual([(s['start'],s['end']) for s in split['sections']], [(0,6),(7,19)])
        self.assertEqual(merge_next(split, 0), original)
        self.assertEqual(split_at(edit, 0), original)

    def test_invalid_timing_cannot_enter_an_edit(self):
        for key, value in [('fps', 0), ('fps', float('nan')), ('ramp', -1), ('end', 300)]:
            edit = three_sections(); edit['sections'][0][key] = value
            with self.assertRaises(ValueError): validate_edit(edit, 300)
        self.assertEqual(validate_edit(three_sections(), 300), three_sections())

    def test_data_box_reports_capture_playback_and_true_slowdown(self):
        overlay = default_edit()['overlay']
        overlay['fields'] = ['slowdown', 'capture', 'playback']
        self.assertEqual(overlay_text(overlay, 10, 30, 1000), '100× slower\n1,000 fps\n10 fps playback')
        self.assertEqual(overlay_text(overlay, 5, 30, 1000), '200× slower\n1,000 fps\n5 fps playback')
        # Output encoding cadence must not change the real-world slowdown.
        self.assertEqual(overlay_text(overlay, 10, 60, 1000), overlay_text(overlay, 10, 30, 1000))
        self.assertIn('Real time', overlay_text(overlay, 1000, 30, 1000))
        self.assertIn('2× faster', overlay_text(overlay, 2000, 30, 1000))
        self.assertIn('50× slower\n1,000 fps', overlay_text(overlay, 10, 30, 1000, decimation=2))
        self.assertIn('unavailable', overlay_text(overlay, 10, 30, float('nan')))

    def test_data_defaults_and_decimal_option_match_requested_readouts(self):
        overlay = default_edit()['overlay']
        self.assertEqual(overlay['layout'], 'stacked')
        self.assertFalse(overlay['decimals'])
        self.assertNotIn('capture', overlay['fields'])
        self.assertEqual(overlay_text(overlay, 30, 30, 94000), '3,133× slower\n30 fps playback')
        overlay['fields'] = ['slowdown', 'capture', 'playback']
        self.assertEqual(overlay_text(overlay, 30, 30, 94000), '3,133× slower\n94,000 fps\n30 fps playback')
        overlay['decimals'] = True
        self.assertEqual(overlay_text(overlay, 30, 30, 94000), '3,133.3× slower\n94,000.0 fps\n30.0 fps playback')
        overlay['decimals'] = False
        self.assertIn('<1 fps playback', overlay_text(overlay, .1, 30, 94000))

    def test_layout_and_decimal_options_validate_and_older_edits_get_defaults(self):
        edit = three_sections()
        edit['overlay'].pop('layout'); edit['overlay'].pop('decimals')
        upgraded = validate_edit(edit, 300)
        self.assertEqual(upgraded['overlay']['layout'], 'stacked')
        self.assertFalse(upgraded['overlay']['decimals'])
        self.assertNotIn('layout', edit['overlay'])
        np.testing.assert_allclose(list(Timeline(edit, 0, 299).samples()), list(Timeline(upgraded, 0, 299).samples()))
        for key, bad in [('layout', 'diagonal'), ('decimals', 1)]:
            invalid = deepcopy(upgraded); invalid['overlay'][key] = bad
            with self.assertRaises(ValueError): validate_edit(invalid, 300)

    def test_straight_card_is_one_short_row_and_stacked_card_is_taller(self):
        overlay = default_edit()['overlay']; overlay['enabled'] = True
        overlay['fields'] = ['slowdown', 'capture', 'playback']
        text = overlay_text(overlay, 30, 30, 94000)
        source = np.zeros((640, 1024, 3), dtype=np.uint8)
        _, stacked = apply_fps_overlay(source, text, overlay)
        overlay['layout'] = 'straight'
        _, straight = apply_fps_overlay(source, text, overlay)
        self.assertGreater(straight[2], stacked[2] * 1.5)
        self.assertLess(straight[3], stacked[3] * .6)
        overlay.update(x=1, y=1, decimals=True)
        for dimensions in [(80, 120), (2, 2)]:
            small = np.zeros((*dimensions, 3), dtype=np.uint8)
            _, (x, y, w, h) = apply_fps_overlay(small, overlay_text(overlay, 30, 30, 94000), overlay)
            self.assertEqual((x+w, y+h), (dimensions[1], dimensions[0]))

    def test_each_data_field_can_be_shown_alone_or_hidden(self):
        overlay = default_edit()['overlay']
        for fields, expected in [(['capture'], '1,000 fps'), (['slowdown'], '100× slower'),
                                 (['playback'], '10 fps playback'), ([], '')]:
            overlay['fields'] = fields
            self.assertEqual(overlay_text(overlay, 10, 30, 1000), expected)
        image = np.zeros((240, 320, 3), dtype=np.uint8)
        overlay['enabled'] = True
        result, rect = apply_fps_overlay(image, '', overlay)
        self.assertIs(result, image); self.assertIsNone(rect)

    def test_camera_and_playback_defaults_are_independent_and_in_opposite_corners(self):
        edit = default_edit(100)
        self.assertEqual(edit['overlay']['fields'], ['slowdown', 'playback'])
        self.assertEqual((edit['overlay']['x'], edit['overlay']['y']), (.96, .96))
        self.assertEqual(edit['camera_overlay']['fields'], ['model', 'capture', 'resolution'])
        self.assertEqual((edit['camera_overlay']['x'], edit['camera_overlay']['y']), (.04, .96))
        edit['camera_overlay']['fields'].remove('capture')
        self.assertIn('capture', default_camera_overlay()['fields'])

    def test_camera_text_uses_model_capture_rate_and_source_resolution(self):
        md = SimpleNamespace(CameraModel='Phantom TMX 7510', FrameRate=94000, ImWidth=1024, ImHeight=640)
        options = default_camera_overlay()
        self.assertEqual(camera_overlay_text(options, md), 'Phantom TMX 7510\n94,000 fps\n1,024 × 640 px')
        options['fields'] = ['capture', 'resolution']; options['decimals'] = True
        self.assertEqual(camera_overlay_text(options, md), '94,000.0 fps\n1,024 × 640 px')
        options['fields'] = ['model']; md.CameraModel = ''
        self.assertEqual(camera_overlay_text(options, md), 'Model not stored')
        self.assertEqual(camera_overlay_text(default_camera_overlay(), SimpleNamespace()),
                         'Model not stored\nCapture FPS unavailable\nResolution unavailable')

    def test_both_cards_and_camera_only_render_without_changing_the_source(self):
        md = SimpleNamespace(CameraModel='Phantom TMX 7510', FrameRate=94000, ImWidth=1024, ImHeight=640)
        edit = default_edit(100)
        image = np.full((640,1024,3), 80, dtype=np.uint8); original=image.copy()
        edit['camera_overlay']['enabled'] = True
        camera_image, rects = apply_data_overlays(image, edit, md, 30)
        self.assertEqual(set(rects), {'camera'})
        camera_rect = rects['camera']
        edit['overlay']['enabled'] = True
        combined, rects = apply_data_overlays(image, edit, md, 30)
        self.assertEqual(set(rects), {'camera', 'playback'})
        self.assertLess(camera_rect[0]+camera_rect[2], rects['playback'][0])
        self.assertGreater(camera_rect[1], image.shape[0] * .75)
        self.assertGreater(rects['playback'][1], image.shape[0] * .75)
        self.assertFalse(np.array_equal(combined,camera_image))
        np.testing.assert_array_equal(image,original)
        edit['camera_overlay']['enabled'] = False
        self.assertEqual(set(apply_data_overlays(image,edit,md,30)[1]), {'playback'})

    def test_legacy_single_box_recipe_keeps_its_settings_and_gains_disabled_camera_box(self):
        old = default_edit(100);old.pop('camera_overlay')
        old['overlay'].update(x=.35,y=.2,fields=['capture','playback'])
        migrated = validate_edit(old,100)
        self.assertEqual(migrated['overlay'],old['overlay'])
        self.assertEqual(migrated['camera_overlay'],default_camera_overlay())
        self.assertNotIn('camera_overlay',old)
        for key,value in [('fields',['slowdown']),('enabled',1),('x',2),('layout','diagonal')]:
            invalid=deepcopy(migrated);invalid['camera_overlay'][key]=value
            with self.assertRaises(ValueError):validate_edit(invalid,100)

    def test_left_and_center_placement_put_the_cut_at_the_requested_ramp_phase(self):
        edit = split_at(default_edit(200), 100)
        edit['sections'][0]['fps'] = 120
        edit['sections'][1].update(fps=30, ramp=.8)
        # Smoothstep consumes 41.25 frames in the first half and 18.75 in the second.
        for place, before, left_frames, cut_rate in [('left', .8, 60, 30), ('center', .4, 41.25, 75), ('right', 0, 0, 120)]:
            with self.subTest(place=place):
                edit['sections'][1]['ramp_place'] = place
                plan = Timeline(edit, 0, 199)
                ramp_start = (100 - left_frames) / 120
                cut = ramp_start + before
                self.assertAlmostEqual(plan.time_for_source(100), cut)
                self.assertEqual(plan.sample(cut)[0], 100)
                self.assertAlmostEqual(plan.sample(cut)[1], cut_rate)
                self.assertAlmostEqual(plan.sample(ramp_start + .4)[1], 75)
                self.assertAlmostEqual(plan.sample(ramp_start + .8)[1], 30)
                self.assertAlmostEqual(plan.duration, ramp_start + .8 + (40 + left_frames) / 30)

    def test_curve_shapes_have_correct_rates_and_frame_consumption(self):
        edit = split_at(default_edit(200), 100)
        edit['sections'][0]['fps'] = 90
        edit['sections'][1].update(fps=30, ramp=1, ramp_place='left')
        for curve, frames, quarter_rate in [('smooth', 60, 80.625), ('linear', 60, 75), ('ease_in', 70, 86.25), ('ease_out', 50, 63.75)]:
            edit['sections'][1]['ramp_curve'] = curve
            plan = Timeline(edit, 0, 199)
            ramp_start = (100 - frames) / 90
            self.assertAlmostEqual(plan.sample(ramp_start + .25)[1], quarter_rate)
            self.assertAlmostEqual(plan.time_for_source(100), ramp_start + 1)
            self.assertAlmostEqual(plan.duration, ramp_start + 1 + 100 / 30)

    def test_trim_and_plain_split_preserve_all_transition_placements(self):
        for place in ('left', 'right', 'center'):
            edit = split_at(default_edit(200), 100)
            edit['sections'][0]['fps'] = 120
            edit['sections'][1].update(fps=30, ramp=.8, ramp_place=place)
            full = Timeline(edit, 0, 199)
            split = Timeline(split_at(split_at(edit, 85), 115), 0, 199)
            self.assertAlmostEqual(split.duration, full.duration)
            np.testing.assert_allclose(list(split.samples()), list(full.samples()), atol=1e-8)
            trimmed = Timeline(edit, 90, 125)
            offset = full.time_for_source(90)
            for t in np.linspace(0, trimmed.duration - 1e-8, 40):
                np.testing.assert_allclose(trimmed.sample(t), full.sample(t + offset), atol=1e-7)

    def test_overlapping_ramps_shorten_without_losing_source_intervals(self):
        edit = split_at(split_at(default_edit(210), 100), 110)
        for section, fps in zip(edit['sections'], [90, 30, 60]): section['fps'] = fps
        edit['sections'][1].update(ramp=10, ramp_place='center')
        edit['sections'][2].update(ramp=10, ramp_place='center')
        plan = Timeline(edit, 0, 209)
        self.assertEqual(len(plan.ramp_limits), 2)
        # Every original frame still has an ordered, invertible time in the map.
        times = [plan.time_for_source(f) for f in range(210)]
        self.assertTrue(np.all(np.diff(times) > 0))
        for frame, t in enumerate(times): self.assertEqual(plan.sample(t)[0], frame)
        a, b = plan.transitions
        self.assertLessEqual(a['right_frames'] + b['left_frames'], 10 + 1e-8)
        self.assertLessEqual(a['left_frames'], 100)
        self.assertLessEqual(b['right_frames'], 100)

    def test_centered_ramp_across_skipped_footage_has_no_skipped_frames_or_rate_jump(self):
        edit = three_sections(); edit['sections'][1]['keep'] = False
        edit['sections'][2].update(fps=30, ramp=.8, ramp_place='center')
        plan = Timeline(edit, 0, 299)
        cut = plan.time_for_source(200)
        self.assertEqual(plan.sample(cut - 1e-6)[0], 99)
        self.assertEqual(plan.sample(cut)[0], 200)
        self.assertAlmostEqual(plan.sample(cut)[1], 75)
        self.assertTrue(all(f < 100 or f >= 200 for f, rate in plan.samples()))

    def test_invalid_placement_curve_and_fields_are_rejected(self):
        for key, value in [('ramp_place', 'before-ish'), ('ramp_curve', 'unknown')]:
            edit = three_sections(); edit['sections'][1][key] = value
            with self.assertRaises(ValueError): validate_edit(edit, 300)
        for fields in [['capture', 'capture'], ['speed'], None, 'capture']:
            edit = three_sections(); edit['overlay']['fields'] = fields
            with self.assertRaises(ValueError): validate_edit(edit, 300)

    def test_fast_section_after_long_slow_lead_in_seeks_to_exact_source_frame(self):
        edit = split_at(default_edit(120), 49)
        edit['sections'][0]['fps'] = .1
        edit['sections'][1]['fps'] = 1_000_000
        plan = Timeline(edit, 0, 119)
        for frame in range(49, 120):
            self.assertEqual(plan.sample(plan.time_for_source(frame))[0], frame)

    def test_shortened_center_transition_seeks_to_first_frame_after_a_skip(self):
        edit = split_at(split_at(default_edit(166), 75), 153)
        edit['sections'][0]['fps'] = 1000
        edit['sections'][1]['keep'] = False
        edit['sections'][2].update(fps=5, ramp=100, ramp_place='center')
        plan = Timeline(edit, 0, 165)
        self.assertEqual(plan.sample(plan.time_for_source(153))[0], 153)
        for frame in [*range(75), *range(153, 166)]:
            self.assertEqual(plan.sample(plan.time_for_source(frame))[0], frame)

    def test_compact_card_is_bounded_at_extreme_positions_and_tiny_resolutions(self):
        overlay = default_edit()['overlay']; overlay.update(enabled=True, x=1, y=1)
        text = overlay_text(overlay, 10, 30, 1000)
        for height, width in [(640, 1024), (80, 120), (2, 2)]:
            source = np.zeros((height, width, 3), dtype=np.uint8)
            result, (x, y, w, h) = apply_fps_overlay(source, text, overlay)
            self.assertEqual((x + w, y + h), (width, height))
            self.assertEqual(result.shape, source.shape)
            if width == 1024:
                self.assertLess(w, 230); self.assertLess(h, 60)

    def test_overlay_scales_positions_and_does_not_mutate_source(self):
        overlay = default_edit()['overlay']; overlay.update(enabled=True,x=1,y=1,size=5)
        for dtype,shape in [(np.uint16,(240,320)),(np.uint8,(240,320,3))]:
            source = np.full(shape,100,dtype=dtype); original=source.copy()
            result, rect = apply_fps_overlay(source,'100× slower\n1,000 fps\n10 fps playback',overlay)
            self.assertEqual(result.dtype,dtype); self.assertEqual(result.shape,shape)
            x,y,w,h=rect
            self.assertEqual(x+w,320); self.assertEqual(y+h,240)
            self.assertFalse(np.array_equal(result,source)); np.testing.assert_array_equal(source,original)


class ViewerProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.source=self.root/'source.cine'; write_cine(self.source)
        self.project=self.root/'edit.pcaedit'

    def test_round_trip_restores_timing_overlay_levels_and_clip(self):
        edit=split_at(default_edit(8),4);edit['sections'][0]['fps']=60
        edit['overlay'].update(enabled=True,x=.7,y=.8,layout='straight',decimals=True)
        edit['camera_overlay'].update(enabled=True,x=.2,y=.6,color='#ffdf8c',fields=['capture','resolution'])
        grade=default_grade();grade['gamma']=1.5
        save_project(self.project,self.source,1,6,edit,grade,SETTINGS)
        saved=load_project(self.project,self.source,8)
        self.assertEqual(saved['edit'],edit);self.assertEqual(saved['grade'],grade)
        self.assertEqual(saved['clip'],[1,6]);self.assertEqual(saved['image_settings'],SETTINGS)

    def test_different_cine_and_corrupt_settings_rejected_before_application(self):
        save_project(self.project,self.source,0,7,default_edit(8),default_grade(),SETTINGS)
        other=self.root/'other.cine';write_cine(other,start_second=1700001234)
        with self.assertRaisesRegex(ValueError,'different Cine'):load_project(self.project,other,8)
        payload=json.loads(self.project.read_text());payload['edit']['sections'][0]['fps']=-3
        self.project.write_text(json.dumps(payload))
        with self.assertRaises(ValueError):load_project(self.project,self.source,8)

    def test_old_recipe_migrates_timing_and_data_without_changing_the_saved_file(self):
        edit=split_at(default_edit(8),4);edit['sections'][1].update(fps=15,ramp=.1)
        edit['version']=1
        for section in edit['sections']:
            section.pop('ramp_place');section.pop('ramp_curve')
        edit['overlay'].pop('fields');edit['overlay']['mode']='effective'
        save_project(self.project,self.source,0,7,edit,default_grade(),SETTINGS)
        before=self.project.read_bytes()
        result=load_project(self.project,self.source,8)['edit']
        self.assertEqual(result['version'],3)
        self.assertEqual(result['sections'][1]['ramp_place'],'right')
        self.assertEqual(result['sections'][1]['ramp_curve'],'smooth')
        self.assertEqual(result['overlay']['fields'],['capture','slowdown','playback'])
        self.assertEqual(self.project.read_bytes(),before)
        np.testing.assert_allclose(list(Timeline(edit,0,7).samples()),list(Timeline(result,0,7).samples()))
