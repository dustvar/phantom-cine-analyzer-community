"""A fixed-lattice quiver plot of measured, reference-relative BOS dot displacement."""
from dataclasses import dataclass
from functools import lru_cache
from math import ceil
import re

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen


# Nominal number of arrow sites across the longer side of the analysis region.
_DENSITIES = {'sparse': 16, 'balanced': 26, 'dense': 39, 'extra_dense': 39, 'ultra_dense': 39}
DENSITY_MODES = tuple(_DENSITIES)
MIN_FRACTION = 0.16
_INK = (22, 31, 40)
_LIGHT = (240, 254, 252)
_STROKE_WIDTHS = tuple(i / 2 for i in range(17))


@dataclass(frozen=True)
class VectorStyle:
    """Validated, hashable appearance shared by the live renderer and the analyzed player."""
    color: str = '#f0fefc'
    high_color: str = '#ffad4d'
    shape: str = 'open'
    thickness: float = 0
    encoding: str = 'double_head'
    outline: bool = True

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
        if type(style.thickness) not in (int, float) or style.thickness not in _STROKE_WIDTHS:
            raise ValueError('Choose Auto or a stroke from 0.5 to 8 pixels in half-pixel steps.')
        if type(style.outline) is not bool:
            raise ValueError('Choose whether to outline the BOS arrows.')
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
    return max(1, round(max(14, max(width, height)/_DENSITIES[density])/step))


def _lattices(stride, density):
    # Stagger the second lattice diagonally. The next pair fills the two
    # remaining corners of each cell, always landing on measured sites.
    if stride == 1 or density not in ('extra_dense', 'ultra_dense'):
        return ((0, 0),)
    half = stride // 2
    diagonal = ((0, 0), (half, half))
    return diagonal if density == 'extra_dense' else diagonal + ((half, 0), (0, half))


def vector_geometry(width, height, step, density):
    stride = _sampling_stride(width, height, step, density)
    grids = len(_lattices(stride, density))
    pitch = max(step, round(step*stride / np.sqrt(grids)))
    return pitch, pitch*.80


def _axis_sites(count, stride, phase):
    start = ((count-1) % stride // 2 + phase) % stride
    return range(start, count, stride)


def vector_capacity(width, height, step, density, margin=0):
    """Number of candidate measurement sites before signal/mask rejection."""
    stride = _sampling_stride(width, height, step, density)
    ny = max(0, (height-2*margin+step-1)//step)
    nx = max(0, (width-2*margin+step-1)//step)
    return sum(len(_axis_sites(ny,stride,ry))*len(_axis_sites(nx,stride,cx))
               for ry,cx in _lattices(stride, density))


def sample_vectors(field, limit, density='balanced'):
    """Select genuine measurement sites without averaging, filling holes or temporal trails."""
    if density not in DENSITY_MODES or not np.isfinite(limit) or limit <= 0:
        raise ValueError('Choose a positive full scale and a supported arrow density.')
    x0,y0,w,h = field.roi
    step = field.step
    stride = _sampling_stride(w, h, step, density)
    pitch, full_length = vector_geometry(w, h, step, density)
    grids = [np.meshgrid(_axis_sites(len(field.y), stride, ry), _axis_sites(len(field.x), stride, cx),
                         indexing='ij') for ry,cx in _lattices(stride, density)]
    rows, cols = (np.concatenate([grid[axis].ravel() for grid in grids]).astype(np.intp, copy=False)
                  for axis in (0,1))
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


def _vector_paths(groups, closed=False):
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.WindingFill)
    # Small disjoint paths keep Qt's scan converter local to nearby arrows.
    # One enormous stroked path makes rasterization much slower on large fields.
    batch = []
    for group in groups:
        for first, *points in group.tolist():
            path.moveTo(*first)
            for point in points:
                path.lineTo(*point)
            if closed:
                path.closeSubpath()
            if path.elementCount() >= 96:
                batch.append(path)
                path = QPainterPath()
                path.setFillRule(Qt.FillRule.WindingFill)
    if not path.isEmpty():
        batch.append(path)
    return batch


def _draw_arrows(image, x, y, draw_dx, draw_dy, full_length, width, border, rgb, outline, shape, extra_head, painter=None):
    """Batch one color of glyphs with an optional contrast outline."""
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
    passes = [(outline, width+2*border), (rgb, width)] if border else [(rgb, width)]
    if painter is not None:
        # Rasterize the requested source-pixel area directly, including strokes
        # thinner than one pixel. OpenCV accepts only whole-pixel pen widths.
        shafts = _vector_paths(paths)
        solids = _vector_paths(filled, closed=True) if filled else None
        for color, weight in passes:
            ink = QColor(*color)
            painter.setPen(QPen(ink, weight, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                                Qt.PenJoinStyle.RoundJoin))
            if solids is not None:
                painter.setBrush(QBrush(ink))
                for path in solids:
                    painter.drawPath(path)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for path in shafts:
                painter.drawPath(path)
        return
    strokes = [p for group in paths for p in np.rint(group*16).astype(np.int32)]
    polygons = [p for group in filled for p in np.rint(group*16).astype(np.int32)]
    for color, weight in passes:
        weight = int(weight)
        if polygons:
            cv2.polylines(image, polygons, True, color, weight, cv2.LINE_AA, 4)
            cv2.fillPoly(image, polygons, color, cv2.LINE_AA, 4)
        cv2.polylines(image, strokes, False, color, weight, cv2.LINE_AA, 4)


def _draw_grouped(image, x, y, draw_dx, draw_dy, fraction, saturated, full_length, pitch, style, roi=None, inverse_luma=None):
    if not len(x):
        return
    width = style.thickness or max(1, round(pitch*.044))
    border = max(1, round(pitch*.034)) if style.outline else 0
    if inverse_luma is not None:
        # Choose opposite ink from the prepared backing under each glyph.
        # The two ranges avoid gray-on-gray at half scale.
        bright = inverse_luma >= 118
        colors = [(bright, (8, 13, 18)), (~bright, (250, 252, 254))]
    elif style.encoding == 'grayscale':
        # 64 perceptual steps permit one batched draw per shade, even for Extra Dense fields.
        shade = np.minimum(63, np.floor(63*np.minimum(fraction, 1.))).astype(np.uint8)
        colors = [(shade == level, (round(int(level)*255/63),)*3) for level in np.unique(shade)]
    elif style.encoding == 'threshold':
        colors = [(~saturated, _rgb(style.color)), (saturated, _rgb(style.high_color))]
    else:
        colors = [(slice(None), _rgb(style.color))]
    painter = None
    if width % 1:
        buffer = np.ascontiguousarray(image)
        surface = QImage(buffer.data, buffer.shape[1], buffer.shape[0], buffer.strides[0], QImage.Format.Format_RGB888)
        painter = QPainter(surface)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if roi:
            painter.setClipRect(*roi)
            painter.translate(roi[0], roi[1])
        # Qt places pixel centers half a pixel past the measured image coordinates.
        painter.translate(.5, .5)
    elif roi:
        left, top, w, h = roi
        image = image[top:top+h, left:left+w]
    try:
        for mask, rgb in colors:
            if len(x[mask]):
                outline = _contrast(rgb)
                if style.encoding == 'grayscale' and inverse_luma is None:
                    # A sudden switch from a light to a dark outline would introduce
                    # a false boundary halfway through a smooth magnitude field.
                    shade = round(112 - 88*rgb[0]/255)
                    outline = (shade, shade, shade)
                _draw_arrows(image, x[mask], y[mask], draw_dx[mask], draw_dy[mask], full_length,
                             width, border, rgb, outline, style.shape, saturated[mask] & (style.encoding == 'double_head'), painter)
    finally:
        if painter is not None:
            painter.end()
            if buffer is not image:
                image[:] = buffer


@lru_cache(maxsize=64)
def _key(width, height, full_length, limit, style, inverse=False, scale=1., *, allow_compact=True):
    short = min(width,height)
    size = min(2.4,max(.36,short*.00075))*scale
    font = cv2.FONT_HERSHEY_SIMPLEX
    pen = max(1,round(size*1.75))
    pad = max(1, round(max(4,round(short*.012))*scale))
    advance = round(full_length)+pad
    label = f'{limit:g} px'
    text_width = lambda text, scale: cv2.getTextSize(text,font,scale,pen)[0][0]
    gap = max(2,round(max(5*scale,12*size)))
    first_width = advance + text_width(label,size)
    clipped = '> '+label
    gradient = inverse or style.encoding == 'grayscale'
    ramp_width = max(8,round(max(30,round(short*.07))*scale))
    upper_label = f'{limit:g}+ px'
    second_width = (text_width('0',size)+2*gap+ramp_width+text_width(upper_label,size)
                    if gradient else advance+text_width(clipped,size))
    key_w = 2*pad + first_width + second_width + 2*gap
    compact = key_w > width-2*pad
    if compact and not allow_compact:
        return None
    if compact:
        key_w = 2*pad + (second_width if gradient else first_width)
    head_h = cv2.getTextSize('DOT SHIFT',font,size*.73,pen)[0][1]
    body_h = max(round(full_length*.43),cv2.getTextSize(label,font,size,pen)[0][1],ceil(style.thickness)+3)
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
                      np.array([1.]),np.array([saturated]),full_length,full_length/.8,style,
                      inverse_luma=np.array([0]) if inverse else None)
        cv2.putText(ink,text,(left+advance,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
    if gradient:
        left = pad if compact else pad+first_width+2*gap
        cv2.putText(ink,'0',(left,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
        x = left+text_width('0',size)+gap
        h = max(3,round(body_h*.63))
        top = round(y-h*.5)
        cv2.rectangle(ink,(x-1,top-1),(x+ramp_width,top+h),(156,165,172),1)
        ink[top:top+h,x:x+ramp_width,:] = np.linspace(0,255,ramp_width,dtype=np.uint8)[None,:,None]
        if inverse:
            # Show the white-to-dark ink switch over the magnitude backing.
            for fraction, value in ((.22,250),(.8,8)):
                cx = x+round((ramp_width-1)*fraction)
                cy = top+h//2
                cv2.arrowedLine(ink,(cx-3,cy),(cx+3,cy),(value,)*3,1,cv2.LINE_AA,tipLength=.5)
        cv2.putText(ink,upper_label,(x+ramp_width+gap,baseline),font,size,(238,245,245),pen,cv2.LINE_AA)
    return ink


def key_rect(width, height, field, samples, anchor_roi=None, style=VectorStyle(), inverse=False):
    """Locate the exported scale away from the measurement and stationary patch."""
    if not field.valid.any():
        return None
    key = _key(width, height, samples.full_length, samples.limit, style, inverse)
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


def draw_vector_field(image, field, samples, placement, style=VectorStyle(), *, inverse=False):
    """Paint centered source-grid arrows on a caller-owned RGB8 display copy."""
    if not field.valid.any():
        return
    x, y = field.roi[:2]
    luma = image[samples.y.astype(np.intp), samples.x.astype(np.intp), 0] if inverse else None
    _draw_grouped(image, samples.x-x-.5*samples.draw_dx, samples.y-y-.5*samples.draw_dy,
                  samples.draw_dx, samples.draw_dy, np.hypot(samples.dx,samples.dy)/samples.limit,
                  samples.saturated, samples.full_length, samples.pitch, style, roi=field.roi, inverse_luma=luma)
    if placement is not None:
        x, y, kw, kh = placement
        sprite = _key(image.shape[1], image.shape[0], samples.full_length, samples.limit, style, inverse)
        patch = image[y:y+kh, x:x+kw]
        cv2.addWeighted(sprite, .90, patch, .10, 0., dst=patch)
