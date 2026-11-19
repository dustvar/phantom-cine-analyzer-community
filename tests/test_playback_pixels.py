import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app-source/modules/trackmeasure'))
from pyphantom_image.imagetools import ImageTools, ImageProcessingConfig
from viewer_processing import render_cine_image
from video_export import video_frame


class PlaybackPixelTests(unittest.TestCase):
    def test_color_lookup_pipeline_matches_camera_pipeline_for_all_bayer_patterns(self):
        for bpp in (8, 10, 12, 16):
            for cfa in ('gb/rg', 'rg/gb', 'gr/gb', 'bg/gr'):
                for enabled in (False, True):
                    with self.subTest(bpp=bpp, cfa=cfa, tone=enabled):
                        maximum = 2**bpp - 1
                        tone = [.3, .1, .7, .6]
                        md = SimpleNamespace(CFA=cfa, RealBPP=bpp, WBGain=(1.13, 1.51),
                            BlackLevel=12, WhiteLevel=maximum-8, cmCalib=[], fTone=tone)
                        settings = dict(image_brightness=-35, image_contrast=1.16,
                            image_gamma=2.222, toggle_ftone=enabled, raw_enabled=False)
                        frame = np.random.default_rng(14).integers(0, maximum+1, (32, 96),
                            dtype=np.uint8 if bpp == 8 else np.uint16)
                        frame[10, 13:18] = frame[20, -1] = 255 << (bpp - 8)
                        original = frame.copy()
                        tools = ImageTools()
                        reference = tools.apply_dmap_correction(frame.astype(np.float32), cfa, bpp)
                        reference = tools.apply_color_pipeline(reference, ImageProcessingConfig(
                            r_gain=md.WBGain[0], b_gain=md.WBGain[1], brightness=-35,
                            contrast=1.16, gamma=2.222, cfa=cfa, bpp=bpp,
                            ftone=tone if enabled else None, cm_calib=[], black_offset=12,
                            white_level=maximum-8))
                        np.testing.assert_array_equal(render_cine_image(frame, md, settings, ImageTools()), reference)
                        np.testing.assert_array_equal(frame, original)

    def test_tone_curve_remains_relative_to_each_frames_extrema(self):
        tools = ImageTools()
        tone = [.3, .1, .7, .6]
        md = SimpleNamespace(CFA='Mono', RealBPP=12, WBGain=(1, 1), BlackLevel=0,
                             WhiteLevel=4095, cmCalib=[], fTone=tone)
        settings = dict(image_brightness=0, image_contrast=1., image_gamma=1.,
                        toggle_ftone=True, raw_enabled=False)
        for low, high in ((0, 3000), (1200, 1800), (400, 400), (0, 3000)):
            frame = np.linspace(low, high, 600).astype(np.uint16).reshape(20, 30)
            reference = tools.apply_gamma(frame.astype(np.float32), 1., 12)
            reference = tools.apply_ftone(reference, tone)
            reference = tools.stretch_image_rng(reference, 16, 0, 4095)
            np.testing.assert_array_equal(render_cine_image(frame, md, settings, tools), reference)

    def test_monitor_conversion_matches_integer_rounding_for_every_16bit_value(self):
        pixels = np.arange(65536, dtype=np.uint16).reshape(256, 256)
        expected = ((pixels.astype(np.uint32) + 128) // 257).astype(np.uint8)
        actual = video_frame(pixels)
        for channel in range(3):
            np.testing.assert_array_equal(actual[:, :, channel], expected)

    def test_mono_display_matches_full_camera_pipeline_and_preserves_raw_data(self):
        for bpp in (8, 10, 12, 16):
            maximum = 2**bpp - 1
            dtype = np.uint8 if bpp == 8 else np.uint16
            for tone in ([.37, .74, 0, 0], [.3, .1, .7, .6]):
                with self.subTest(bpp=bpp, tone=tone):
                    md = SimpleNamespace(CFA='Mono', RealBPP=bpp, WBGain=(1, 1),
                        BlackLevel=12, WhiteLevel=maximum-8, cmCalib=[], fTone=tone)
                    settings = dict(image_brightness=-35, image_contrast=1.16, image_gamma=2.222,
                        toggle_ftone=True, raw_enabled=False)
                    frame = np.random.default_rng(5).integers(0, maximum+1, (32, 96), dtype=dtype)
                    frame[10, 13:15] = frame[20, -1] = 255 << (bpp - 8)
                    original = frame.copy()
                    tools = ImageTools()
                    ref = tools.apply_dmap_correction(frame.astype(np.float32), 'Mono', bpp)
                    ref = tools.apply_brightness(ref, settings['image_brightness'] / 2**bpp)
                    ref = tools.apply_contrast(ref, settings['image_contrast'])
                    ref = tools.apply_gamma(ref, settings['image_gamma'], bpp)
                    ref = tools.apply_ftone(ref, tone)
                    ref = tools.stretch_image_rng(ref, 16, md.BlackLevel, md.WhiteLevel)
                    np.testing.assert_array_equal(render_cine_image(frame, md, settings, ImageTools()), ref)
                    np.testing.assert_array_equal(frame, original)
