from abc import ABC, abstractmethod
from calibration import CalSpace
from PySide6.QtGui import *
import numpy as np
from autotrackalgorithms import AutoTrackAlgorithms, AutoTrackException
from collections import OrderedDict
import logging
import math

SUBPIXEL_VALS = {'1.0 pix': 1, '1/2 pix': 2, '1/3 pix': 3, '1/4 pix': 4, '1/5 pix': 5, '1/6 pix': 6, '1/7 pix': 7, '1/8 pix': 8, '1/9 pix': 9, '1/10 pix': 10}
HYBRID_TRACKING_METHOD = 'Hybrid (Edge + Intensity)'
CLASSIC_TRACKING_METHOD = 'Classic (Intensity Only)'
DEFAULT_HYBRID_SEARCH_MULTIPLIER = 1.5
DEFAULT_HYBRID_MATCH_THRESHOLD = 0.40


def _rotate_tracking_offset(offset, angle_deg):
    """Rotate a point-to-template offset using the tracker's image convention."""
    dx, dy = float(offset[0]), float(offset[1])
    radians = math.radians(float(angle_deg))
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return np.array([
        (cosine * dx) + (sine * dy),
        (-sine * dx) + (cosine * dy),
    ], dtype=float)

class BaseTool(ABC):
    def __init__(self, vm):
        logging.info(f'Initializing {self.__class__.__name__}')
        self.vm = vm
        self._point_target = -1
        self.points = []
        self.phys_pts = []
        self.points_frame_ids = []
        self._connect_at_end = False
        self._connect_pairs = False
        self._connect_box_from_two_points = False
        self._template_sz = 64
        self._draw_label = False
        # Order for Hex Codes is ROYGBCM (Last Two are Cyan and Magenta)
        self.colors_hex_codes = ['#ff0000','#ffa500','#ffff00','#008000','#0000ff','#00ffff','#ff00ff']
        
    def collect_points(self, point, frame_id):
        point = (int(point[0]), int(point[1]))
        self.points.append(point)
        self.points_frame_ids.append(frame_id)
        
        if len(self.points) >= self._point_target and self._point_target != -1:
            self.evaluate()
    
    def cleanup(self):
        self.points_frame_ids = []
        
    def evaluate(self):
        logging.info(f'Evaluating points in {self.__class__.__name__}')
        # MUST override, do evaluation on points here
        try:
            self.vm.cal.update_cal(1, self.vm.image_scale)
            self.phys_pts = self.vm.cal.point_transform(self.points, going_to=CalSpace.PHYSICAL)
        except TypeError as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'Calibration Error: {e}')
            return
        except ValueError as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'Calibration Error: {e}')
            return
        
        
    def clear_points(self):
        val = len(self.points) >= self._point_target
        if val:
            self.points = []
        return val
    

class ScaleTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self._point_target = 2
        
    def evaluate(self):
        super().evaluate()
        self.vm.get_new_scale.emit(self.apply_scale, self)
            
    def apply_scale(obj, self, known_length, length_units):
        # this is a callback to the signal, use self from second arg as it wll be passed in
        try:
            deltaX_pix, deltaY_pix, displacement_pix = self.vm.meas.meas_displacement(self.points) 
            if displacement_pix == 0:
                raise ValueError('Displacement cannot be zero. Please select two points that are not the same.')
            if known_length <= 0:
                raise ValueError('Known length must be a positive, non-zero value.')
            if length_units == '':
                raise ValueError('Please select a length unit for the known length.')
            self.vm.known_length = known_length
            self.vm.length_units = length_units
            
            self.vm.image_scale = float(self.vm.known_length) / displacement_pix  
            self.vm.cal.phys_factor = self.vm.image_scale
            self.vm.image_scale_text =  self.vm.format_scale_text(self.vm.cal.phys_factor)
            self.vm.update_status_text.emit(f'Image Scale: {self.vm.image_scale_text}\nΔX: {round(deltaX_pix,2)} pix\nΔY: {round(deltaY_pix,2)} pix')
            
            # add the new measurements to the dictionary
            d = None
            i = self.vm.meas_data['_curr_i']
            try:
                d = self.vm.meas_data['Scale Tool']
            except:
                d = OrderedDict({'ID': [], 'X0': [], 'Y0': [], 'X1':[], 'Y1':[], 'Scale':[], 'Known Length':[], 'Units':[]})
            d['ID'].append(i)
            d['X0'].append(self.points[0][0])
            d['Y0'].append(self.points[0][1])
            d['X1'].append(self.points[1][0])
            d['Y1'].append(self.points[1][1])
            d['Scale'].append(self.vm.image_scale)
            d['Known Length'].append(known_length)
            d['Units'].append(self.vm.length_units)
            self.vm.meas_data['Scale Tool'] = d
            self.vm.meas_data['_curr_i'] = i + 1

            self.cleanup()
            
        except Exception as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'{e}')
    
    def cleanup(self):
        super().cleanup()
        # clear this tool, don't need to keep it selected
        self.vm.track_fig_changed.emit(self.vm.track_data)
        self.vm.active_tool = None
        
            
class TrackTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self.track_select = False

    def cleanup(self, track_all=None):
        # send process complete message
        self.vm.process_tool_complete.emit()
        return super().cleanup()

    def collect_points(self, point, frame_id, track_all=False):
        """
        takes a user entered point, frame_id and saves it to the track_data dictionary.
        if track_select, then it should make a new object instead
        """
        if self.track_select:
            # make a new object
            self.track_select = False
            new_id = len(self.vm.track_data)
            tracking_method = getattr(self.vm, 'pending_tracking_method', None) or CLASSIC_TRACKING_METHOD
            is_hybrid = tracking_method == HYBRID_TRACKING_METHOD
            score = 'N/A' 
            new_t = {'id': new_id, 'name': f'Object {new_id}', 'points': np.array([point]), 'frames': np.array([frame_id]), 
                     'scores': np.array([score]), 'angles': np.array([0.0]),
                     't_points': np.array([point]), 't_frames': np.array([frame_id]), 't_angles': np.array([0.0]), 'notes': {}, 
                     'origin_frame': None, 'relative_to': None, 'enabled': True, 'color': self.colors_hex_codes[new_id % len(self.colors_hex_codes)], 
                     'start': int(self.vm.cine_handler.metadata.FirstImageNo), 
                     'end': int(self.vm.cine_handler.metadata.FirstImageNo + self.vm.cine_handler.metadata.ImageCount - 1), 
                     'search_area':(101,101), 'tpl_rng': (31,31),
                     'subpixel_size': '1/10 pix',
                     'subpixel_type': 'cubic',
                     'frames_enable': True, 'search_area_enable': True, 'tpl_rng_enable':True, 
                     'update_template_enable': not is_hybrid,
                     'acceptable_score': DEFAULT_HYBRID_MATCH_THRESHOLD if is_hybrid else 0.6,
                     'tpl_score': 0.8,
                     'tracking_method': tracking_method, 'rotation_range': 180.0,
                     'rotation_step': 2.0, 'edge_weight': 0.6, 'edge_threshold': 0.30,
                     'position_precision': 0.1, 'angle_precision': 0.1,
                     'rotation_allowed': is_hybrid, 'smart_frames': is_hybrid,
                     'smart_miss_limit': 3,
                     'search_area_multiplier': DEFAULT_HYBRID_SEARCH_MULTIPLIER,
                     'adjacent_confidence_weight': 0.65,
                     'anchor_refinement_enabled': is_hybrid,
                     'confidence_components': {},
                     'template_angle': 0.0, 'template_offset': (0.0, 0.0),
                     'anchor_frame': int(frame_id),
                     'frame_number_offset': int(
                         self.vm.cine_handler.metadata.FirstImageNo
                     )}
            if is_hybrid:
                new_t['hybrid_candidates'] = {
                    int(frame_id): {
                        'point': (float(point[0]), float(point[1])),
                        'score': 'N/A',
                        'angle': 0.0,
                    }
                }
            self.vm.track_data[new_id] = new_t
            self.vm.active_object = new_id
            self.draw_all(points=point)
            self.vm.update_status_text.emit('')

        else:
            # add new points to track_data dictionary for active_object ID
            t_ids = []
            if track_all:
                t_ids = [i for i in self.vm.track_data.keys() if self.vm.track_data[i]['enabled']]
            elif self.vm.active_object != None:
                t_ids = [self.vm.active_object]
            if len(t_ids) > 0:
                for id in t_ids:
                    self.vm.format_progress.emit(self.vm.track_data[id]['name'])
                    self.process_template(self.vm.track_data[id], point, frame_id)
                self.draw_all(points=point)
                self.cleanup(track_all=track_all)  # return here to avoid double cleanup?
            else:
                self.vm.update_status_text.emit("Select an object from the list or add a new object to add a new position")
        super().cleanup()

    def draw_all(self, points=None):
        # get points from track_data dictionary, then call parent draw_all
        for t_id in list(self.vm.track_data.keys()):
            if self.vm.track_data[t_id]['enabled'] == True:
                t = self.vm.track_data[t_id]
                points = t['points']
                # only show previously added points when compared to active frame
                frames = t['frames']
                in_range = frames <=  self.vm.active_frame
                if not np.all(in_range):
                    i = np.argmin(in_range)
                    points = points[:i]
                draw_track_path = getattr(self.vm, 'draw_track_path', None)
                if callable(draw_track_path):
                    draw_track_path(points, t['color'], t_id)
                else:
                    self.vm.draw_points_lines(points, t['color'])
                # draw track elements for all
                if self.vm.track_type == 'Auto':
                    if t['tpl_rng_enable']:
                        self.vm.draw_roi.emit(t['tpl_rng'], t_id, 'template')
                    if t['search_area_enable']:
                        self.vm.draw_roi.emit(t['search_area'], t_id, 'search_area')
            
    def clear_points(self):
        return False
    
    def process_template(self, template, point, frame_id):
        pass

    def add_point_to_template(self, t, point, frame_id, score='N/A', update_track_template=True, angle=0.0):
        """
        will always add a new point to the template dictionaries lists: points, frameID and score.
        if update_track_template: it will additionally add to the 't_<keyname>' for the track template lists
        """

        point_key = 'points'
        frame_key = 'frames'
        angle_key = 'angles'
        if 'angles' not in t or len(t['angles']) != len(t['points']):
            t['angles'] = np.zeros(len(t['points']), dtype=float)
        if 't_angles' not in t or len(t['t_angles']) != len(t['t_points']):
            t['t_angles'] = np.zeros(len(t['t_points']), dtype=float)

        for i in range(2):
            if i == 1:
                point_key = f't_{point_key}'
                frame_key = f't_{frame_key}'
                angle_key = 't_angles'
                if not update_track_template: break 
        
            m_arr = np.argwhere(t[frame_key]==frame_id)
            if m_arr.size > 0: 
                m = m_arr[0,0]
                # replace if match at frame id
                t[point_key][m] = np.array(point)
                t[frame_key][m] = frame_id
                t[angle_key][m] = float(angle)
                if i == 0:
                    t['scores'][m] = score
            else:
                # append new point
                t[point_key] = np.append(t[point_key], np.array([point]), axis=0)
                t[frame_key] = np.append(t[frame_key], frame_id)
                t[angle_key] = np.append(t[angle_key], float(angle))
                if i == 0:
                    t['scores'] = np.append(t['scores'], score)
            # sort by frame id
            ind = np.argsort(t[frame_key])
            t[point_key] = t[point_key][ind]
            t[frame_key] = t[frame_key][ind]
            t[angle_key] = t[angle_key][ind]
            if i == 0:
                t['scores'] = t['scores'][ind]

        return t

    @staticmethod
    def _score_is_numeric(score):
        try:
            return bool(np.isfinite(float(score)))
        except (TypeError, ValueError):
            return False

    @classmethod
    def prepare_hybrid_candidates(cls, track):
        """Reset a Hybrid rerun to user-confirmed points and retain raw results."""
        candidates = {}
        frames = np.asarray(track.get('frames', []), dtype=int)
        points = np.asarray(track.get('points', []), dtype=float)
        scores = np.asarray(track.get('scores', []), dtype=object)
        angles = np.asarray(
            track.get('angles', np.zeros(len(frames))), dtype=float
        )
        for index, frame in enumerate(frames):
            score = scores[index] if index < len(scores) else 'N/A'
            if not cls._score_is_numeric(score):
                candidates[int(frame)] = {
                    'point': tuple(float(value) for value in points[index]),
                    'score': 'N/A',
                    'angle': float(angles[index]) if index < len(angles) else 0.0,
                }
        anchor_frame = int(track.get('anchor_frame', frames[0] if len(frames) else 0))
        if anchor_frame not in candidates and len(frames):
            matches = np.flatnonzero(frames == anchor_frame)
            anchor_index = int(matches[0]) if matches.size else 0
            anchor_score = (
                scores[anchor_index]
                if anchor_index < len(scores)
                and cls._score_is_numeric(scores[anchor_index])
                else 'N/A'
            )
            candidates[anchor_frame] = {
                'point': tuple(float(value) for value in points[anchor_index]),
                'score': anchor_score,
                'angle': float(angles[anchor_index]) if anchor_index < len(angles) else 0.0,
            }
        track['hybrid_candidates'] = candidates
        track['confidence_components'] = {}
        track.pop('boundary_reasons', None)
        cls.apply_hybrid_threshold(track)

    @staticmethod
    def record_hybrid_candidate(track, point, frame_id, score, angle):
        track.setdefault('hybrid_candidates', {})[int(frame_id)] = {
            'point': (float(point[0]), float(point[1])),
            'score': float(score),
            'angle': float(angle),
        }

    @classmethod
    def apply_hybrid_threshold(cls, track):
        """Rebuild the public series from stored candidates at the live cutoff."""
        if track.get('tracking_method') != HYBRID_TRACKING_METHOD:
            return False
        candidates = track.get('hybrid_candidates')
        if not candidates:
            return False
        threshold = float(track.get(
            'acceptable_score', DEFAULT_HYBRID_MATCH_THRESHOLD
        ))
        old_frames = np.asarray(track.get('frames', []), dtype=int)
        old_notes = dict(track.get('notes', {}))
        notes_by_frame = track.setdefault('hybrid_notes_by_frame', {})
        notes_by_frame.update({
            int(old_frames[index]): note
            for index, note in old_notes.items()
            if isinstance(index, (int, np.integer))
            and 0 <= int(index) < len(old_frames)
        })
        visible = []
        for frame, candidate in sorted(candidates.items()):
            score = candidate.get('score', 'N/A')
            if not cls._score_is_numeric(score) or float(score) >= threshold:
                visible.append((int(frame), candidate))
        if not visible:
            return False
        track['frames'] = np.asarray([frame for frame, _ in visible], dtype=int)
        track['points'] = np.asarray(
            [candidate['point'] for _, candidate in visible], dtype=float
        )
        track['scores'] = np.asarray(
            [candidate.get('score', 'N/A') for _, candidate in visible],
            dtype=object,
        )
        track['angles'] = np.asarray(
            [candidate.get('angle', 0.0) for _, candidate in visible], dtype=float
        )
        track['notes'] = {
            index: notes_by_frame[frame]
            for index, frame in enumerate(track['frames'])
            if int(frame) in notes_by_frame
        }
        if track.get('smart_frames', False):
            frame_offset = int(track.get('frame_number_offset', 0))
            track['start'] = frame_offset + int(np.min(track['frames']))
            track['end'] = frame_offset + int(np.max(track['frames']))
            track['smart_detected_start'] = track['start']
            track['smart_detected_end'] = track['end']
        numeric_scores = sum(
            cls._score_is_numeric(candidate.get('score'))
            for _, candidate in visible
        )
        track['threshold_visible_count'] = int(numeric_scores)
        track['threshold_candidate_count'] = int(sum(
            cls._score_is_numeric(candidate.get('score'))
            for candidate in candidates.values()
        ))
        return True

class ManualTrackTool(TrackTool):
    def process_template(self, template, point, frame_id):
        point = (round(point[0]), round(point[1]))
        self.add_point_to_template(template, point, frame_id)
        self.cleanup()
        return template

class AutoTrackTool(TrackTool):
    def __init__(self, vm):
        self._curr_fr = None
        self.fail_cnt = 0
        super().__init__(vm)

    def process_template(self, template, point, frame_id):
        self.fail_cnt = 0
        if point == (-1,-1):
            if template.get('tracking_method') == HYBRID_TRACKING_METHOD:
                self.prepare_hybrid_candidates(template)
            # parameterize dictionary items
            self.vm.update_status_text.emit(f'Start autotrack of object {template["name"]}')
            smart_frames = bool(
                template.get('smart_frames', False)
                and template.get('tracking_method') == HYBRID_TRACKING_METHOD
            )
            if smart_frames:
                anchor_fr = int(template.get('anchor_frame', template['frames'][0]))
                anchor_fr = int(np.clip(
                    anchor_fr,
                    0,
                    self.vm.cine_handler.metadata.ImageCount - 1,
                ))
                # Search away from the user-confirmed anchor in both directions.
                # Each direction stops independently after the configured number
                # of consecutive low-confidence frames. The anchor is not
                # matched against itself, because a self-correlation always
                # looks artificially perfect and is not useful confidence data.
                final_frame = self.vm.cine_handler.metadata.ImageCount - 1
                if anchor_fr < final_frame:
                    template = self.track(
                        anchor_fr + 1, final_frame, template,
                        progress_offset=0, progress_span=50,
                    )
                else:
                    self.vm.update_progress.emit(50)
                if anchor_fr > 0:
                    template = self.track(
                        anchor_fr - 1,
                        0,
                        template,
                        progress_offset=50,
                        progress_span=50,
                    )
                else:
                    self.vm.update_progress.emit(100)
                if len(template['frames']):
                    first_image = int(self.vm.cine_handler.metadata.FirstImageNo)
                    template['start'] = first_image + int(np.min(template['frames']))
                    template['end'] = first_image + int(np.max(template['frames']))
                    template['smart_detected_start'] = template['start']
                    template['smart_detected_end'] = template['end']
                self.apply_hybrid_threshold(template)
                self.vm.update_status_text.emit(
                    f'{template["name"]} smart range: '
                    f'{template["start"]} to {template["end"]}'
                )
                return template

            enable_fr = template['frames_enable']
            start_fr = template['start'] - int(self.vm.cine_handler.metadata.FirstImageNo)
            last_fr = template['end'] - int(self.vm.cine_handler.metadata.FirstImageNo)

            # preprocess the parameters
            if enable_fr and (start_fr not in template['frames']):
                frames = template['frames']
                if start_fr < last_fr:
                    p_arr = frames[(frames >= start_fr) & (frames <= last_fr)]
                    if p_arr.size > 0:
                        midpoint = np.min(p_arr)
                        template = self.track(midpoint, start_fr, template)
                        start_fr = midpoint 
                if start_fr > last_fr:
                    p_arr = frames[(frames <= start_fr) & (frames >= last_fr)]
                    if p_arr.size > 0:
                        midpoint = np.max(p_arr)
                        template = self.track(midpoint, start_fr, template)
                        start_fr = midpoint                 
            elif not enable_fr:
                start_fr = self.vm.active_frame
                last_fr = min(self.vm.active_frame + 1, self.vm.cine_handler.metadata.ImageCount - 1)
                
            # run calcs to get points, scores etc
            template = self.track(start_fr, last_fr, template)
                
        else:
            # user clicks on graph to add a point manually
            template = self.add_point_to_template(template, point, frame_id, 'N/A', update_track_template=True)
        
        # all done with processing, clean it up
        return template

    @staticmethod
    def _fit_search_area_to_frame(center, size, frame_size):
        """Keep a Hybrid search crop inside the image without changing its origin math.

        Search rectangles are allowed to reach an image edge.  The crop is
        shifted inward (and only reduced when it is larger than the image), so
        the matcher still receives a full search area and can return correct
        full-frame coordinates.
        """
        frame_width = max(1, int(frame_size[0]))
        frame_height = max(1, int(frame_size[1]))
        width = min(frame_width, max(1, int(round(size[0]))))
        height = min(frame_height, max(1, int(round(size[1]))))

        left = int(float(center[0]) - math.floor(width / 2))
        top = int(float(center[1]) - math.floor(height / 2))
        left = int(np.clip(left, 0, frame_width - width))
        top = int(np.clip(top, 0, frame_height - height))
        right = left + width
        bottom = top + height
        adjusted_center = (
            left + math.floor(width / 2),
            top + math.floor(height / 2),
        )
        return (left, top), (right, bottom), adjusted_center, (width, height)

    @staticmethod
    def _oriented_region_within_frame(center, size, angle_deg, frame_size):
        """Return whether every corner of the inner Hybrid fixture is visible."""
        # A 31-pixel patch centered at x=15 occupies pixels 0..30 and is still
        # fully visible.  Use pixel-center extents rather than the geometric
        # half-width so that exact edge contact is accepted, but the first
        # genuinely out-of-frame subpixel pose stops the pass.
        half_width = max(0.0, (float(size[0]) - 1.0) / 2.0)
        half_height = max(0.0, (float(size[1]) - 1.0) / 2.0)
        center = np.asarray(center, dtype=float)
        corners = np.array([
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        ], dtype=float)
        transformed = np.array([
            center + _rotate_tracking_offset(corner, angle_deg)
            for corner in corners
        ])
        frame_width = float(frame_size[0])
        frame_height = float(frame_size[1])
        return bool(
            np.all(transformed[:, 0] >= 0.0)
            and np.all(transformed[:, 0] <= frame_width - 1.0)
            and np.all(transformed[:, 1] >= 0.0)
            and np.all(transformed[:, 1] <= frame_height - 1.0)
        )
    
    def track(self, start_fr, last_fr, template, progress_offset=0, progress_span=100):
        enable_sa = template['search_area_enable']
        sa_rng = template['search_area'] if enable_sa else (self.vm.cine_handler.metadata.ImWidth - 1, self.vm.cine_handler.metadata.ImHeight - 1)
        tpl_rng = template['tpl_rng']
        score_limit = template['acceptable_score']
        subpixel = SUBPIXEL_VALS[template['subpixel_size']]
        subpixel_type = template['subpixel_type']
        update_tpl = template['update_template_enable']
        tpl_score = template['tpl_score']
        tracking_method = template.get('tracking_method', HYBRID_TRACKING_METHOD)
        rotation_range = template.get('rotation_range', 180.0)
        rotation_step = template.get('rotation_step', 2.0)
        edge_weight = template.get('edge_weight', 0.6)
        edge_threshold = template.get('edge_threshold', None)
        position_precision = template.get('position_precision', 0.1)
        angle_precision = template.get('angle_precision', 0.1)
        adjacent_confidence_weight = float(np.clip(
            template.get('adjacent_confidence_weight', 0.65), 0.0, 1.0
        ))
        template_offset = np.asarray(
            template.get('template_offset', (0.0, 0.0)), dtype=float
        )
        if not template.get('rotation_allowed', True):
            rotation_range = 0.0
        smart_frames = bool(
            template.get('smart_frames', False)
            and tracking_method == HYBRID_TRACKING_METHOD
        )
        miss_limit = int(template.get('smart_miss_limit', 3)) if smart_frames else 5
        miss_limit = max(1, miss_limit)
        self.fail_cnt = 0
        if 'angles' not in template or len(template['angles']) != len(template['points']):
            template['angles'] = np.zeros(len(template['points']), dtype=float)
        if 't_angles' not in template or len(template['t_angles']) != len(template['t_points']):
            template['t_angles'] = np.zeros(len(template['t_points']), dtype=float)

        cfa = self.vm.cine_handler.metadata.CFA
        bpp = self.vm.cine_handler.metadata.RealBPP
        setup_patch = None
        anchor_refinement_patch = None
        anchor_refinement_radius = 0
        if tracking_method == HYBRID_TRACKING_METHOD:
            anchor_frame = int(template.get('anchor_frame', template['frames'][0]))
            anchor_matches = np.flatnonzero(template['frames'] == anchor_frame)
            if anchor_matches.size == 0:
                raise AutoTrackException('Hybrid setup frame is missing its tracked point.')
            anchor_index = int(anchor_matches[0])
            anchor_point = np.asarray(template['points'][anchor_index], dtype=float)
            anchor_angle = float(template['angles'][anchor_index])
            anchor_center = anchor_point + _rotate_tracking_offset(
                template_offset, anchor_angle
            )
            anchor_img = self.vm.cine_handler.get_img(anchor_frame)
            anchor_img = self.vm.image_tools.debayer(
                np.array(anchor_img, dtype=np.float32), cfa, bpp, force_mono=1
            )
            setup_patch = AutoTrackAlgorithms.extract_oriented_patch(
                anchor_img, anchor_center, tpl_rng, -anchor_angle
            )
            if template.get('anchor_refinement_enabled', True):
                anchor_size = int(np.clip(
                    round(min(tpl_rng) * 0.20), 15, 31
                ))
                if anchor_size % 2 == 0:
                    anchor_size += 1
                anchor_refinement_radius = int(np.clip(
                    round(anchor_size * 0.40), 5, 12
                ))
                anchor_refinement_patch = (
                    AutoTrackAlgorithms.extract_oriented_patch(
                        anchor_img,
                        anchor_point,
                        (anchor_size, anchor_size),
                        -anchor_angle,
                    )
                )
            template.setdefault('confidence_components', {})

        forward = start_fr <= last_fr
        fr_rng = range(start_fr, last_fr+1) if forward else range(start_fr, last_fr-1, -1)

        for i, fr in enumerate(fr_rng):
            try:
                self._curr_fr = fr
                if self.vm.abort_autotrack:
                    break
                progress = int(
                    float(progress_offset)
                    + ((i + 1) / len(fr_rng)) * float(progress_span)
                    + 0.5
                )
                progress = int(np.clip(progress, 0, 100))
                self.vm.update_progress.emit(progress)

                # get most recent template point and frame
                t_index = -1
                p_index = -1
                if forward:
                    t_arr = np.argwhere(template['t_frames']<fr)
                    if t_arr.size > 0: 
                        t_index = np.max(t_arr)
                        
                    p_arr = np.argwhere(template['frames']<fr)
                    if p_arr.size > 0: 
                        p_index = np.max(p_arr)   
                else:
                    t_arr = np.argwhere(template['t_frames']>fr)
                    if t_arr.size > 0:
                        t_index = np.min(t_arr)
                        
                    p_arr = np.argwhere(template['frames']>fr)
                    if p_arr.size > 0:
                        p_index = np.min(p_arr)

                previous_angle = float(template['angles'][p_index])
                sa_center = np.asarray(template['points'][p_index], dtype=float)
                if tracking_method == HYBRID_TRACKING_METHOD:
                    # Hybrid always uses the nearest successfully tracked frame
                    # in the processing direction as its local reference.
                    t_frame = int(template['frames'][p_index])
                    tracked_template_point = np.asarray(
                        template['points'][p_index], dtype=float
                    )
                    reference_angle = previous_angle
                    tpl_center = tracked_template_point + _rotate_tracking_offset(
                        template_offset, reference_angle
                    )
                    sa_center = sa_center + _rotate_tracking_offset(
                        template_offset, previous_angle
                    )
                else:
                    t_frame = template['t_frames'][t_index]
                    tracked_template_point = np.asarray(
                        template['t_points'][t_index], dtype=float
                    )
                    reference_angle = float(template['t_angles'][t_index])
                    tpl_center = tracked_template_point
                if not enable_sa:
                    sa_center = (math.floor(sa_rng[0]/2), math.floor(sa_rng[1]/2))

                # Hybrid search windows may touch an image edge. Shift the
                # requested window inward while preserving its size and pass
                # the adjusted center/range to the matcher so its global
                # coordinate conversion remains exact. Classic retains the
                # original PCA range validation below.
                frame_size = (
                    self.vm.cine_handler.metadata.ImWidth,
                    self.vm.cine_handler.metadata.ImHeight,
                )
                if tracking_method == HYBRID_TRACKING_METHOD:
                    tl_sa, br_sa, sa_center, frame_sa_rng = self._fit_search_area_to_frame(
                        sa_center, sa_rng, frame_size
                    )
                else:
                    tl_sa = (
                        int(sa_center[0] - math.floor(sa_rng[0] / 2)),
                        int(sa_center[1] - math.floor(sa_rng[1] / 2)),
                    )
                    br_sa = int(tl_sa[0] + sa_rng[0]), int(tl_sa[1] + sa_rng[1])
                    frame_sa_rng = sa_rng
                tl_tpl = int(tpl_center[0] - math.floor(tpl_rng[0]/2)), int(tpl_center[1] - math.floor(tpl_rng[1]/2))
                br_tpl = int(tl_tpl[0] + tpl_rng[0]), int(tl_tpl[1] + tpl_rng[1])

                if tracking_method != HYBRID_TRACKING_METHOD:
                    not_in_range = [any(x < 0 for x in tl_sa),
                                    br_sa[0] >= self.vm.cine_handler.metadata.ImWidth,
                                    br_sa[1] >= self.vm.cine_handler.metadata.ImHeight,
                                    any(x < 0 for x in tl_tpl),
                                    br_tpl[0] >= self.vm.cine_handler.metadata.ImWidth,
                                    br_tpl[1] >= self.vm.cine_handler.metadata.ImHeight ]

                    if any(not_in_range):
                        raise SearchAreaOutOfRange('The search area is out of the range of the frame.')

                # crop sa and tpl
                # if color, debayer the image into a 1 channel mono for tracking. no op for Mono cfa
                current_img = self.vm.cine_handler.get_img(fr)
                current_img = self.vm.image_tools.debayer(
                    np.array(current_img, dtype=np.float32), cfa, bpp, force_mono=1
                )
                sa_img = current_img[tl_sa[1]:br_sa[1], tl_sa[0]:br_sa[0]]

                # if color, debayer the image into a 1 channel mono for tracking. no op for Mono cfa
                tpl_img = self.vm.cine_handler.get_img(t_frame)
                tpl_img = self.vm.image_tools.debayer(np.array(tpl_img, dtype=np.float32), cfa, bpp, force_mono=1)
                if tracking_method == HYBRID_TRACKING_METHOD:
                    adjacent_patch = AutoTrackAlgorithms.extract_oriented_patch(
                        tpl_img,
                        tpl_center,
                        tpl_rng,
                        -reference_angle,
                    )
                else:
                    # Preserve the original PCA Classic template crop exactly.
                    tpl_img = tpl_img[tl_tpl[1]:br_tpl[1], tl_tpl[0]:br_tpl[0]]

                # run cross correlation to get expected location
                if tracking_method == HYBRID_TRACKING_METHOD:
                    # Solve pose from the immutable setup-frame model. The
                    # preceding accepted frame supplies only the search center,
                    # angle continuity, and adjacent confidence diagnostic; it
                    # never replaces the reference geometry.
                    data = AutoTrackAlgorithms.hybrid_pattern_matcher(
                                            tpl_img=setup_patch,
                                            sa_img=sa_img,
                                            sa_center=tuple(sa_center),
                                            sa_rng=frame_sa_rng,
                                            reference_angle=reference_angle,
                                            rotation_range=rotation_range,
                                            rotation_step=rotation_step,
                                            edge_weight=edge_weight,
                                            edge_threshold=edge_threshold,
                                            position_precision=position_precision,
                                            angle_precision=angle_precision)
                    candidate_center = (float(data.x_pos), float(data.y_pos))
                    if not self._oriented_region_within_frame(
                        candidate_center,
                        tpl_rng,
                        data.angle_deg,
                        frame_size,
                    ):
                        pass_direction = 'forward' if forward else 'backward'
                        cine_frame = int(fr) + int(
                            self.vm.cine_handler.metadata.FirstImageNo
                        )
                        template.setdefault('boundary_reasons', {})[
                            pass_direction
                        ] = {
                            'reason': 'fixture_out_of_frame',
                            'frame': int(fr),
                            'cine_frame': cine_frame,
                        }
                        self.vm.update_status_text.emit(
                            f'{template["name"]}: tracking stopped at frame '
                            f'{cine_frame} because the inner Hybrid fixture '
                            'left the image.'
                        )
                        break
                    candidate_patch = AutoTrackAlgorithms.extract_oriented_patch(
                        current_img,
                        candidate_center,
                        tpl_rng,
                        -data.angle_deg,
                    )
                    adjacent_data = AutoTrackAlgorithms.hybrid_pattern_matcher(
                        tpl_img=adjacent_patch,
                        sa_img=candidate_patch,
                        sa_center=((tpl_rng[0] - 1) / 2.0, (tpl_rng[1] - 1) / 2.0),
                        sa_rng=tpl_rng,
                        reference_angle=0.0,
                        rotation_range=0.0,
                        rotation_step=rotation_step,
                        edge_weight=edge_weight,
                        edge_threshold=edge_threshold,
                    )
                    adjacent_score = float(adjacent_data.confid_val_ij)
                    setup_data = AutoTrackAlgorithms.hybrid_pattern_matcher(
                        tpl_img=setup_patch,
                        sa_img=candidate_patch,
                        sa_center=((tpl_rng[0] - 1) / 2.0, (tpl_rng[1] - 1) / 2.0),
                        sa_rng=tpl_rng,
                        reference_angle=0.0,
                        rotation_range=0.0,
                        rotation_step=rotation_step,
                        edge_weight=edge_weight,
                        edge_threshold=edge_threshold,
                    )
                    setup_score = float(setup_data.confid_val_ij)
                    setup_confidence_weight = 1.0 - adjacent_confidence_weight
                    combined_score = (
                        (max(0.0, adjacent_score) ** adjacent_confidence_weight)
                        * (max(0.0, setup_score) ** setup_confidence_weight)
                    )
                    data.adjacent_score = adjacent_score
                    data.setup_score = setup_score
                    data.confid_val_ij = combined_score
                    template['confidence_components'][int(fr)] = {
                        'adjacent': round(adjacent_score, 6),
                        'setup': round(setup_score, 6),
                        'combined': round(combined_score, 6),
                        'reference_frame': int(t_frame),
                        'setup_frame': int(anchor_frame),
                    }
                else:
                    data = AutoTrackAlgorithms.template_matcher(
                                            tpl_img=tpl_img, 
                                            sa_img=sa_img, 
                                            sa_center=tuple(sa_center),
                                            sa_rng=sa_rng,
                                            sub_pixel=subpixel,
                                            sub_pixel_type=subpixel_type)
                    data.angle_deg = reference_angle

                score = round(float(data.confid_val_ij), 3)
                point = np.array((data.x_pos, data.y_pos), dtype=float)
                if tracking_method == HYBRID_TRACKING_METHOD:
                    # The matcher follows the reinforcement geometry. Convert
                    # its pose back to the exact point selected by the user.
                    point -= _rotate_tracking_offset(template_offset, data.angle_deg)
                    refinement = None
                    if anchor_refinement_patch is not None:
                        refinement = AutoTrackAlgorithms.refine_anchor_point(
                            anchor_refinement_patch,
                            current_img,
                            point,
                            data.angle_deg,
                            search_radius=anchor_refinement_radius,
                            position_precision=position_precision,
                        )
                    if refinement is not None:
                        point = np.array(
                            (refinement.x_pos, refinement.y_pos), dtype=float
                        )
                    component = template['confidence_components'].get(int(fr))
                    if component is not None:
                        component['point_lock_applied'] = refinement is not None
                        component['point_lock_score'] = (
                            round(float(refinement.confid_val_ij), 6)
                            if refinement is not None
                            else None
                        )
                point = tuple(point)

                if tracking_method == HYBRID_TRACKING_METHOD:
                    self.record_hybrid_candidate(
                        template, point, fr, score, data.angle_deg
                    )

                # add pt to list if score is above limit
                if score >= score_limit:
                    update_temp =  (update_tpl and score < tpl_score)
                    template = self.add_point_to_template(
                        template,
                        point,
                        fr,
                        score,
                        update_track_template=update_temp,
                        angle=data.angle_deg,
                    )
                    self.fail_cnt = 0
                else:
                    self.fail_cnt += 1
                    if self.fail_cnt >= miss_limit:
                        if smart_frames:
                            direction = 'start' if not forward else 'end'
                            self.vm.update_status_text.emit(
                                f'Smart {direction} boundary found for {template["name"]}.'
                            )
                            break
                        raise ObjectLostException('The object has been lost. Please select a new point and start processing again.')
                if tracking_method == HYBRID_TRACKING_METHOD:
                    self.vm.update_status_text.emit(
                        f'{template["name"]}: confidence {score:.3f} '
                        f'(neighbor {data.adjacent_score:.3f}, setup {data.setup_score:.3f}); '
                        f'angle {data.angle_deg:.2f}°'
                    )
                else:
                    self.vm.update_status_text.emit(
                        f'{template["name"]} track complete. Angle: {data.angle_deg:.2f}°'
                    )

            except (SearchAreaOutOfRange, AutoTrackException) as e:
                if smart_frames:
                    # A single unreadable/degenerate frame is a miss, not a
                    # reason to abort the complete video. Consecutive misses
                    # still establish the user-configured start/end boundary.
                    logging.warning('Hybrid frame %s could not be matched: %s', fr, e)
                    self.fail_cnt += 1
                    template.setdefault('confidence_components', {})[int(fr)] = {
                        'adjacent': 0.0,
                        'setup': 0.0,
                        'combined': 0.0,
                        'reference_frame': int(t_frame),
                        'setup_frame': int(anchor_frame),
                        'error': str(e),
                    }
                    if self.fail_cnt >= miss_limit:
                        direction = 'start' if not forward else 'end'
                        self.vm.update_status_text.emit(
                            f'Smart {direction} boundary found for {template["name"]} '
                            f'after {self.fail_cnt} consecutive misses.'
                        )
                        break
                    self.vm.update_status_text.emit(
                        f'{template["name"]}: frame could not be matched '
                        f'({self.fail_cnt}/{miss_limit} consecutive misses).'
                    )
                    continue
                logging.exception(e)
                self.vm.update_status_text.emit(f'{e}')
                break
            except Exception as e:
                logging.exception(e)
                self.vm.update_status_text.emit(f'{e}')
                break
        
        if not forward:
            self.fail_cnt = -self.fail_cnt
        
        return template
        
    def cleanup(self, track_all=False):
        last_fr = self.vm.active_frame - self.fail_cnt
        if self._curr_fr:
            self.vm.active_frame = (self._curr_fr - self.fail_cnt)
        self.vm.track_complete.emit(True, track_all)
        self._curr_fr = None
        return super().cleanup()

class TwoPointTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self._point_target = 2
        
    def evaluate(self):
        super().evaluate()
        try:
            deltaX_phys, deltaY_phys, displacement_phys = self.vm.meas.meas_displacement(self.phys_pts) 
            speedx_phys, speedy_phys, speed_abs_phys = self.vm.meas.meas_speed(self.phys_pts, self.vm.cine_handler.metadata.DecimatedFrameRate, 
                                                                               self.points_frame_ids)

            disp_output = f"{round(displacement_phys,2)} {self.vm.length_units}"
            speed_output = f"{round(speed_abs_phys,2)} {self.vm.length_units}/s"
            delta_frame = self.points_frame_ids[1] - self.points_frame_ids[0]
            cal_txt = self.vm.image_scale_text.replace('\n','')
            self.vm.update_status_text.emit(f'Distance: {round(displacement_phys, 2)} {self.vm.length_units}\nSpeed: {round(speed_abs_phys, 2)} {self.vm.length_units}/s\nCal: {cal_txt}\nΔframe: {delta_frame} frames')
            
            # add the new measurements to the dictionary
            d = None
            i = self.vm.meas_data['_curr_i']
            try:
                d = self.vm.meas_data['Two Point Tool']
            except:
                d = OrderedDict({'ID': [], 'X0': [], 'Y0': [], 'X1':[], 'Y1':[], 'Scale':[], 'Units':[], 'Displacement':[], 'Speed':[], 'Delta Frame':[]})
            d['ID'].append(i)
            d['X0'].append(self.phys_pts[0][0])
            d['Y0'].append(self.phys_pts[0][1])
            d['X1'].append(self.phys_pts[1][0])
            d['Y1'].append(self.phys_pts[1][1])
            d['Scale'].append(self.vm.image_scale)
            d['Units'].append(f'{self.vm.length_units}/px')
            d['Displacement'].append(displacement_phys)
            d['Speed'].append(speed_abs_phys)
            d['Delta Frame'].append(delta_frame)
            self.vm.meas_data['Two Point Tool'] = d
            self.vm.meas_data['_curr_i'] = i + 1
            
            self.cleanup()
            
        except Exception as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'{e}')
            
class ThreePointTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self._point_target = 3
        
    def evaluate(self):
        super().evaluate()
        try: 
            angle = round(self.vm.meas.meas_angle(self.phys_pts), 2)
            angle_rate = round(self.vm.meas.meas_angular_rate(self.phys_pts, self.vm.cine_handler.metadata.DecimatedFrameRate, 
                                                              self.points_frame_ids) , 2)
            area = round(self.vm.meas.meas_area_between_three_points(self.phys_pts), 2)
            delta_frame = f"{self.points_frame_ids[2] - self.points_frame_ids[0]}"
            self.vm.update_status_text.emit(f'Angle: {angle} deg\nAngular rate: {angle_rate} deg/s\nΔframe: {delta_frame} frames')

            # add the new measurements to the dictionary
            d = None
            i = self.vm.meas_data['_curr_i']
            try:
                d = self.vm.meas_data['Three Point Tool']
            except:
                d = OrderedDict({'ID': [], 'X0': [], 'Y0': [], 'X1':[], 'Y1':[], 'X2':[], 'Y2':[], 
                                 'Scale':[], 'Units':[], 'Angle (deg)':[], 'Angular Speed (deg/s)':[], 'Delta Frame':[], 'Area':[]})
            d['ID'].append(i)
            d['X0'].append(self.phys_pts[0][0])
            d['Y0'].append(self.phys_pts[0][1])
            d['X1'].append(self.phys_pts[1][0])
            d['Y1'].append(self.phys_pts[1][1])
            d['X2'].append(self.phys_pts[2][0])
            d['Y2'].append(self.phys_pts[2][1])
            d['Scale'].append(self.vm.image_scale)
            d['Units'].append(f'{self.vm.length_units}/px')
            d['Angle (deg)'].append(angle)
            d['Angular Speed (deg/s)'].append(angle_rate)
            d['Delta Frame'].append(delta_frame)
            d['Area'].append(area)
            self.vm.meas_data['Three Point Tool'] = d
            self.vm.meas_data['_curr_i'] = i + 1

            self.cleanup()
            
        except Exception as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'{e}')
            
class TwoLineTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self._point_target = 4
        self._connect_pairs = True
    
    def evaluate(self):
        super().evaluate()
        try:
            angle = round(self.vm.meas.meas_angle_blines(self.phys_pts), 2)
            angle_rate_lines = round(self.vm.meas.meas_angular_rate_between_lines(self.phys_pts, self.vm.cine_handler.metadata.DecimatedFrameRate, 
                                                                                  self.points_frame_ids), 2)
            delta_frame = f"{self.points_frame_ids[3] - self.points_frame_ids[0]}"
            self.vm.update_status_text.emit(f'Angle: {angle} deg\nAngular rate: {angle_rate_lines} deg/s\nΔframe: {delta_frame} frames')

            # add the new measurements to the dictionary
            d = None
            i = self.vm.meas_data['_curr_i']
            try:
                d = self.vm.meas_data['Two Line Tool']
            except:
                d = OrderedDict({'ID': [], 'X0': [], 'Y0': [], 'X1':[], 'Y1':[], 'X2':[], 'Y2':[], 'X3':[], 'Y3':[], 
                                 'Scale':[], 'Units':[], 'Angle (deg)':[], 'Angular Speed (deg/s)':[], 'Delta Frame':[]})
            d['ID'].append(i)
            d['X0'].append(self.phys_pts[0][0])
            d['Y0'].append(self.phys_pts[0][1])
            d['X1'].append(self.phys_pts[1][0])
            d['Y1'].append(self.phys_pts[1][1])
            d['X2'].append(self.phys_pts[2][0])
            d['Y2'].append(self.phys_pts[2][1])            
            d['X3'].append(self.phys_pts[2][0])
            d['Y3'].append(self.phys_pts[2][1])
            d['Scale'].append(self.vm.image_scale)
            d['Units'].append(f'{self.vm.length_units}/px')
            d['Angle (deg)'].append(angle)
            d['Angular Speed (deg/s)'].append(angle_rate_lines)
            d['Delta Frame'].append(delta_frame)
            self.vm.meas_data['Two Line Tool'] = d
            self.vm.meas_data['_curr_i'] = i + 1

            self.cleanup()
        except Exception as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'{e}')
            
class AreaTool(BaseTool):
    def __init__(self, vm):
        super().__init__(vm)
        self._point_target = 2
        self._connect_at_end = True
        self._connect_box_from_two_points = True
        
    def evaluate(self):
        super().evaluate()
        try:
            area_pixel = round(self.vm.meas.meas_area_with_two_points(self.points), 2)
            area_phys = self.vm.meas.meas_area_with_two_points(self.phys_pts)
            avg_pixel_val = round(self.vm.meas.avg_pixel_value(self.points, self.vm.cine_handler.get_img(self.vm.active_frame)), 2)
            self.vm.update_status_text.emit(f'Area: {area_pixel} sq-pix\nCal. area: {round(area_phys, 2)} {self.vm._area_units}\nAvg. pixel val.: {avg_pixel_val} counts')
            
            # add the new measurements to the dictionary
            d = None
            i = self.vm.meas_data['_curr_i']
            try:
                d = self.vm.meas_data['Area Tool']
            except:
                d = OrderedDict({'ID': [], 'X0': [], 'Y0': [], 'X1':[], 'Y1':[], 'Scale':[], 'Units':[], 'Area (sq-px)':[], 'Area (unit)':[], 'Mean':[]})
            d['ID'].append(i)
            d['X0'].append(self.phys_pts[0][0])
            d['Y0'].append(self.phys_pts[0][1])
            d['X1'].append(self.phys_pts[1][0])
            d['Y1'].append(self.phys_pts[1][1])
            d['Scale'].append(self.vm.image_scale)
            d['Units'].append(f'{self.vm.length_units}/px')
            d['Area (sq-px)'].append(area_pixel)
            d['Area (unit)'].append(area_phys)
            d['Mean'].append(avg_pixel_val)
            self.vm.meas_data['Area Tool'] = d
            self.vm.meas_data['_curr_i'] = i + 1
            
            self.cleanup()
        except Exception as e:
            logging.exception(e)
            self.vm.update_status_text.emit(f'{e}')

# EXCEPTIONS
class ObjectLostException(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

class SearchAreaOutOfRange(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)
