"""Small live crops without converting or padding an entire Cine per object."""
import cv2
import numpy as np
from viewer_processing import apply_grade


def point_preview(image, point, zoom_size, output_size, grade=None):
    border = int(zoom_size) // 4
    span = max(1, int(zoom_size) // 2)
    x0, y0 = int(point[0])-border, int(point[1])-border
    h, w = image.shape[:2]
    left, top, right, bottom = max(0,x0), max(0,y0), min(w,x0+span), min(h,y0+span)
    crop = np.zeros((span,span)+image.shape[2:], dtype=image.dtype)
    if right > left and bottom > top:
        crop[top-y0:bottom-y0,left-x0:right-x0] = apply_grade(image[top:bottom,left:right], grade)
    return cv2.resize(crop,(int(output_size),int(output_size)),interpolation=cv2.INTER_CUBIC)
