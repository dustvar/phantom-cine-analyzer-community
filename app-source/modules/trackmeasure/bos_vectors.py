"""A fixed-lattice quiver plot of measured, reference-relative BOS dot displacement."""
from dataclasses import dataclass
from functools import lru_cache
import re

import cv2
import numpy as np


# Nominal number of arrow sites across the longer side of the analysis region.
_DENSITIES = {'sparse': 16, 'balanced': 26, 'dense': 39}
MIN_FRACTION = 0.16
_INK = (22, 31, 40)
_LIGHT = (240, 254, 252)


@dataclass(frozen=True)
class VectorStyle:
    """Validated, hashable appearance shared by the live renderer and the analyzed player."""
    color: str = '#f0fefc'
    high_color: str = '#ffad4d'
    shape: str = 'open'
    thickness: int = 0
    encoding: str = 'double_head'

    @classmethod
    def from_settings(cls, settings):
        default = cls()
        style = cls(**{name: settings.get('vector_' + name, getattr(default, name))
                       for name in cls.__dataclass_fields__})
        for color in (style.color, style.high_color):
            if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
                raise ValueError('Choose an RGB color for the BOS arrows.')
        if style.shape not in ('open', 'filled', 'needle'):
            raise ValueError('Choose an open, filled, or needle arrow.')
        if type(style.thickness) is not int or not 0 <= style.thickness <= 8:
            raise ValueError('Choose automatic thickness or a stroke from 1 to 8 pixels.')
        if style.encoding not in ('double_head', 'threshold', 'grayscale'):
            raise ValueError('Choose double tips, two colors, or black to white for magnitude.')
        return style

    def settings(self):
        return {'vector_' + name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True)
class VectorSamples:
    rows: np.ndarray
    cols: np.ndarray
    x: np.ndarray
    y: np.ndarray
    dx: np.ndarray
    dy: np.ndarray
    saturated: np.ndarray
    pitch: int
    full_length: float
    limit: float
    draw_dx: np.ndarray
    draw_dy: np.ndarray

    def nearest(self, x, y):
        """Return the measured grid site for a pointer on a drawn arrow."""
        if self.rows.size:
            # Distance to a centered line segment in source image coordinates.
            dx, dy = x - self.x, y - self.y
            along = np.clip((dx*self.draw_dx + dy*self.draw_dy) /
                            (self.draw_dx**2 + self.draw_dy**2), -.5, .5)
            distance2 = (dx-along*self.draw_dx)**2 + (dy-along*self.draw_dy)**2
            i = int(np.argmin(distance2))
            if distance2[i] <= max(2.5, min(7., self.pitch*.16))**2:
                return int(self.rows[i]), int(self.cols[i])
        return None


def _sampling_stride(width, height, step, density):
    base_density = 'dense' if density == 'extra_dense' else density
    return max(1, round(max(14, max(width, height)/_DENSITIES[base_density])/step))


def vector_capacity(width, height, step, density):
    """Upper bound on lattice sites, before borders and match rejection."""
    stride = _sampling_stride(width, height, step, density)
    pitch = step*stride
    grids = 2 if density == 'extra_dense' and stride > 1 else 1
    return grids*((width+pitch-1)//pitch)*((height+pitch-1)//pitch)


def sample_vectors(field, limit, density='balanced'):
    """Select genuine measurement sites without averaging, filling holes or temporal trails."""
    if (density not in _DENSITIES and density != 'extra_dense') or not np.isfinite(limit) or limit <= 0:
        raise ValueError('Choose a positive full scale and a supported arrow density.')
    x0,y0,w,h = field.roi
    step = field.step
    stride = _sampling_stride(w, h, step, density)
    pitch = stride*step

    def sites(size):
        # Center the entire lattice once using geometry alone. Rejected samples
        # leave holes; they never cause other arrows to jump to different sites.
        return np.arange((size-1) % stride // 2, size, stride, dtype=np.intp)

    rows, cols = np.meshgrid(sites(len(field.y)), sites(len(field.x)), indexing='ij')
    rows, cols = rows.ravel(), cols.ravel()
    if density == 'extra_dense' and stride > 1:
        # Add a second, staggered set of measured sites between the dense arrows.
        # Coarse acquisition grids can already be at one arrow per measurement.
        def between(size):
            start = ((size-1) % stride // 2 + stride//2) % stride
            return np.arange(start, size, stride, dtype=np.intp)

        extra_rows, extra_cols = np.meshgrid(between(len(field.y)), between(len(field.x)), indexing='ij')
        rows = np.concatenate((rows, extra_rows.ravel()))
        cols = np.concatenate((cols, extra_cols.ravel()))
        pitch = max(step, round(pitch / np.sqrt(2)))
    full_length = pitch*.80
    dx, dy = field.dx[rows,cols], field.dy[rows,cols]
    magnitude = np.hypot(dx,dy)
    visible = field.valid[rows,cols] & np.isfinite(magnitude) & (magnitude >= MIN_FRACTION*limit)
    rows, cols = rows[visible], cols[visible]
    x,y = field.x[cols],field.y[rows]
    dx,dy,magnitude = dx[visible],dy[visible],magnitude[visible]
    factor = full_length/np.maximum(limit,magnitude)
    draw_dx, draw_dy = dx*factor, dy*factor

    # Pivot each glyph at the measurement. Opposing arrows on adjacent sides
    # of a narrow ridge stay distinguishable. Drop glyphs crossing a rejected
    # cell or the region boundary, where clipping would distort their length.
    keep = ((x - .5*np.abs(draw_dx) >= x0+1) & (x + .5*np.abs(draw_dx) <= x0+w-2)
            & (y - .5*np.abs(draw_dy) >= y0+1) & (y + .5*np.abs(draw_dy) <= y0+h-2))
    for fraction in np.linspace(-.5, .5, max(2, int(np.ceil(full_length/(.5*step)))+1)):
        col = np.rint(cols + fraction*draw_dx/step).astype(np.intp).clip(0, len(field.x)-1)
        row = np.rint(rows + fraction*draw_dy/step).astype(np.intp).clip(0, len(field.y)-1)
        keep &= field.valid[row, col]
    return VectorSamples(rows[keep], cols[keep], x[keep], y[keep], dx[keep], dy[keep],
                         magnitude[keep] > limit, pitch, full_length, float(limit),
                         draw_dx[keep], draw_dy[keep])


def _rgb(color):
    return tuple(bytes.fromhex(color[1:]))


def _contrast(rgb):
    luma = .2126*rgb[0] + .7152*rgb[1] + .0722*rgb[2]
    return _INK if luma >= 125 else _LIGHT


def _draw_arrows(image, x, y, draw_dx, draw_dy, full_length, width, border, rgb, outline, shape, extra_head):
    """Batch one color of glyphs using subpixel coordinates and a contrast outline."""
    origins = np.column_stack((x, y))
    shifts = np.column_stack((draw_dx, draw_dy))
    lengths = np.linalg.norm(shifts, axis=1)[:, None]
    unit = shifts / lengths
    tips = origins + shifts
    head = np.minimum(lengths*.46, max(2., full_length*.29))
    sides = head * .60 * np.column_stack((-unit[:, 1], unit[:, 0]))

    def corners(points, length):
        back = points - length*unit
        return np.stack((back+sides, points, back-sides), axis=1)

    paths = [np.stack((origins, tips), axis=1)]
    filled = []
    if shape == 'needle':
        # The long tapered body has a broad shoulder just ahead of the tail.
        shoulder = origins + .27*shifts
        filled.append(np.stack((origins, shoulder+sides, tips, shoulder-sides), axis=1))
    else:
        triangles = corners(tips, head)
        if shape == 'filled':
            filled.append(triangles)
        else:
            paths.append(triangles)
    if np.any(extra_head):
        paths.append(corners(tips - unit*head*.78, head)[extra_head])
    strokes = [p for group in paths for p in np.rint(group*16).astype(np.int32)]
    polygons = [p for group in filled for p in np.rint(group*16).astype(np.int32)]
    for color, weight in ((outline, width+2*border), (rgb, width)):
        if polygons:
            cv2.polylines(image, polygons, True, color, weight, cv2.LINE_AA, 4)
            cv2.fillPoly(image, polygons, color, cv2.LINE_AA, 4)
        cv2.polylines(image, strokes, False, color, weight, cv2.LINE_AA, 4)


def _draw_grouped(image, x, y, draw_dx, draw_dy, fraction, saturated, full_length, pitch, style):
    if not len(x):
        return
    width = style.thickness or max(1, round(pitch*.044))
    border = max(1, round(pitch*.034))
    if style.encoding == 'grayscale':
        # 64 perceptual steps permit one batched draw per shade, even for Extra Dense fields.
        shade = np.minimum(63, np.floor(63*np.minimum(fraction, 1.))).astype(np.uint8)
        colors = [(shade == level, (round(int(level)*255/63),)*3) for level in np.unique(shade)]
    elif style.encoding == 'threshold':
        colors = [(~saturated, _rgb(style.color)), (saturated, _rgb(style.high_color))]
    else:
        colors = [(slice(None), _rgb(style.color))]
    for mask, rgb in colors:
        if len(x[mask]):
            outline = _contrast(rgb)
            if style.encoding == 'grayscale':
                # A sudden switch from a light to a dark outline would introduce
                # a false boundary halfway through a smooth magnitude field.
                shade = round(112 - 88*rgb[0]/255)
                outline = (shade, shade, shade)
            _draw_arrows(image, x[mask], y[mask], draw_dx[mask], draw_dy[mask], full_length,
                         width, border, rgb, outline, style.shape, saturated[mask] & (style.encoding == 'double_head'))


@lru_cache(maxsize=64)
def _key(width, height, full_length, limit, style):
    short = min(width,height)
    size = min(2.4,max(.36,short*.00075))
    font = cv2.FONT_HERSHEY_SIMPLEX
    pen = max(1,round(size*1.75))
    pad = max(4,round(short*.012))
    advance = round(full_length)+pad
    label = f'{limit:g} px'
    text_width = lambda text, scale: cv2.getTextSize(text,font,scale,pen)[0][0]
    gap = max(5,round(12*size))
    first_width = advance + text_width(label,size)
    clipped = '> '+label
    gradient = style.encoding == 'grayscale'
    ramp_width = max(30, round(short*.07))
    upper_label = f'{limit:g}+ px'
    second_width = (text_width('0',size)+2*gap+ramp_width+text_width(upper_label,size)
                    if gradient else advance+text_width(clipped,size))
    key_w = 2*pad + first_width + second_width + 2*gap
    compact = key_w > width-2*pad
    if compact:
        key_w = 2*pad + (second_width if gradient else first_width)
    head_h = cv2.getTextSize('DOT SHIFT',font,size*.73,pen)[0][1]
    body_h = max(round(full_length*.43),cv2.getTextSize(label,font,size,pen)[0][1],style.thickness+3)
    key_h = 2*pad + head_h+gap+body_h
    if key_w > width-2*pad or key_h > height*.38 or min(width,height) < 48:
        return None
    ink = np.empty((key_h,key_w,3),np.uint8)
    ink[:] = (24,33,43)
    cv2.putText(ink,'DOT SHIFT',(pad,pad+head_h),font,size*.73,(180,205,209),pen,cv2.LINE_AA)
    y = pad+head_h+gap+body_h*.49
    baseline = round(y+body_h*.37)
    for left, text, saturated in [(pad,label,False),(pad+first_width+2*gap,clipped,True)]:
        if (compact and saturated) or (gradient and (saturated or compact)):
            continue
        _draw_grouped(ink,np.array([left]),np.array([y]),np.array([full_length]),np.array([0.]),
                      np.array([1.]),np.array([saturated]),full_length,full_length/.8,style)
        cv2.putText(ink,text,(left+advance,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
    if gradient:
        left = pad if compact else pad+first_width+2*gap
        cv2.putText(ink,'0',(left,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
        x = left+text_width('0',size)+gap
        h = max(3,round(body_h*.63))
        top = round(y-h*.5)
        cv2.rectangle(ink,(x-1,top-1),(x+ramp_width,top+h),(156,165,172),1)
        ink[top:top+h,x:x+ramp_width,:] = np.linspace(0,255,ramp_width,dtype=np.uint8)[None,:,None]
        cv2.putText(ink,upper_label,(x+ramp_width+gap,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
    return ink


def key_rect(width, height, field, samples, anchor_roi=None, style=VectorStyle()):
    """Locate the exported scale away from the measurement and stationary patch."""
    if not field.valid.any():
        return None
    key = _key(width, height, samples.full_length, samples.limit, style)
    if key is None:
        return None
    kh, kw = key.shape[:2]
    margin = max(3, round(min(width, height)*.014))
    corners = [(width-kw-margin, height-kh-margin), (margin, height-kh-margin),
               (width-kw-margin, margin), (margin, margin)]

    def overlap(position, rect):
        x, y = position
        rx, ry, rw, rh = rect
        return max(0, min(x+kw, rx+rw)-max(x, rx))*max(0, min(y+kh, ry+rh)-max(y, ry))

    x, y = min(corners, key=lambda pos: (overlap(pos, field.roi), overlap(pos, anchor_roi) if anchor_roi else 0))
    return x, y, kw, kh


def draw_vector_field(image, field, samples, placement, style=VectorStyle()):
    """Paint centered source-grid arrows on a caller-owned RGB8 display copy."""
    if not field.valid.any():
        return
    x, y, w, h = field.roi
    _draw_grouped(image[y:y+h, x:x+w], samples.x-x-.5*samples.draw_dx, samples.y-y-.5*samples.draw_dy,
                  samples.draw_dx, samples.draw_dy, np.hypot(samples.dx,samples.dy)/samples.limit,
                  samples.saturated, samples.full_length, samples.pitch, style)
    if placement is not None:
        x, y, kw, kh = placement
        sprite = _key(image.shape[1], image.shape[0], samples.full_length, samples.limit, style)
        patch = image[y:y+kh, x:x+kw]
        cv2.addWeighted(sprite, .90, patch, .10, 0., dst=patch)
