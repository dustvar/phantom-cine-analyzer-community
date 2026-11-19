"""Check lattice density, magnitude encoding and the styling controls used by both BOS views."""
import os
import unittest
from unittest.mock import patch

import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QColor
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from bos_frames import validate_bos_settings
from bos_processing import BOSField, render_bos
from bos_vector_controls import VectorAppearance
from bos_vectors import VectorStyle, draw_vector_field, sample_vectors


class VectorStyleTests(unittest.TestCase):
    def field(self, width=640, height=400, step=4):
        x = np.arange(8, width-8, step, dtype=np.float32)
        y = np.arange(8, height-8, step, dtype=np.float32)
        dims = (len(y), len(x))
        return BOSField((0, 0, width, height), x, y, np.full(dims,.36,np.float32),
                        np.zeros(dims,np.float32), np.ones(dims,bool), np.ones(dims,np.float32),
                        step, np.zeros(dims,np.uint8))

    def test_old_jobs_and_invalid_styles(self):
        legacy = validate_bos_settings({'detail':True,'show_vectors':True})
        self.assertEqual(VectorStyle.from_settings(legacy), VectorStyle())
        for key, value in [('vector_thickness', True), ('vector_thickness', 9),
                           ('vector_color','#123'), ('vector_high_color','blue'),
                           ('vector_shape','ring'), ('vector_encoding','rainbow')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_bos_settings({key:value})
        self.assertEqual(validate_bos_settings({'vector_density':'extra_dense'})['vector_density'], 'extra_dense')

    def test_extra_dense_interleaves_measured_sites_and_respects_acquisition_grid(self):
        f = self.field()
        dense = sample_vectors(f, .4, 'dense')
        extra = sample_vectors(f, .4, 'extra_dense')
        ordinary_sites = set(zip(dense.rows,dense.cols))
        extra_sites = set(zip(extra.rows,extra.cols))
        self.assertTrue(ordinary_sites <= extra_sites)
        self.assertAlmostEqual(len(extra.rows)/len(dense.rows),2.,delta=.08)
        self.assertEqual(len(extra.rows),len(extra_sites))
        self.assertLess(extra.full_length,dense.full_length)
        f.valid[extra.rows[0],extra.cols[0]] = False
        reduced = sample_vectors(f, .4, 'extra_dense')
        self.assertNotIn((extra.rows[0],extra.cols[0]),zip(reduced.rows,reduced.cols))
        coarse = self.field(step=16)
        for density in ('dense','extra_dense'):
            same = sample_vectors(coarse,.4,density)
            np.testing.assert_array_equal(same.x,coarse.x[same.cols])
            self.assertEqual(len(same.x),len(coarse.x)*len(coarse.y))

    def test_gradient_and_threshold_use_magnitude_with_identical_single_tip_geometry(self):
        f = self.field()
        for a,b,value in [(0,52,.10),(52,104,.25),(104,160,.50)]:
            f.dx[:,a:b] = value
        samples = sample_vectors(f,.4,'sparse')
        labels = [(60,.10),(300,.25),(520,.50)]
        def colors_at_sites(style):
            canvas=np.full((400,640,3),115,np.uint8)
            draw_vector_field(canvas,f,samples,None,style)
            values=[]
            for x, expected in labels:
                idx = np.argmin(np.square(samples.x-x)+np.square(samples.y-180))
                self.assertAlmostEqual(samples.dx[idx],expected,places=4)
                values.append(canvas[int(samples.y[idx]),int(samples.x[idx])])
            return values
        colors=colors_at_sites(VectorStyle(encoding='threshold',color='#2375af',
                                           high_color='#ef9015',thickness=4))
        self.assertEqual([tuple(rgb) for rgb in colors],[(35,117,175),(35,117,175),(239,144,21)])
        grays=colors_at_sites(VectorStyle(encoding='grayscale',thickness=4))
        self.assertTrue(all(c[0]==c[1]==c[2] for c in grays))
        self.assertGreater(grays[1][0],grays[0][0])
        self.assertEqual(grays[2][0],255)
        self.assertTrue(samples.saturated.any())
        constant = np.full((400,640,3),115,np.uint8)
        kwargs=dict(show_vectors=True,vector_background='footage',limit=.4,vector_density='sparse')
        single=render_bos(constant,f,**kwargs,vector_style=VectorStyle(encoding='threshold',high_color='#f0fefc'))
        double=render_bos(constant,f,**kwargs)
        np.testing.assert_array_equal(single[:350,:205],double[:350,:205])
        self.assertGreater(np.sum(single[:350,440:]!=double[:350,440:]),30)

    def test_each_shape_changes_geometry_and_thickness_changes_ink(self):
        f = self.field()
        samples = sample_vectors(f,.4,'sparse')
        images=[]
        for shape, thickness in [('open',1),('filled',1),('needle',1),('open',4)]:
            canvas=np.full((400,640,3),115,np.uint8)
            draw_vector_field(canvas,f,samples,None,VectorStyle(shape=shape,thickness=thickness))
            images.append(canvas)
        self.assertTrue(all(not np.array_equal(images[i],images[j])
                            for i in range(4) for j in range(i)))
        self.assertGreater(np.sum(images[-1]!=115),np.sum(images[0]!=115))
        center_ink = lambda image: np.sum(np.all(image == [240,254,252], axis=2))
        self.assertGreater(center_ink(images[-1]),2*center_ink(images[0]))

    def test_controls_restore_without_signals_and_keep_custom_colors_when_switching_modes(self):
        app = QApplication.instance() or QApplication([])
        ui=VectorAppearance()
        self.addCleanup(ui.close)
        spy=QSignalSpy(ui.changed)
        options=dict(vector_color='#2a5f89',vector_high_color='#be4618',vector_shape='filled',
                     vector_encoding='threshold',vector_thickness=4)
        ui.set_settings(options)
        self.assertEqual(ui.settings(),options)
        self.assertEqual(spy.count(),0)
        ui.toggle.click()
        ui.encoding.setCurrentIndex(ui.encoding.findData('grayscale'))
        self.assertTrue(ui.color.isHidden() and ui.high_color.isHidden())
        ui.encoding.setCurrentIndex(ui.encoding.findData('threshold'))
        self.assertFalse(ui.color.isHidden() or ui.high_color.isHidden())
        self.assertEqual(ui.settings(),options)
        with patch('bos_vector_controls.QColorDialog.getColor',return_value=QColor('#124442')):
            ui.color.click()
        self.assertEqual(ui.settings()['vector_color'],'#124442')
        self.assertEqual(spy.count(),3)
        app.processEvents()
