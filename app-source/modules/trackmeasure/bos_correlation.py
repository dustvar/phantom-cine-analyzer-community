"""Windowed zero-mean normalized cross-correlation for apparent BOS dot shifts."""
import cv2
import numpy as np


def _prepare(gray, window):
    # A mild low-pass targets pixel-scale read noise before matching dot shapes.
    image = cv2.GaussianBlur(gray.astype(np.float32) - 128, (5, 5), .8)
    image = cv2.copyMakeBorder(image, 1, 1, 1, 1, cv2.BORDER_REFLECT_101)
    mean = cv2.blur(image, (window, window))
    deviation = np.sqrt(np.maximum(0., cv2.blur(image * image, (window, window)) - mean * mean))
    return image, mean, deviation


def _surface(reference, current, xs, ys, window, radius):
    ref, mean_r, std_r = reference
    img, mean_i, std_i = current
    stride = int(xs[1]-xs[0]) if len(xs) > 1 else 1
    y_stride = int(ys[1]-ys[0]) if len(ys) > 1 else 1
    height, width = len(ys), len(xs)
    span = 2*radius + 1
    scores = np.empty((span, span, height, width), np.float32)
    half = window // 2
    # Symmetric windows sample the displacement at the midpoint between the
    # matched patches. Swapping identical images gives an exactly centered
    # peak even when a patch has very different texture near its two edges.
    for row, dy in enumerate(range(-radius, radius+1)):
        y = int(ys[0])-dy//2
        ey = int(ys[-1])-dy//2 + 1
        ref_y = slice(y, ey, y_stride)
        cur_y = slice(y+dy, ey+dy, y_stride)
        for col, dx in enumerate(range(-radius, radius+1)):
            x = int(xs[0])-dx//2
            ex = int(xs[-1])-dx//2 + 1
            ref_x = slice(x, ex, stride)
            cur_x = slice(x+dx, ex+dx, stride)
            product = (ref[y-half:ey+half, x-half:ex+half] *
                       img[y-half+dy:ey+half+dy, x-half+dx:ex+half+dx])
            cross = cv2.blur(product, (window, window))[half:half+ey-y:y_stride, half:half+ex-x:stride]
            cross -= mean_r[ref_y, ref_x] * mean_i[cur_y, cur_x]
            scale = std_r[ref_y, ref_x] * std_i[cur_y, cur_x]
            scores[row,col] = np.clip(cross / np.maximum(scale, 1e-6), -1., 1.)
    return scores


def _peak(scores, radius, search):
    # Index all peak neighborhoods in parallel. A one-pixel guard ring outside
    # the user limit lets us distinguish well-centered boundary shifts from
    # maxima whose actual locations lie beyond the allowed search.
    span = 2*radius+1
    height, width = scores.shape[-2:]
    flat = scores.reshape(span*span, -1)
    center = flat.argmax(axis=0)
    yi, xi = np.divmod(center, span)
    patch = np.arange(len(center))
    peak = flat[center, patch]
    outside = (xi == 0) | (yi == 0) | (xi == span-1) | (yi == span-1)
    safe = np.clip(yi,1,span-2)*span + np.clip(xi,1,span-2)
    def sample(dy, dx):
        # Log fit approximates a Gaussian correlation peak; clipping only
        # affects the tails of low-scoring, subsequently rejected peaks.
        return np.log(np.maximum(flat[safe+dy*span+dx, patch], .01))
    middle = sample(0,0)
    west,east = sample(0,-1),sample(0,1)
    north,south = sample(-1,0),sample(1,0)
    hxx = 2*middle - east - west
    hyy = 2*middle - south - north
    hxy = -.25*(sample(1,1)-sample(1,-1)-sample(-1,1)+sample(-1,-1))
    det = hxx*hyy - hxy*hxy
    gx,gy = (east-west)*.5, (south-north)*.5
    safe_det = np.maximum(det, 1e-8)
    sub_x,sub_y = (hyy*gx-hxy*gy)/safe_det,(hxx*gy-hxy*gx)/safe_det
    # A stripe does not constrain both displacement components. Reject a flat
    # or saddle-shaped peak, and do not extrapolate the peak fit.
    weak = ((hxx <= .002) | (hyy <= .002) | (det <= .015*(hxx+hyy)**2)
            | (np.abs(sub_x) > 1.) | (np.abs(sub_y) > 1.))
    dx,dy = (xi-radius+sub_x).astype(np.float32), (yi-radius+sub_y).astype(np.float32)
    # Allow a tiny tolerance only at a mathematically exact boundary, e.g.
    # constant integer translation with floating-point rounding.
    outside |= (np.abs(dx)>search+.001) | (np.abs(dy)>search+.001)
    # Test alternative solutions outside the immediate correlation peak. A
    # repeated dot pattern can correlate perfectly at several displacements.
    for oy in range(-1,2):
        for ox in range(-1,2):
            neighbor = np.clip(yi+oy,0,span-1)*span + np.clip(xi+ox,0,span-1)
            flat[neighbor,patch] = -2
    ambiguous = peak - flat.max(axis=0) < .03
    shape=(height,width)
    return tuple(array.reshape(shape) for array in
                 (dx.clip(-search, search),dy.clip(-search, search),peak,weak,outside,ambiguous))


def correlate(reference, current, xs, ys, window, search, *, prepared_reference=None):
    prepared_ref = _prepare(reference, window) if prepared_reference is None else prepared_reference
    prepared_img = _prepare(current, window)
    xs = xs.astype(np.intp) + 1
    ys = ys.astype(np.intp) + 1
    radius = search + 1
    # A dense Fine grid at the largest search radius must not allocate a
    # full-frame score volume. Bound the tile of displacement scores to 8 MiB.
    tile = max(1, min(160, int(np.sqrt(2_097_152)/(2*radius+1))))
    shape = (len(ys),len(xs))
    outputs = [np.empty(shape, dtype=dtype) for dtype in
               (np.float32,np.float32,np.float32,bool,bool,bool)]
    for y in range(0,len(ys),tile):
        for x in range(0,len(xs),tile):
            region = np.s_[y:y+tile,x:x+tile]
            scores = _surface(prepared_ref, prepared_img, xs[x:x+tile], ys[y:y+tile], window, radius)
            values = _peak(scores, radius, search)
            for result,value in zip(outputs,values):
                result[region] = value
    return outputs
