"""PCA Classic tracker, plus backwards-compatible access to portable Edge Assist."""
import math
import numpy as np
from scipy.ndimage import zoom
from edge_tracking import EdgeAlgorithms, Data, EdgeTrackingError as AutoTrackException

INTERP_METHOD = ['linear', 'quadratic', 'cubic', 'quartic', 'quintic']

class AutoTrackAlgorithms(EdgeAlgorithms):
    @staticmethod
    def interpolator(tpl_img, sa_img, sub_pixel, sub_pixel_type: str):
        sp_i = INTERP_METHOD.index(sub_pixel_type.lower())
        tpl_interp = zoom(tpl_img, sub_pixel, order = sp_i)
        sa_interp = zoom(sa_img, sub_pixel, order = sp_i)

        return tpl_interp, sa_interp


    @staticmethod
    def template_matcher(tpl_img: np, sa_img: np, sa_center: tuple, sa_rng: tuple, sub_pixel: int, sub_pixel_type: str, search_constraint=None, template_center_offset=(0., 0.)):
        """Inputs:
            tpl_img: np.ndarray, template image to match, row-col format
            sa_img: np.ndarray, current search area image, row-col format
            sa_center: tuple, center of search area in sa_img, full image coordinates (x, y)
            sa_rng: tuple, width/height of search area, (x, y) coordinates
        Outputs: float, full image coordinates (x, y)
        """
        try:
            from classic_tracking import match_classic
            origin = (int(sa_center[0] - math.floor(sa_rng[0]/2)),
                      int(sa_center[1] - math.floor(sa_rng[1]/2)))
            origin = (origin[0]+float(template_center_offset[0]),
                      origin[1]+float(template_center_offset[1]))
            return match_classic(tpl_img, sa_img, origin, sub_pixel,
                                 sub_pixel_type, search_constraint)
        except Exception as e:
            raise AutoTrackException(f'template_matcher() encountered an error\n{e}') from e
