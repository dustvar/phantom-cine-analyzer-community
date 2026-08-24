import math
import numpy as np
#from scipy.interpolate import interpn
from scipy.ndimage import zoom
from skimage.feature import match_template 
from dataclasses import dataclass, field

INTERP_METHOD = ['linear', 'quadratic', 'cubic', 'quartic', 'quintic']

@dataclass
class Data:
    norm_xcorr_map: np.array = field(default=None)
    x_pos: float = field(default=None)
    y_pos: float = field(default=None)
    confid_val_ij: float = field(default=None)

class AutoTrackAlgorithms:
    @staticmethod
    def interpolator(tpl_img, sa_img, sub_pixel, sub_pixel_type: str):
        sp_i = INTERP_METHOD.index(sub_pixel_type.lower())
        tpl_interp = zoom(tpl_img, sub_pixel, order = sp_i)
        sa_interp = zoom(sa_img, sub_pixel, order = sp_i)

        return tpl_interp, sa_interp

    @staticmethod
    def template_matcher(tpl_img: np, sa_img: np, sa_center: tuple, sa_rng: tuple, sub_pixel: int, sub_pixel_type: str):
        """Inputs:
            tpl_img: np.ndarray, template image to match, row-col format
            sa_img: np.ndarray, current search area image, row-col format
            sa_center: tuple, center of search area in sa_img, full image coordinates (x, y)
            sa_rng: tuple, width/height of search area, (x, y) coordinates
        Outputs: float, full image coordinates (x, y)
        """
        try:
            # interpolate the template and search area
            tpl_interp, sa_interp = AutoTrackAlgorithms.interpolator(tpl_img, sa_img, sub_pixel, sub_pixel_type)

            # put the template and search area through a template matcher function
            norm_xcorr_map = match_template(sa_interp, tpl_interp, pad_input=True)

            # unravel the xcorr matrix above (result) and finds the max value
            ij = np.unravel_index(np.argmax(norm_xcorr_map), norm_xcorr_map.shape)
            confid_val_ij = np.max(norm_xcorr_map)

            # get the location of where the template image is within entire image
            sa_tl = int(sa_center[0] - math.floor(sa_rng[0]/2)), int(sa_center[1] - math.floor(sa_rng[1]/2))
            x_pos = ij[1] / sub_pixel 
            y_pos = ij[0] / sub_pixel
            x_pos = x_pos + sa_tl[0]
            y_pos = y_pos + sa_tl[1]
            
            # build dataclass and return
            data = Data(norm_xcorr_map = norm_xcorr_map,
                        x_pos = x_pos,
                        y_pos = y_pos,
                        confid_val_ij  = confid_val_ij)   
            return data
    
        except Exception as e:
            raise AutoTrackException(f'template_matcher() encountered an error\n{e}')  
        
# EXCEPTIONS
class AutoTrackException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
