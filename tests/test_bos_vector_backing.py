"""The arrow count, contrast backing and independent layer fades use measured sites."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import cv2
import numpy as np

from bos_frames import default_bos_settings
from bos_preview import prepare_bos_preview
from bos_processing import BOSField, magnitude_backing, render_bos
from bos_vector_cache import HEADER, SITE, paint_vectors
from bos_vectors import DENSITY_MODES, VectorStyle, sample_vectors, vector_capacity
from test_bos_motion import texture, write_frames


def example(step=4):
    width, height = 520, 300
    margin = 10
    x = np.arange(margin, width-margin, step, dtype=np.float32)
    y = np.arange(margin, height-margin, step, dtype=np.float32)
    shape = (len(y), len(x))
    return BOSField((0,0,width,height), x, y, np.full(shape,.40,np.float32),
                    np.zeros(shape,np.float32), np.ones(shape,bool), np.ones(shape,np.float32),
                    step, np.zeros(shape,np.uint8))


class VectorContrastTests(unittest.TestCase):
    def test_numeric_limits_count_unique_measurements_for_region_and_grid(self):
        for step in (4,9,16):
            field = example(step)
            field.dx[:] = .12  # Uniform, visible strokes fully inside the measurement region.
            counts = {}
            for mode in DENSITY_MODES:
                s = sample_vectors(field, .45, mode)
                sites = set(zip(s.rows,s.cols))
                self.assertEqual(len(sites),len(s.x))
                self.assertEqual(len(sites),vector_capacity(*field.roi[2:],step,mode,margin=10))
                counts[mode] = sites
            self.assertTrue(counts['extra_dense'] <= counts['ultra_dense'])
            if step == 4:
                self.assertAlmostEqual(len(counts['ultra_dense'])/len(counts['extra_dense']),2.,delta=.05)
            if step == 16:
                self.assertEqual(len(counts['ultra_dense']),field.valid.size)
            field.valid[:5,:] = False
            masked = sample_vectors(field,.45,'ultra_dense')
            self.assertTrue(set(zip(masked.rows,masked.cols)) < counts['ultra_dense'])

    def test_inverse_ink_has_contrast_and_unreliable_holes_stay_original(self):
        f = example()
        f.dx[:,:42] = .10
        f.dx[:,42:84] = .23
        f.valid[4:12,4:12] = False
        f.dx[~f.valid] = np.nan
        rng = np.random.default_rng(14)
        raw = rng.integers(40,170,(300,520,3),dtype=np.uint8)
        plate = magnitude_backing(raw,f,.4,False)
        ink = render_bos(raw, f, show_vectors=True, vector_density='ultra_dense', vector_background='footage',
                         limit=.4, vector_style=VectorStyle(thickness=2,outline=False))
        samples = sample_vectors(f,.4,'ultra_dense')
        for x,expected in [(100,'bright'),(258,'dark'),(420,'dark')]:
            i = np.argmin((samples.x-x)**2+(samples.y-155)**2)
            xx,yy=int(samples.x[i]),int(samples.y[i])
            shade = int(ink[yy,xx,0]); backing=int(plate[yy,xx])
            self.assertGreater(abs(shade-backing),85)
            self.assertEqual(shade>backing,expected=='bright')
        np.testing.assert_array_equal(ink[27:42,27:42],raw[27:42,27:42])
        self.assertEqual(int(plate[32,32]),0)
        self.assertLess(plate[145,100],plate[145,258])
        self.assertLess(plate[145,258],plate[145,420])

    def test_both_fades_interpolate_complete_compositions_offline(self):
        with TemporaryDirectory() as temp:
            path = Path(temp)/'dots.cine'
            dot = texture()
            write_frames(path,np.stack([dot,np.roll(dot,1,axis=1)]))
            state = dict(default_bos_settings(),path=str(path),roi=[20,15,350,205],reference=0,
                         component='direction',show_vectors=True,vector_density='ultra_dense')
            clip = prepare_bos_preview(state,1,1,root=Path(temp)/'cache')
            path.unlink()
            try:
                with clip.reader() as reader:
                    c,v = 110,180
                    raw = reader.blended(0,0,0)
                    color = reader.blended(0,255,0)
                    gray_arrows = reader.blended(0,0,255)
                    color_arrows = reader.blended(0,255,255)
                    mix = lambda a,b,n: cv2.addWeighted(a,1.-n/255.,b,n/255.,0.)
                    np.testing.assert_array_equal(reader.blended(0,c,v),
                        mix(mix(raw,color,c),mix(gray_arrows,color_arrows,c),v))
                    np.testing.assert_array_equal(reader.blended(0,0,v),mix(raw,gray_arrows,v))
            finally:
                clip.close()

    def test_old_arrow_cache_still_paints_over_original(self):
        f = example()
        samples = sample_vectors(f,.4)
        data = np.empty(len(samples.x),dtype=SITE)
        for name in SITE.names:
            data[name]=getattr(samples,name)
        blob=HEADER.pack(samples.pitch,samples.full_length,.4)+data.tobytes()
        image=np.full((300,520,3),145,np.uint8)
        paint_vectors(BytesIO(blob),[(0,len(blob))],0,image,dict(roi=f.roi),VectorStyle(),include_key=False)
        self.assertTrue(np.any(image != 145))
        self.assertTrue(np.all(image[0] == 145))
