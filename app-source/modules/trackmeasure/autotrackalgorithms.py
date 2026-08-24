import math
import cv2
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

class AutoTrackAlgorithms:
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
        image_u8 = AutoTrackAlgorithms._normalize_u8(image)
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
        image_u8 = AutoTrackAlgorithms._normalize_u8(image)
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
        rotated = cv2.warpAffine(
            arr,
            matrix,
            (arr.shape[1], arr.shape[0]),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        return cv2.getRectSubPix(rotated, (width, height), center)

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
    ):
        """Match intensity and edge geometry while solving X, Y and angle.

        This is a lightweight pose matcher inspired by geometric pattern tools:
        it searches rotation coarsely, refines around the best candidate, and
        combines illumination-tolerant intensity correlation with edge overlap.
        """
        try:
            tpl_u8 = AutoTrackAlgorithms._normalize_u8(tpl_img)
            sa_u8 = AutoTrackAlgorithms._normalize_u8(sa_img)
            if tpl_u8.ndim != 2 or sa_u8.ndim != 2:
                raise ValueError('Hybrid matcher requires single-channel images')
            if tpl_u8.shape[0] > sa_u8.shape[0] or tpl_u8.shape[1] > sa_u8.shape[1]:
                raise ValueError('Template must fit inside the search area')

            sa_float = sa_u8.astype(np.float32) / 255.0
            sa_edges_raw = AutoTrackAlgorithms._edge_image(
                sa_u8, threshold=edge_threshold, dilate=False
            )
            sa_soft_edges = AutoTrackAlgorithms._soft_edge_image(
                sa_u8, threshold=edge_threshold
            )
            template_edges_raw = AutoTrackAlgorithms._edge_image(
                tpl_u8, threshold=edge_threshold, dilate=False
            )
            template_soft_edges = AutoTrackAlgorithms._soft_edge_image(
                tpl_u8, threshold=edge_threshold
            )
            rotation_range = float(np.clip(abs(rotation_range), 0.0, 180.0))
            rotation_step = float(np.clip(abs(rotation_step), 0.25, max(0.25, rotation_range or 0.25)))
            edge_weight = float(np.clip(edge_weight, 0.0, 1.0))
            intensity_weight = 1.0 - edge_weight
            position_precision = float(np.clip(abs(position_precision), 0.01, 1.0))
            angle_precision = float(np.clip(abs(angle_precision), 0.05, 1.0))

            best = None

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
                rotated_tpl, rotated_mask = AutoTrackAlgorithms._rotate_image_expanded(
                    tpl_u8, absolute_angle, return_mask=True
                )
                if (
                    rotated_tpl.shape[0] > sa_u8.shape[0]
                    or rotated_tpl.shape[1] > sa_u8.shape[1]
                ):
                    return None
                rotated_edges_raw = AutoTrackAlgorithms._rotate_image_expanded(
                    template_edges_raw,
                    absolute_angle,
                    interpolation=cv2.INTER_NEAREST,
                )
                rotated_edges_raw[rotated_mask == 0] = 0
                rotated_soft_edges = AutoTrackAlgorithms._rotate_image_expanded(
                    template_soft_edges,
                    absolute_angle,
                    interpolation=cv2.INTER_LINEAR,
                )
                rotated_soft_edges[rotated_mask == 0] = 0.0
                tpl_float = rotated_tpl.astype(np.float32) / 255.0

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
                    and np.count_nonzero(sa_edges_raw) >= 8
                    and np.count_nonzero(rotated_soft_edges) >= 8
                    and np.count_nonzero(sa_soft_edges) >= 8
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
                _, raw_peak, _, max_loc = cv2.minMaxLoc(combined)
                col, row = max_loc
                tpl_h, tpl_w = rotated_tpl.shape[:2]

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

            dx, dy = AutoTrackAlgorithms._subpixel_peak(
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
            x_pos = AutoTrackAlgorithms._quantize_pose_value(
                x_pos, position_precision
            )
            y_pos = AutoTrackAlgorithms._quantize_pose_value(
                y_pos, position_precision
            )
            angle_deg = AutoTrackAlgorithms._quantize_pose_value(
                best['absolute_angle'], angle_precision
            )

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
            raise AutoTrackException(f'hybrid_pattern_matcher() encountered an error\n{e}')
        
# EXCEPTIONS
class AutoTrackException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
