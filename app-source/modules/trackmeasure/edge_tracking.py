"""Portable Edge Assist matching engine: NumPy + OpenCV, with no PCA imports.

Rigid in-plane X/Y/angle estimation and Point Lock refinement. Classic particle
correlation and its SciPy/scikit-image dependencies remain in PCA's adapter.
"""
import math
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
import cv2
import numpy as np

class EdgeTrackingError(Exception):
    pass


@dataclass(frozen=True)
class EdgeSettings:
    confidence: float = .40
    search_multiplier: float = 1.5
    rotation_range: float = 180.
    rotation_step: float = 2.
    edge_weight: float = .60
    edge_threshold: float = .30
    neighbor_weight: float = .65
    position_precision: float = .1
    angle_precision: float = .1
    point_lock: bool = True
    miss_limit: int = 3

    def __post_init__(self):
        for key in ('confidence','edge_weight','edge_threshold','neighbor_weight'):
            if not math.isfinite(getattr(self,key)) or not 0<=getattr(self,key)<=1:raise ValueError('Invalid '+key)
        for key,lo,hi in (('search_multiplier',1,20),('rotation_range',0,180),('rotation_step',.1,45),('position_precision',.01,1),('angle_precision',.05,1),('miss_limit',1,100)):
            if not math.isfinite(getattr(self,key)) or not lo<=getattr(self,key)<=hi:raise ValueError('Invalid '+key)


@dataclass
class EdgeObservation:
    frame: int
    accepted: bool
    point: tuple | None
    angle: float | None
    confidence: float
    reason: str
    components: dict = field(default_factory=dict)

@dataclass
class Data:
    norm_xcorr_map: np.array = field(default=None)
    x_pos: float = field(default=None)
    y_pos: float = field(default=None)
    confid_val_ij: float = field(default=None)
    angle_deg: float = field(default=0.0)
    intensity_score: float = field(default=None)
    edge_score: float = field(default=None)
    raw_similarity: float = field(default=None)
    peak_uniqueness: float = field(default=None)
    edge_density: float = field(default=None)
    edge_f1: float = field(default=None)
    continuity_score: float = field(default=None)
    adjacent_score: float = field(default=None)
    setup_score: float = field(default=None)
    method: str = field(default='Classic')


def candidate_search_mask(x, y, pose, constraint, axis_tolerance=.76):
    """Allowed fixture centers inside an oriented search rectangle (no Qt/PCA).

    Coordinates use image pixels. Pose follows OpenCV's angle convention; the
    search angle is clockwise in the image. An axis constraint limits matching,
    not just the displayed trajectory.
    """
    theta=math.radians(float(constraint['angle']));c,s=math.cos(theta),math.sin(theta)
    dx=np.asarray(x)-constraint['center'][0];dy=np.asarray(y)-constraint['center'][1]
    local_x=c*dx+s*dy;local_y=-s*dx+c*dy
    relative=math.radians(-float(pose)-float(constraint['angle']))
    rw,rh=(np.asarray(constraint['roi_size'],float)-1)/2
    extent_x=abs(math.cos(relative))*rw+abs(math.sin(relative))*rh
    extent_y=abs(math.sin(relative))*rw+abs(math.cos(relative))*rh
    sw,sh=(np.asarray(constraint['size'],float)-1)/2
    allowed=(np.abs(local_x)+extent_x<=sw+1e-6)&(np.abs(local_y)+extent_y<=sh+1e-6)
    axis=constraint.get('motion_axis','xy')
    if axis!='xy':
        offset=constraint.get('template_offset',(0,0));a=math.radians(float(pose))
        ox=math.cos(a)*offset[0]+math.sin(a)*offset[1]
        oy=-math.sin(a)*offset[0]+math.cos(a)*offset[1]
        anchor=constraint['anchor_point']
        allowed &= (np.abs(np.asarray(x)-ox-anchor[0])<=axis_tolerance if axis=='y'
                    else np.abs(np.asarray(y)-oy-anchor[1])<=axis_tolerance)
    return allowed


def constrain_match_center(x,y,pose,constraint):
    """Apply an explicit one-axis model after the constrained peak search."""
    axis=constraint.get('motion_axis','xy')
    offset=constraint.get('template_offset',(0,0));a=math.radians(float(pose))
    if axis=='y':x=constraint['anchor_point'][0]+math.cos(a)*offset[0]+math.sin(a)*offset[1]
    elif axis=='x':y=constraint['anchor_point'][1]-math.sin(a)*offset[0]+math.cos(a)*offset[1]
    return x,y


class EdgeMatchCache:
    """Bounded per-tracking-pass invariant template data; no global state."""
    def __init__(self, max_bytes=16*1024*1024, max_entries=256):
        self.max_bytes=int(max_bytes);self.max_entries=int(max_entries)
        self.items=OrderedDict();self.bytes=0

    @staticmethod
    def _size(value):
        if isinstance(value,np.ndarray):return value.nbytes
        if isinstance(value,(tuple,list)):return sum(EdgeMatchCache._size(v) for v in value)
        return 64

    def get(self,key,build):
        if key in self.items:
            self.items.move_to_end(key)
            return self.items[key][0]
        value=build();size=self._size(value)
        if size<=self.max_bytes:
            while self.items and (self.bytes+size>self.max_bytes or len(self.items)>=self.max_entries):
                _,(_,old_size)=self.items.popitem(last=False);self.bytes-=old_size
            self.items[key]=(value,size);self.bytes+=size
        return value


class EdgeAlgorithms:
    @staticmethod
    def _normalize_u8(image):
        """Contrast-normalize an image while rejecting isolated hot pixels."""
        arr = np.asarray(image, dtype=np.float32)
        finite = arr[np.isfinite(arr)]
        if finite.size == 0:
            return np.zeros(arr.shape, dtype=np.uint8)
        low, high = np.percentile(finite, (2.0, 98.0))
        if high <= low:
            low = float(np.min(finite))
            high = float(np.max(finite))
        if high <= low:
            return np.zeros(arr.shape, dtype=np.uint8)
        normalized = np.clip((arr - low) * (255.0 / (high - low)), 0, 255)
        return normalized.astype(np.uint8)


    @staticmethod
    def _edge_image(image, threshold=None, dilate=True):
        """Create a slightly tolerant binary edge representation."""
        image_u8 = EdgeAlgorithms._normalize_u8(image)
        # Suppress sensor speckle before edge extraction. A wider, modest blur
        # preserves coherent object contours while preventing single-pixel Cine
        # noise from filling the entire reinforcement region with false edges.
        blurred = cv2.GaussianBlur(image_u8, (7, 7), 1.5)
        if threshold is None:
            median = float(np.median(blurred))
            lower = int(max(8, 0.66 * median))
            upper = int(max(lower + 12, min(255, 1.33 * median)))
        else:
            upper = int(np.clip(float(threshold), 0.02, 1.0) * 255)
            lower = max(4, int(upper * 0.40))
        edges = cv2.Canny(blurred, lower, upper, L2gradient=True)
        if np.count_nonzero(edges) < 8:
            gx = cv2.Scharr(blurred, cv2.CV_32F, 1, 0)
            gy = cv2.Scharr(blurred, cv2.CV_32F, 0, 1)
            magnitude = cv2.magnitude(gx, gy)
            threshold = np.percentile(magnitude, 75) if magnitude.size else 0
            if threshold > 0:
                edges = np.where(magnitude >= threshold, 255, 0).astype(np.uint8)
            else:
                edges = np.zeros(magnitude.shape, dtype=np.uint8)
        if dilate:
            return cv2.dilate(edges, np.ones((3, 3), dtype=np.uint8), iterations=1)
        return edges


    @staticmethod
    def _soft_edge_image(image, threshold=None):
        """Return a blur-tolerant, continuous gradient-strength image.

        Canny edges are useful for the visible geometry overlay and discrete
        overlap diagnostics, but their one-pixel threshold crossings discard
        the intensity ramp that contains subpixel edge-location information.
        This representation preserves that ramp for correlation refinement.
        """
        image_u8 = EdgeAlgorithms._normalize_u8(image)
        if image_u8.size == 0:
            return np.zeros(image_u8.shape, dtype=np.float32)
        blurred = cv2.GaussianBlur(image_u8, (5, 5), 1.0)
        gx = cv2.Scharr(blurred, cv2.CV_32F, 1, 0)
        gy = cv2.Scharr(blurred, cv2.CV_32F, 0, 1)
        magnitude = cv2.magnitude(gx, gy)
        finite = magnitude[np.isfinite(magnitude)]
        if finite.size == 0:
            return np.zeros(magnitude.shape, dtype=np.float32)
        scale = float(np.percentile(finite, 98.0))
        if scale <= 1e-9:
            return np.zeros(magnitude.shape, dtype=np.float32)
        normalized = np.clip(magnitude / scale, 0.0, 1.0)
        # Keep the transition gradual. A blurred edge still contributes with a
        # lower weight instead of jumping from present to absent at one pixel.
        floor = 0.04 if threshold is None else float(
            np.clip(float(threshold) * 0.20, 0.01, 0.20)
        )
        softened = np.clip((normalized - floor) / (1.0 - floor), 0.0, 1.0)
        return cv2.GaussianBlur(softened.astype(np.float32), (3, 3), 0.65)


    @staticmethod
    def extract_oriented_patch(image, center, size, angle_deg=0.0):
        """Extract a rotated rectangular region centered in full-image coordinates."""
        arr = np.asarray(image)
        if arr.dtype not in (np.uint8, np.float32):
            arr = arr.astype(np.float32)
        width = max(1, int(round(size[0])))
        height = max(1, int(round(size[1])))
        center = (float(center[0]), float(center[1]))
        if abs(float(angle_deg)) < 1e-9:
            return cv2.getRectSubPix(arr, (width, height), center)
        matrix = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0)
        # Sample only the output pixels needed by getRectSubPix, rather than
        # rotating millions of source pixels to obtain a 15--61px fixture.
        # Keep OpenCV's affine interpolation grid and float32 center rounding
        # so this is pixel-identical to the previous full-frame warp. A simple
        # translated warp changes rounding at some 1/32px phase boundaries.
        x0 = max(0, math.floor(center[0]-(width-1)/2)-1)
        y0 = max(0, math.floor(center[1]-(height-1)/2)-1)
        x1 = min(arr.shape[1], math.ceil(center[0]+(width-1)/2)+2)
        y1 = min(arr.shape[0], math.ceil(center[1]+(height-1)/2)+2)
        if x1 <= x0 or y1 <= y0 or max(arr.shape[:2]) >= 32767:
            rotated = cv2.warpAffine(arr, matrix, (arr.shape[1], arr.shape[0]),
                                     flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT_101)
            return cv2.getRectSubPix(rotated, (width, height), center)
        inverse = cv2.invertAffineTransform(matrix)
        xs = np.arange(x0, x1)
        ys = np.arange(y0, y1)[:, None]
        bits = int(cv2.INTER_BITS)
        affine_bits = max(10, bits)
        scale = 1 << affine_bits
        rounding = scale // (1 << bits) // 2
        maps = []
        for axis in (0, 1):
            xpart = np.rint(inverse[axis,0]*xs*scale).astype(np.int32)
            ypart = np.rint((inverse[axis,1]*ys+inverse[axis,2])*scale).astype(np.int32)
            maps.append(((xpart+ypart+rounding) >> (affine_bits-bits)).astype(np.float32)/(1 << bits))
        rotated = cv2.remap(arr, maps[0], maps[1], cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_REFLECT_101)
        local_center = (float(np.float32(center[0]))-x0, float(np.float32(center[1]))-y0)
        return cv2.getRectSubPix(rotated, (width, height), local_center)


    @staticmethod
    def _rotate_image(image, angle_deg, interpolation=cv2.INTER_LINEAR):
        height, width = image.shape[:2]
        center = ((width - 1) / 2.0, (height - 1) / 2.0)
        matrix = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0)
        return cv2.warpAffine(
            image,
            matrix,
            (width, height),
            flags=interpolation,
            borderMode=cv2.BORDER_REFLECT_101,
        )


    @staticmethod
    def _rotate_image_expanded(
        image, angle_deg, interpolation=cv2.INTER_LINEAR, return_mask=False
    ):
        """Rotate a matching template without clipping its corners."""
        arr = np.asarray(image)
        height, width = arr.shape[:2]
        center = ((width - 1) / 2.0, (height - 1) / 2.0)
        matrix = cv2.getRotationMatrix2D(center, float(angle_deg), 1.0)
        cosine = abs(float(matrix[0, 0]))
        sine = abs(float(matrix[0, 1]))
        rotated_width = max(
            1, int(math.ceil((height * sine + width * cosine) - 1e-9))
        )
        rotated_height = max(
            1, int(math.ceil((height * cosine + width * sine) - 1e-9))
        )
        matrix[0, 2] += ((rotated_width - 1) / 2.0) - center[0]
        matrix[1, 2] += ((rotated_height - 1) / 2.0) - center[1]

        border_pixels = np.concatenate((
            arr[0].reshape(-1), arr[-1].reshape(-1),
            arr[:, 0].reshape(-1), arr[:, -1].reshape(-1),
        ))
        border_value = float(np.median(border_pixels)) if border_pixels.size else 0.0
        rotated = cv2.warpAffine(
            arr,
            matrix,
            (rotated_width, rotated_height),
            flags=interpolation,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=border_value,
        )
        if not return_mask:
            return rotated
        valid_mask = cv2.warpAffine(
            np.full((height, width), 255, dtype=np.uint8),
            matrix,
            (rotated_width, rotated_height),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        )
        return rotated, valid_mask


    @staticmethod
    def _subpixel_peak(score_map, row, col):
        """Quadratic peak refinement, limited to half a pixel per axis."""
        def offset(before, center, after):
            denominator = before - (2.0 * center) + after
            if abs(denominator) < 1e-12:
                return 0.0
            return float(np.clip(0.5 * (before - after) / denominator, -0.5, 0.5))

        dx = 0.0
        dy = 0.0
        if 0 < col < score_map.shape[1] - 1:
            dx = offset(score_map[row, col - 1], score_map[row, col], score_map[row, col + 1])
        if 0 < row < score_map.shape[0] - 1:
            dy = offset(score_map[row - 1, col], score_map[row, col], score_map[row + 1, col])
        return dx, dy


    @staticmethod
    def _quantize_pose_value(value, precision):
        """Round a pose component to an explicit measurement resolution."""
        precision = float(precision)
        if not np.isfinite(precision) or precision <= 0:
            return float(value)
        return float(np.round(float(value) / precision) * precision)


    @staticmethod
    def refine_anchor_point(
        reference_patch,
        current_img,
        predicted_point,
        angle_deg,
        search_radius=10,
        min_score=0.62,
        position_precision=0.1,
    ):
        """Locally re-lock the exact clicked feature after rigid-pose matching.

        The large Edge Assist region solves stable translation and rotation.
        This second, deliberately small match uses the immutable setup-frame
        appearance around the user's selected point to remove a few pixels of
        pose-center bias without allowing unbounded drift.
        """
        reference_u8 = EdgeAlgorithms._normalize_u8(reference_patch)
        if (
            reference_u8.ndim != 2
            or min(reference_u8.shape) < 5
            or float(np.std(reference_u8)) < 4.0
        ):
            return None

        radius = max(2, int(round(search_radius)))
        ref_height, ref_width = reference_u8.shape[:2]
        search_size = (
            ref_width + (2 * radius),
            ref_height + (2 * radius),
        )
        search_patch = EdgeAlgorithms.extract_oriented_patch(
            current_img,
            predicted_point,
            search_size,
            -float(angle_deg),
        )
        search_u8 = EdgeAlgorithms._normalize_u8(search_patch)
        if (
            search_u8.ndim != 2
            or search_u8.shape[0] < ref_height
            or search_u8.shape[1] < ref_width
        ):
            return None

        intensity_map = cv2.matchTemplate(
            search_u8,
            reference_u8,
            cv2.TM_CCOEFF_NORMED,
        )
        intensity_map = np.nan_to_num(
            intensity_map, nan=-1.0, posinf=-1.0, neginf=-1.0
        )
        intensity_unit = np.clip((intensity_map + 1.0) / 2.0, 0.0, 1.0)

        reference_edges = EdgeAlgorithms._soft_edge_image(reference_u8)
        search_edges = EdgeAlgorithms._soft_edge_image(search_u8)
        if (
            np.count_nonzero(reference_edges > 0.05) >= 6
            and np.count_nonzero(search_edges > 0.05) >= 6
        ):
            edge_map = cv2.matchTemplate(
                search_edges,
                reference_edges,
                cv2.TM_CCORR_NORMED,
            )
            edge_map = np.clip(
                np.nan_to_num(edge_map, nan=0.0, posinf=0.0, neginf=0.0),
                0.0,
                1.0,
            )
        else:
            edge_map = np.zeros_like(intensity_unit)

        combined = (0.75 * intensity_unit) + (0.25 * edge_map)
        _, peak, _, max_location = cv2.minMaxLoc(combined)
        col, row = max_location
        # A boundary peak means the requested correction may lie outside this
        # deliberately small safety window; retain the rigid-pose prediction.
        if (
            row == 0
            or col == 0
            or row == combined.shape[0] - 1
            or col == combined.shape[1] - 1
        ):
            return None

        second_map = combined.copy()
        second_map[
            max(0, row - 2):min(second_map.shape[0], row + 3),
            max(0, col - 2):min(second_map.shape[1], col + 3),
        ] = -np.inf
        finite_second = second_map[np.isfinite(second_map)]
        second_peak = float(np.max(finite_second)) if finite_second.size else 0.0
        uniqueness = float(np.clip((float(peak) - second_peak) / 0.08, 0.0, 1.0))
        if float(peak) < float(min_score) or uniqueness < 0.12:
            return None

        subpixel_x, subpixel_y = EdgeAlgorithms._subpixel_peak(
            combined, row, col
        )
        matched_center = np.array([
            float(col) + ((ref_width - 1) / 2.0) + subpixel_x,
            float(row) + ((ref_height - 1) / 2.0) + subpixel_y,
        ])
        search_center = np.array([
            (search_u8.shape[1] - 1) / 2.0,
            (search_u8.shape[0] - 1) / 2.0,
        ])
        local_correction = matched_center - search_center
        radians = math.radians(float(angle_deg))
        cosine = math.cos(radians)
        sine = math.sin(radians)
        global_correction = np.array([
            (cosine * local_correction[0]) + (sine * local_correction[1]),
            (-sine * local_correction[0]) + (cosine * local_correction[1]),
        ])
        refined = np.asarray(predicted_point, dtype=float) + global_correction
        refined = np.array([
            EdgeAlgorithms._quantize_pose_value(
                refined[0], position_precision
            ),
            EdgeAlgorithms._quantize_pose_value(
                refined[1], position_precision
            ),
        ])
        return Data(
            norm_xcorr_map=combined,
            x_pos=float(refined[0]),
            y_pos=float(refined[1]),
            confid_val_ij=float(peak),
            peak_uniqueness=uniqueness,
            angle_deg=float(angle_deg),
            method='Edge Assist Point Lock',
        )


    @staticmethod
    def hybrid_pattern_matcher(
        tpl_img: np.ndarray,
        sa_img: np.ndarray,
        sa_center: tuple,
        sa_rng: tuple,
        reference_angle: float = 0.0,
        rotation_range: float = 180.0,
        rotation_step: float = 2.0,
        edge_weight: float = 0.6,
        edge_threshold: float = None,
        position_precision: float = 0.1,
        angle_precision: float = 0.1,
        search_constraint: dict = None,
        template_cache=None,
    ):
        """Match intensity and edge geometry while solving X, Y and angle.

        This is a lightweight pose matcher inspired by geometric pattern tools:
        it searches rotation coarsely, refines around the best candidate, and
        combines illumination-tolerant intensity correlation with edge overlap.
        """
        try:
            array=np.asarray(tpl_img)
            model_key=('template',array.shape,array.dtype.str,
                       hashlib.sha256(array.tobytes()).digest(),edge_threshold)
            def prepare_template():
                normalized=EdgeAlgorithms._normalize_u8(array)
                return (normalized,
                        EdgeAlgorithms._edge_image(normalized,threshold=edge_threshold,dilate=False),
                        EdgeAlgorithms._soft_edge_image(normalized,threshold=edge_threshold))
            tpl_u8,template_edges_raw,template_soft_edges=(
                template_cache.get(model_key,prepare_template) if template_cache is not None
                else prepare_template())
            sa_u8 = EdgeAlgorithms._normalize_u8(sa_img)
            if tpl_u8.ndim != 2 or sa_u8.ndim != 2:
                raise ValueError('Hybrid matcher requires single-channel images')
            if tpl_u8.shape[0] > sa_u8.shape[0] or tpl_u8.shape[1] > sa_u8.shape[1]:
                raise ValueError('Template must fit inside the search area')

            sa_float = sa_u8.astype(np.float32) / 255.0
            sa_edges_raw = EdgeAlgorithms._edge_image(
                sa_u8, threshold=edge_threshold, dilate=False
            )
            sa_soft_edges = EdgeAlgorithms._soft_edge_image(
                sa_u8, threshold=edge_threshold
            )
            rotation_range = float(np.clip(abs(rotation_range), 0.0, 180.0))
            rotation_step = float(np.clip(abs(rotation_step), 0.25, max(0.25, rotation_range or 0.25)))
            edge_weight = float(np.clip(edge_weight, 0.0, 1.0))
            intensity_weight = 1.0 - edge_weight
            position_precision = float(np.clip(abs(position_precision), 0.01, 1.0))
            angle_precision = float(np.clip(abs(angle_precision), 0.05, 1.0))

            best = None
            search_has_edges = (np.count_nonzero(sa_edges_raw) >= 8
                                and np.count_nonzero(sa_soft_edges) >= 8)

            def edge_density_quality(density):
                """Reject empty and saturated maps without punishing useful geometry."""
                density = float(np.clip(density, 0.0, 1.0))
                if density < 0.008:
                    return 0.60
                if density <= 0.15:
                    return 1.0
                if density <= 0.25:
                    return 1.0 - 0.50 * ((density - 0.15) / 0.10)
                if density <= 0.35:
                    return 0.50 - 0.20 * ((density - 0.25) / 0.10)
                if density <= 0.45:
                    return 0.30 - 0.10 * ((density - 0.35) / 0.10)
                return max(0.12, 0.20 - 0.08 * ((density - 0.45) / 0.55))

            def evaluate(relative_angle):
                # The reinforcement patch is stored level in object-local
                # coordinates. Search absolute pose around the prior frame's
                # angle so the returned angle accumulates through full turns.
                absolute_angle = float(reference_angle) + float(relative_angle)
                def prepare_rotation():
                    rotated_tpl,rotated_mask=EdgeAlgorithms._rotate_image_expanded(tpl_u8,absolute_angle,return_mask=True)
                    raw=EdgeAlgorithms._rotate_image_expanded(template_edges_raw,absolute_angle,interpolation=cv2.INTER_NEAREST)
                    raw[rotated_mask==0]=0
                    soft=EdgeAlgorithms._rotate_image_expanded(template_soft_edges,absolute_angle,interpolation=cv2.INTER_LINEAR)
                    soft[rotated_mask==0]=0.
                    return rotated_tpl,rotated_mask,raw,soft,rotated_tpl.astype(np.float32)/255.
                rotation_key=('rotation',model_key,float(absolute_angle))
                rotated_tpl,rotated_mask,rotated_edges_raw,rotated_soft_edges,tpl_float=(
                    template_cache.get(rotation_key,prepare_rotation) if template_cache is not None
                    else prepare_rotation())
                if rotated_tpl.shape[0]>sa_u8.shape[0] or rotated_tpl.shape[1]>sa_u8.shape[1]:
                    return None

                intensity_map = cv2.matchTemplate(
                    sa_float,
                    tpl_float,
                    cv2.TM_CCOEFF_NORMED,
                    mask=rotated_mask,
                )
                intensity_map = np.nan_to_num(intensity_map, nan=-1.0, posinf=-1.0, neginf=-1.0)
                intensity_unit = np.clip((intensity_map + 1.0) / 2.0, 0.0, 1.0)

                if (
                    np.count_nonzero(rotated_edges_raw) >= 8
                    and search_has_edges
                    and np.count_nonzero(rotated_soft_edges) >= 8
                ):
                    edge_map = cv2.matchTemplate(
                        sa_soft_edges,
                        rotated_soft_edges,
                        cv2.TM_CCORR_NORMED,
                        mask=rotated_mask,
                    )
                    edge_map = np.nan_to_num(edge_map, nan=0.0, posinf=0.0, neginf=0.0)
                    edge_map = np.clip(edge_map, 0.0, 1.0)
                else:
                    edge_map = np.zeros_like(intensity_unit)

                combined = intensity_weight * intensity_unit + edge_weight * edge_map
                tpl_h, tpl_w = rotated_tpl.shape[:2]
                if search_constraint is not None:
                    xs=np.arange(combined.shape[1])[None,:]+(tpl_w-1)/2+sa_center[0]-math.floor(sa_rng[0]/2)
                    ys=np.arange(combined.shape[0])[:,None]+(tpl_h-1)/2+sa_center[1]-math.floor(sa_rng[1]/2)
                    allowed=candidate_search_mask(xs,ys,absolute_angle,search_constraint)
                    if not np.any(allowed):return None
                    combined=np.where(allowed,combined,-1e6).astype(np.float32)
                _, raw_peak, _, max_loc = cv2.minMaxLoc(combined)
                col, row = max_loc

                candidate_edges = sa_edges_raw[row:row + tpl_h, col:col + tpl_w]
                template_mask = rotated_edges_raw > 0
                valid_template_mask = rotated_mask > 0
                candidate_mask = (candidate_edges > 0) & valid_template_mask
                template_count = int(np.count_nonzero(template_mask))
                candidate_count = int(np.count_nonzero(candidate_mask))
                if template_count and candidate_count:
                    template_tolerance = cv2.dilate(
                        template_mask.astype(np.uint8),
                        np.ones((3, 3), dtype=np.uint8),
                        iterations=1,
                    ) > 0
                    candidate_tolerance = cv2.dilate(
                        candidate_mask.astype(np.uint8),
                        np.ones((3, 3), dtype=np.uint8),
                        iterations=1,
                    ) > 0
                    precision = np.count_nonzero(candidate_mask & template_tolerance) / candidate_count
                    recall = np.count_nonzero(template_mask & candidate_tolerance) / template_count
                    edge_f1 = (
                        2.0 * precision * recall / (precision + recall)
                        if precision + recall > 0
                        else 0.0
                    )
                else:
                    edge_f1 = 0.0

                valid_count = int(np.count_nonzero(valid_template_mask))
                template_density = template_count / float(max(1, valid_count))
                candidate_density = candidate_count / float(max(1, valid_count))
                edge_density = max(template_density, candidate_density)
                density_quality = edge_density_quality(edge_density)

                # A real lock should produce a distinct peak. Repetitive noise
                # tends to produce many similarly good candidates.
                second_map = combined.copy()
                exclusion_x = max(2, tpl_w // 4)
                exclusion_y = max(2, tpl_h // 4)
                second_map[
                    max(0, row - exclusion_y):min(second_map.shape[0], row + exclusion_y + 1),
                    max(0, col - exclusion_x):min(second_map.shape[1], col + exclusion_x + 1),
                ] = -np.inf
                finite_second = second_map[np.isfinite(second_map)]
                second_peak = float(np.max(finite_second)) if finite_second.size else 0.0
                peak_uniqueness = float(np.clip((raw_peak - second_peak) / 0.10, 0.0, 1.0))

                edge_correlation = float(edge_map[row, col])
                geometry_score = (0.55 * edge_correlation) + (0.45 * edge_f1)
                intensity_score = float(intensity_unit[row, col])
                raw_similarity = (
                    intensity_weight * intensity_score
                    + edge_weight * geometry_score
                )

                candidate_center = np.array([
                    col + ((tpl_w - 1) / 2.0),
                    row + ((tpl_h - 1) / 2.0),
                ])
                search_center = np.array([
                    (sa_u8.shape[1] - 1) / 2.0,
                    (sa_u8.shape[0] - 1) / 2.0,
                ])
                distance = float(np.linalg.norm(candidate_center - search_center))
                continuity_scale = max(1.0, 0.30 * min(sa_u8.shape[:2]))
                continuity = 0.85 + 0.15 * math.exp(-((distance / continuity_scale) ** 2))
                rotation_scale = max(1.0, min(30.0, rotation_range or 1.0))
                rotation_continuity = 0.98 + 0.02 * math.exp(
                    -((float(relative_angle) / rotation_scale) ** 2)
                )
                score = (
                    raw_similarity
                    * density_quality
                    * (0.78 + 0.22 * peak_uniqueness)
                    * continuity
                    * rotation_continuity
                )
                return {
                    'relative_angle': float(relative_angle),
                    'absolute_angle': absolute_angle,
                    'score': float(score),
                    'row': int(row),
                    'col': int(col),
                    'template_height': int(tpl_h),
                    'template_width': int(tpl_w),
                    'combined': combined,
                    'intensity': intensity_score,
                    'edge': edge_correlation,
                    'raw_similarity': float(raw_similarity),
                    'peak_uniqueness': peak_uniqueness,
                    'edge_density': float(edge_density),
                    'edge_f1': float(edge_f1),
                    'continuity': float(continuity),
                    'rotation_continuity': float(rotation_continuity),
                }

            def scan_angles(angles):
                nonlocal best
                for relative_angle in angles:
                    candidate = evaluate(relative_angle)
                    if candidate is None:
                        continue
                    if best is None or candidate['score'] > best['score']:
                        best = candidate

            active_coarse_step = rotation_step
            if rotation_range == 0:
                scan_angles([0.0])
            elif rotation_range > 30.0:
                # Adjacent Cine frames normally rotate only a few degrees.
                # Search close to the prior pose first, then retain the full
                # ±180° recovery path for abrupt motion or a weak local lock.
                local_range = min(20.0, rotation_range)
                scan_angles(np.arange(
                    -local_range,
                    local_range + (rotation_step * 0.5),
                    rotation_step,
                ))
                if best is None or best['score'] < 0.72:
                    active_coarse_step = max(rotation_step, 4.0)
                    scan_angles(np.arange(
                        -rotation_range,
                        rotation_range + (active_coarse_step * 0.5),
                        active_coarse_step,
                    ))
            else:
                scan_angles(np.arange(
                    -rotation_range,
                    rotation_range + (rotation_step * 0.5),
                    rotation_step,
                ))

            if best is None:
                raise ValueError('Rotated template does not fit inside the search area')

            fine_step = None
            if rotation_range > 0 and active_coarse_step > 0.5:
                fine_step = max(0.25, active_coarse_step / 4.0)
                fine_start = max(
                    -rotation_range, best['relative_angle'] - active_coarse_step
                )
                fine_stop = min(
                    rotation_range, best['relative_angle'] + active_coarse_step
                )
                for relative_angle in np.arange(fine_start, fine_stop + (fine_step * 0.5), fine_step):
                    candidate = evaluate(relative_angle)
                    if candidate is not None and candidate['score'] > best['score']:
                        best = candidate

            # Keep the broad rotation search practical, then resolve the final
            # pose locally at the requested angular precision. With the default
            # settings this adds only 11 nearby evaluations instead of scanning
            # the full ±180° range in 0.1° increments.
            if rotation_range > 0:
                refinement_span = max(
                    angle_precision,
                    fine_step if fine_step is not None else min(active_coarse_step, 0.5),
                )
                precision_start = max(
                    -rotation_range,
                    best['relative_angle'] - refinement_span,
                )
                precision_stop = min(
                    rotation_range,
                    best['relative_angle'] + refinement_span,
                )
                scan_angles(np.arange(
                    precision_start,
                    precision_stop + (angle_precision * 0.5),
                    angle_precision,
                ))

            dx, dy = EdgeAlgorithms._subpixel_peak(
                best['combined'], best['row'], best['col']
            )
            tpl_h = best['template_height']
            tpl_w = best['template_width']
            sa_tl = (
                float(sa_center[0]) - math.floor(sa_rng[0] / 2),
                float(sa_center[1]) - math.floor(sa_rng[1] / 2),
            )
            x_pos = sa_tl[0] + best['col'] + ((tpl_w - 1) / 2.0) + dx
            y_pos = sa_tl[1] + best['row'] + ((tpl_h - 1) / 2.0) + dy
            x_pos = EdgeAlgorithms._quantize_pose_value(
                x_pos, position_precision
            )
            y_pos = EdgeAlgorithms._quantize_pose_value(
                y_pos, position_precision
            )
            angle_deg = EdgeAlgorithms._quantize_pose_value(
                best['absolute_angle'], angle_precision
            )

            if search_constraint is not None:
                x_pos,y_pos=constrain_match_center(x_pos,y_pos,angle_deg,search_constraint)
                if not candidate_search_mask(x_pos,y_pos,angle_deg,search_constraint):
                    # A subpixel fit near the boundary may leave the allowed
                    # region. Retain the measured integer peak in that case.
                    x_pos=sa_tl[0]+best['col']+(tpl_w-1)/2
                    y_pos=sa_tl[1]+best['row']+(tpl_h-1)/2
                    x_pos,y_pos=constrain_match_center(x_pos,y_pos,angle_deg,search_constraint)
                    if not candidate_search_mask(x_pos,y_pos,angle_deg,search_constraint):
                        raise ValueError('No match satisfies the search geometry and motion constraint')
            return Data(
                norm_xcorr_map=best['combined'],
                x_pos=float(x_pos),
                y_pos=float(y_pos),
                confid_val_ij=best['score'],
                angle_deg=angle_deg,
                intensity_score=best['intensity'],
                edge_score=best['edge'],
                raw_similarity=best['raw_similarity'],
                peak_uniqueness=best['peak_uniqueness'],
                edge_density=best['edge_density'],
                edge_f1=best['edge_f1'],
                continuity_score=best['continuity'],
                method='Hybrid',
            )
        except Exception as e:
            raise EdgeTrackingError(f'hybrid_pattern_matcher() encountered an error\n{e}')


class EdgeAssistTracker:
    """Portable sequential rigid-point tracker built from the same PCA-used core.

    initialize(image, point, region_size, region_center=None, angle=0, frame=0)
    selects an exact measurement point and an optional offset rigid fixture.
    step(image, frame) returns an EdgeObservation. One instance per direction;
    loss never returns a fabricated point. No cameras/files/GUI are required.
    """
    def __init__(self,settings=None):
        self.settings=settings or EdgeSettings();self.ready=False;self.misses=0;self.direction=0
        self.template_cache=EdgeMatchCache()

    @staticmethod
    def _gray(image):
        a=np.asarray(image,dtype=np.float32)
        if a.ndim==3 and a.shape[2]==3:a=cv2.cvtColor(a,cv2.COLOR_RGB2GRAY)
        if a.ndim!=2 or min(a.shape)<9 or not np.all(np.isfinite(a)):raise ValueError('Use a finite gray or RGB image.')
        return a

    @staticmethod
    def _rotate(offset,angle):
        c=math.cos(math.radians(angle));s=math.sin(math.radians(angle));x,y=offset
        return np.array([c*x+s*y,-s*x+c*y])

    @classmethod
    def _visible(cls,center,size,angle,shape):
        corners=np.asarray([[-(size[0]-1)/2,-(size[1]-1)/2],[(size[0]-1)/2,-(size[1]-1)/2],[(size[0]-1)/2,(size[1]-1)/2],[-(size[0]-1)/2,(size[1]-1)/2]])
        points=np.asarray([cls._rotate(c,angle)+center for c in corners])
        return bool(np.all(points>=0) and np.all(points[:,0]<=shape[1]-1) and np.all(points[:,1]<=shape[0]-1))

    def initialize(self,image,point,region_size,region_center=None,angle=0.,frame=0):
        a=self._gray(image);self.shape=a.shape
        self.template_cache=EdgeMatchCache()
        self.size=tuple(map(int,region_size));self.point=np.asarray(point,dtype=float)
        center=np.asarray(region_center if region_center is not None else point,dtype=float)
        if len(self.size)!=2 or min(self.size)<9 or self.point.shape!=(2,) or not np.all(np.isfinite(self.point)) or not math.isfinite(angle):raise ValueError('Invalid point or fixture size.')
        if not self._visible(center,self.size,angle,a.shape):raise ValueError('The fixture must fit within the setup image.')
        self.offset=self._rotate(center-self.point,-angle)
        self.setup=EdgeAlgorithms.extract_oriented_patch(a,center,self.size,-angle)
        if np.std(self.setup)<1e-6:raise ValueError('Select distinctive, nonuniform geometry.')
        self.neighbor=self.setup.copy();self.angle=float(angle)
        self.anchor_size=int(np.clip(round(min(self.size)*.2),15,31))|1
        self.anchor=EdgeAlgorithms.extract_oriented_patch(a,self.point,(self.anchor_size,)*2,-angle)
        self.frame=int(frame);self.last_attempt=int(frame);self.misses=0;self.direction=0;self.ready=True
        return EdgeObservation(self.frame,True,tuple(self.point),self.angle,1.,'Setup point')

    def step(self,image,frame):
        if not self.ready:raise ValueError('Initialize before stepping.')
        frame=int(frame);direction=int(np.sign(frame-self.last_attempt))
        if not direction or (self.direction and direction!=self.direction):raise ValueError('Frames must advance monotonically.')
        self.last_attempt=frame;self.direction=direction;s=self.settings
        if self.misses>=s.miss_limit:return EdgeObservation(frame,False,None,None,0.,'Stopped; reinitialize at a clear frame.')
        a=self._gray(image)
        if a.shape!=self.shape:raise ValueError('Image dimensions changed.')
        center=self.point+self._rotate(self.offset,self.angle)
        base=math.ceil(math.hypot(*self.size)) if s.rotation_range else max(self.size)
        width=min(a.shape[1],int(math.ceil(base*s.search_multiplier))|1)
        height=min(a.shape[0],int(math.ceil(base*s.search_multiplier))|1)
        x=int(np.clip(round(center[0]-width//2),0,a.shape[1]-width))
        y=int(np.clip(round(center[1]-height//2),0,a.shape[0]-height))
        options=dict(rotation_step=s.rotation_step,edge_weight=s.edge_weight,edge_threshold=s.edge_threshold,
                     position_precision=s.position_precision,angle_precision=s.angle_precision,
                     template_cache=self.template_cache)
        try:
            pose=EdgeAlgorithms.hybrid_pattern_matcher(self.setup,a[y:y+height,x:x+width],(x+width//2,y+height//2),(width,height),self.angle,s.rotation_range,**options)
            candidate_center=np.array([pose.x_pos,pose.y_pos]);angle=pose.angle_deg
            if not self._visible(candidate_center,self.size,angle,a.shape):raise EdgeTrackingError('Fixture clipped by image edge.')
            patch=EdgeAlgorithms.extract_oriented_patch(a,candidate_center,self.size,-angle)
            local=((self.size[0]-1)/2,(self.size[1]-1)/2)
            adjacent=EdgeAlgorithms.hybrid_pattern_matcher(self.neighbor,patch,local,self.size,rotation_range=0,**options).confid_val_ij
            setup=EdgeAlgorithms.hybrid_pattern_matcher(self.setup,patch,local,self.size,rotation_range=0,**options).confid_val_ij
            score=max(0.,adjacent)**s.neighbor_weight*max(0.,setup)**(1-s.neighbor_weight)
            components=dict(neighbor=float(adjacent),setup=float(setup),point_lock=False)
            if score<s.confidence:
                self.misses+=1;return EdgeObservation(frame,False,None,None,float(score),'Below acceptance threshold.',components)
            point=candidate_center-self._rotate(self.offset,angle)
            if s.point_lock:
                locked=EdgeAlgorithms.refine_anchor_point(self.anchor,a,point,angle,search_radius=int(np.clip(round(self.anchor_size*.4),5,12)),position_precision=s.position_precision)
                if locked is not None:point=np.asarray([locked.x_pos,locked.y_pos]);components['point_lock']=True
            self.point=point;self.angle=angle;self.neighbor=patch;self.frame=frame;self.misses=0
            return EdgeObservation(frame,True,tuple(map(float,point)),float(angle),float(score),'Accepted',components)
        except EdgeTrackingError as error:
            self.misses+=1;return EdgeObservation(frame,False,None,None,0.,str(error))
