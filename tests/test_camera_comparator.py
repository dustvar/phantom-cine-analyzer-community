import os
import sys
import json
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
from unittest.mock import patch

import cv2
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app-source/modules/trackmeasure'))
from camera_comparator import (PROFILES,SourceSpec,Geometry,SamplingPlan,available_modes,
    geometry,integrate_pixels,render_pair,linear_frame,default_settings,validate_settings,
    save_comparison,load_comparison)
from camera_comparator_media import ComparisonSession,export_comparison
from cine_metadata import CineHandler
from video_export import ExportCancelled
from test_cine_metadata import write_cine


class CameraGeometryTests(unittest.TestCase):
    def test_identity_preserves_every_pixel(self):
        source=SourceSpec(1280,800,'TMX7510',18.5,1,10000)
        mode=available_modes('TMX7510')[0]
        g=geometry(source,PROFILES['TMX7510'],mode)
        image=np.arange(1280*800,dtype=np.float32).reshape(800,1280)/(1280*800)
        output,valid=integrate_pixels(image,g)
        np.testing.assert_array_equal(output,image);self.assertTrue(valid.all())
        self.assertEqual(g.retained_fraction,1);self.assertEqual(g.available_fraction,1)

    def test_binning_integrates_linear_checkerboard_before_gamma(self):
        image=(np.indices((4,4)).sum(axis=0)%2).astype(np.float32)
        g=Geometry(2,2,2,0,0,4,4,True)
        out,valid=integrate_pixels(image,g)
        np.testing.assert_array_equal(out,np.full((2,2),.5));self.assertTrue(valid.all())
        original=image.copy();_,display=render_pair(image,image,g,2)
        self.assertTrue(np.all(display==180));np.testing.assert_array_equal(image,original)

    def test_fractional_pixel_footprints_use_area_not_nearest_neighbor(self):
        image=np.tile(np.arange(4,dtype=np.float32),(2,1))
        g=Geometry(2,1,1.5,.25,.25,4,2,False)
        out,valid=integrate_pixels(image,g)
        np.testing.assert_allclose(out,[[.5,2]],atol=1e-6);self.assertTrue(valid.all())

    def test_same_optics_maps_different_pixel_pitch(self):
        source=SourceSpec(1280,800,'TMX7510',18.5,1,10000)
        mode=available_modes('T4040')[0]
        g=geometry(source,PROFILES['T4040'],mode)
        self.assertAlmostEqual(g.step,9.27/18.5)
        self.assertAlmostEqual(g.right-g.left,2560*9.27/18.5)
        self.assertLess(g.available_fraction,1)

    def test_binned_source_cannot_regain_spatial_detail(self):
        source=SourceSpec(640,384,'TMX7510',18.5,2,10000)
        mode=available_modes('T3610')[0]
        g=geometry(source,PROFILES['T3610'],mode)
        self.assertEqual(g.step,.5);self.assertLess(g.available_fraction,1)

    def test_crop_and_binning_have_different_physical_coverage(self):
        source=SourceSpec(1280,800,'TMX7510',18.5,1,100000)
        modes=available_modes('T2410')
        standard=next(m for m in modes if m['id']=='1280x192-b1')
        binned=next(m for m in modes if m['id']=='640x384-b2')
        a=geometry(source,PROFILES['T2410'],standard);b=geometry(source,PROFILES['T2410'],binned)
        self.assertAlmostEqual(a.retained_fraction,.24);self.assertAlmostEqual(b.retained_fraction,.96)
        self.assertTrue(b.mono)

    def test_source_offset_and_reaim_are_applied_in_physical_coordinates(self):
        source=SourceSpec(1024,640,'TMX7510',18.5,1,94000,center_x_um=10*18.5)
        mode=next(m for m in available_modes('T3610') if m['id']=='1280x320-b1')
        g=geometry(source,PROFILES['T3610'],mode,aim_x=15,aim_y=20)
        self.assertEqual(g.left,-123);self.assertEqual(g.top,180)

    def test_outside_source_is_hatched_and_partial_pixels_not_fabricated(self):
        image=np.full((4,4),.9,np.float32)
        g=Geometry(4,4,1,-.5,-.5,4,4,False)
        _,valid=integrate_pixels(image,g)
        self.assertEqual(valid.sum(),9)
        masked,target=render_pair(image,image,g)
        self.assertTrue(np.all(target[0,:,0]<100));self.assertEqual(target[1,1,0],230)
        self.assertFalse(np.shares_memory(masked,image))

    def test_red_bayer_pattern_stays_red(self):
        from types import SimpleNamespace
        raw=np.zeros((8,8),np.uint16);raw[::2,::2]=4095
        md=SimpleNamespace(CFA='rg/gb',RealBPP=12,BlackLevel=0,WhiteLevel=4095,WBGain=[1,1])
        color=linear_frame(raw,md)
        np.testing.assert_allclose(color[2:-2,2:-2,0],1)
        np.testing.assert_array_equal(color[2:-2,2:-2,2],0)

    def test_binned_color_is_monochrome(self):
        image=np.full((4,4,3),(1.,0.,0.),np.float32)
        g=Geometry(2,2,2,0,0,4,4,True)
        out,_=integrate_pixels(image,g)
        np.testing.assert_allclose(out,.2126)

    def test_unknown_binning_and_impossible_geometry_are_rejected(self):
        for source in [SourceSpec(100,100,'TMX7510',18.5,0,1000),SourceSpec(1280,800,'TMX7510',18.5,2,1000),SourceSpec(1280,800,'TMX7510',9.27,1,1000)]:
            with self.assertRaises(ValueError):source.validate()

    def test_camera_options_and_caps_are_separate(self):
        self.assertNotIn('1280x32-b1-FAST',[m['id'] for m in available_modes('TMX7510')])
        self.assertEqual(max(m['max_fps'] for m in available_modes('TMX7510','FAST95')),1750000)
        self.assertEqual(max(m['max_fps'] for m in available_modes('T2110','E225')),225000)
        self.assertEqual(max(m['max_fps'] for m in available_modes('T2410','FAST')),558330)
        self.assertTrue(all(p['sources'] and p['verified_on'] for p in PROFILES.values()))


class CameraSamplingTests(unittest.TestCase):
    def test_identity_and_exact_integer_ratios(self):
        times=np.arange(100)/100000
        for rate,step in [(100000,1),(50000,2),(25000,4)]:
            p=SamplingPlan(times,rate)
            np.testing.assert_array_equal(p.indices,np.arange(0,100,step));self.assertFalse(p.approximate)

    def test_time64_quantization_at_1750000_fps_preserves_identity(self):
        times=np.round(np.arange(100)/1750000*2**32)/2**32
        p=SamplingPlan(times,1750000)
        np.testing.assert_array_equal(p.indices,np.arange(100))

    def test_noninteger_ratio_has_no_drift(self):
        p=SamplingPlan(np.arange(10000)/100000,30000)
        self.assertEqual(len(p.indices),3000)
        self.assertEqual(set(np.diff(p.indices)),{3,4})
        self.assertLessEqual(p.max_error,1/100000/2+1e-12)
        self.assertTrue(p.approximate)
        np.testing.assert_allclose(p.requested,np.arange(3000)/30000)

    def test_phase_selects_other_half_of_recording(self):
        times=np.arange(12)/100000
        p=SamplingPlan(times,50000,.5)
        np.testing.assert_array_equal(p.indices,np.arange(1,12,2))
        self.assertIsNone(p.pair(0,'event_sync')[1])
        self.assertEqual(p.pair(1,'event_sync')[1],1)

    def test_playback_modes_and_end_hold(self):
        p=SamplingPlan(np.arange(8)/1000,500)
        self.assertEqual(p.pair(2,'same_fps'),(2,4,False))
        self.assertEqual(p.pair(5,'same_fps'),(5,6,True))
        self.assertEqual(p.pair(5,'event_sync'),(5,4,False))

    def test_irregular_timestamps_do_not_invent_captures(self):
        times=np.array([0,1,2,6,7,8,9],float)/1000
        p=SamplingPlan(times,1000)
        self.assertTrue(p.irregular);self.assertEqual(p.missing,3)
        self.assertEqual(len(np.unique(p.indices)),len(p.indices))
        np.testing.assert_array_equal(p.indices,np.arange(len(times)))

    def test_invalid_timestamps_and_higher_fps_are_rejected(self):
        for times,rate in [([0,0],100),([0,np.nan],100),([1,0],100),([0,.01],101),([],100)]:
            with self.assertRaises(ValueError):SamplingPlan(times,rate)
        for phase in [-.1,1,float('nan')]:
            with self.assertRaises(ValueError):SamplingPlan([0,.01],100,phase)

    def test_single_frame_is_supported(self):
        p=SamplingPlan([0],1000,fallback_fps=2000)
        self.assertEqual(p.indices.tolist(),[0])


class CameraMediaTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'source.cine'
        write_cine(self.path,frames=12,width=1280,height=128)
        self.session=ComparisonSession(self.path);self.addCleanup(self.session.close)
        self.settings=default_settings(12)
        self.settings.update(target='T2110',mode='1280x128-b1',target_fps=1000.)
        self.settings['source']=dict(width=1280,height=128,profile_id='TMX7510',pitch_um=18.5,binning=1,capture_fps=2000.,exposure_us=416.666,center_x_um=0.,center_y_um=0.)

    def test_precise_time_reader_avoids_large_epoch_subtraction(self):
        expected=np.array([round(i/2000*2**32)/2**32 for i in range(12)])
        np.testing.assert_array_equal(self.session.timestamps,expected)

    def test_timestamp_block_length_is_checked(self):
        self.session.reader._timestamp_block_info['blocksize']=8
        with self.assertRaisesRegex(ValueError,'Truncated'):self.session.reader.relative_capture_timestamps()

    def test_timestamp_block_not_reused_for_another_cine(self):
        other=self.path.with_name('no-times.cine');write_cine(other)
        data=bytearray(other.read_bytes());position=84+9344+1608
        struct.pack_into('<H',data,position+4,7778);other.write_bytes(data)
        self.session.reader.load_cine(other)
        self.assertIsNone(self.session.reader.relative_capture_timestamps())

    def test_metadata_decimation_does_not_override_stored_cadence(self):
        self.session.metadata.fDecimation=2
        c=self.session.build(self.settings)
        self.assertAlmostEqual(c.plan.available_fps,2000,places=2)
        self.assertTrue(any('metadata predicts' in n for n in c.notes))

    def test_exposure_warning_and_feature_geometry(self):
        s=deepcopy(self.settings);s['source']['exposure_us']=.05
        c=self.session.build(s)
        self.assertTrue(any('EXPOSURE INCOMPATIBLE' in n for n in c.notes))
        self.assertEqual(c.geo.step,1)

    def test_preview_and_export_do_not_modify_source_or_settings(self):
        original=self.path.read_bytes();settings=deepcopy(self.settings)
        c=self.session.build(settings);c.render(3);c.export_frame(3,'comparison')
        self.assertEqual(self.path.read_bytes(),original);self.assertEqual(settings,self.settings)
        self.assertLessEqual(len(self.session.cache),3)

    def test_setting_validation_rejects_unsupported_modes_and_nan(self):
        for key,value in [('target_fps',2001),('mode','invented'),('phase',float('nan')),('first',-1),('last',100),('playback_mode','invalid')]:
            s=deepcopy(self.settings);s[key]=value
            with self.assertRaises(ValueError):self.session.build(s)

    def test_preset_roundtrip_source_identity_and_profile_version(self):
        path=self.path.with_suffix('.pcacompare');c=self.session.build(self.settings)
        save_comparison(path,self.path,c.settings,c.profile)
        self.assertEqual(load_comparison(path,self.path,12,1280,128),c.settings)
        payload=json.loads(path.read_text());payload['profile_snapshot']['verified_on']='old';path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError,'specifications have changed'):load_comparison(path,self.path,12,1280,128)
        save_comparison(path,self.path,c.settings,c.profile)
        other=self.path.with_name('other.cine');write_cine(other,frames=3)
        with self.assertRaisesRegex(ValueError,'different source'):load_comparison(path,other,3,32,24)
        with self.assertRaises(ValueError):save_comparison(self.path,self.path,c.settings,c.profile)

    def test_ordinary_video_requires_manual_acquisition_information(self):
        path=self.path.with_suffix('.avi');writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'MJPG'),30,(128,64))
        for i in range(4):writer.write(np.full((64,128,3),100,np.uint8))
        writer.release();session=ComparisonSession(path);self.addCleanup(session.close)
        defaults=session.source_defaults()
        self.assertEqual(defaults['capture_fps'],0);self.assertEqual(defaults['binning'],0)
        settings=default_settings(4);settings['source']=defaults
        with self.assertRaises(ValueError):session.build(settings)
        defaults.update(profile_id='TMX7510',pitch_um=18.5,binning=1,capture_fps=1000.)
        settings.update(target='T2110',mode='1280x128-b1',target_fps=500.)
        c=session.build(settings);self.assertEqual(c.plan.indices.tolist(),[0,2])

    def test_decoded_exports_match_preview_in_each_playback_mode(self):
        for playback in ['same_fps','event_sync']:
            s=deepcopy(self.settings);s['playback_mode']=playback
            c=self.session.build(s)
            for layout in ['target','comparison']:
                for extension in ['.mp4','.mov','.avi']:
                    with self.subTest(playback=playback,layout=layout,extension=extension):
                        output=self.path.with_suffix(extension)
                        export_comparison(self.path,output,s,layout)
                        cap=cv2.VideoCapture(str(output))
                        self.assertEqual(cap.get(cv2.CAP_PROP_FRAME_COUNT),c.frame_count(layout))
                        self.assertAlmostEqual(cap.get(cv2.CAP_PROP_FPS),30)
                        for i in range(c.frame_count(layout)):
                            ok,actual=cap.read();self.assertTrue(ok)
                            actual=cv2.cvtColor(actual,cv2.COLOR_BGR2RGB)
                            expected=c.export_frame(i,layout)
                            self.assertEqual(actual.shape,expected.shape)
                            self.assertLess(np.abs(actual.astype(float)-expected).mean(),4)
                        self.assertFalse(cap.read()[0]);cap.release()

    def test_cancel_and_encoder_failure_preserve_destination(self):
        output=self.path.with_suffix('.mp4');output.write_bytes(b'existing export')
        event=threading.Event()
        with self.assertRaises(ExportCancelled):
            export_comparison(self.path,output,self.settings,cancel=event,progress=lambda *_:event.set())
        self.assertEqual(output.read_bytes(),b'existing export')
        with patch('camera_comparator_media.subprocess.Popen',side_effect=OSError('Encoder unavailable')):
            with self.assertRaises(OSError):export_comparison(self.path,output,self.settings)
        self.assertEqual(output.read_bytes(),b'existing export')
        self.assertFalse(list(output.parent.glob('.pca-compare-export-*')))


if __name__=='__main__':unittest.main()
