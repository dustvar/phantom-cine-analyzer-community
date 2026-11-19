"""Spatial noise reduction for the displayed BOS overlay. Raw measurements stay in BOSField."""
import cv2
import numpy as np


# Clockwise from image right. Intermediate anchors give each diagonal its own hue.
DIRECTION_COLORS = ('#ff655f', '#ff974f', '#f6cc56', '#a9db70',
                    '#48daba', '#45c5eb', '#6499ff', '#d185ed')
_direction_anchors = np.array([tuple(bytes.fromhex(c[1:])) for c in DIRECTION_COLORS])
_angles = np.linspace(0, 8, 1024, endpoint=False)
_direction_lut = np.stack([np.interp(_angles, np.arange(9), np.r_[channel, channel[0]])
                           for channel in _direction_anchors.T], axis=-1).astype(np.float32)


def direction_tone(dx, dy):
    angle = cv2.phase(dx, dy)
    return _direction_lut[(angle * (1024 / (2*np.pi))).astype(np.int32) % 1024]


def overlay_vectors(field, reduce_noise=True):
    if not reduce_noise or min(field.valid.shape) < 3 or not field.valid.any():
        return field.dx, field.dy

    # Aim for a 16-source-pixel radius. A coarse grid needs fewer neighbors.
    radius = max(1, min(4, round(16 / field.step)))
    width = 2 * radius + 1
    support = cv2.erode(field.valid.astype('uint8'), np.ones((width, width), np.uint8),
                        borderType=cv2.BORDER_REPLICATE).astype(bool)
    if not support.any():
        return field.dx, field.dy

    def blur(values, sigma):
        return cv2.GaussianBlur(values, (width, width), sigma, borderType=cv2.BORDER_REPLICATE)

    # Adjacent-patch differences estimate the scale of local fluctuations.
    # Sample uniformly on very large grids; flat or unsupported grids pass through.
    stride = max(1, int(np.ceil(np.sqrt(field.valid.size / 32768))))
    differences = [np.diff(v, axis=axis)[::stride, ::stride].ravel()
                   for v in (field.dx, field.dy) for axis in (0, 1)]
    differences = np.concatenate(differences)
    differences = differences[np.isfinite(differences)]
    if not differences.size:
        return field.dx, field.dy
    scatter = 1.4826 * np.median(np.abs(differences - np.median(differences)))
    if scatter <= 1e-7:
        return field.dx, field.dy

    x, y = np.nan_to_num(field.dx), np.nan_to_num(field.dy)
    sigma = radius / 2.
    mx, my = blur(x, sigma), blur(y, sigma)
    variance = np.maximum(0., blur(x*x + y*y, sigma) - mx*mx - my*my)
    typical = np.median(variance[support])
    # Smooth quiet areas. Preserve locally varying displacement and back off
    # entirely when the local motion is coherent relative to patch scatter.
    weight = np.clip(typical / np.maximum(variance, 1e-12), 0., 1.)
    signal = np.hypot(blur(x, .6*sigma), blur(y, .6*sigma))
    weight *= np.clip((3.5 - signal / scatter) / 2., 0., 1.)
    return (np.where(support, x + weight * (mx-x), field.dx),
            np.where(support, y + weight * (my-y), field.dy))
