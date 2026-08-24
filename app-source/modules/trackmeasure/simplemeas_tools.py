from abc import ABC, abstractmethod
from calibration import CalSpace
from PySide6.QtGui import *
import numpy as np
from autotrackalgorithms import AutoTrackAlgorithms
from collections import OrderedDict
import logging
import math

SUBPIXEL_VALS = {'1.0 pix': 1, '1/2 pix': 2, '1/3 pix': 3, '1/4 pix': 4, '1/5 pix': 5, '1/6 pix': 6, '1/7 pix': 7, '1/8 pix': 8, '1/9 pix': 9, '1/10 pix': 10}

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
            score = 'N/A' 
            new_t = {'id': new_id, 'name': f'Object {new_id}', 'points': np.array([point]), 'frames': np.array([frame_id]), 
                     'scores': np.array([score]), 't_points': np.array([point]), 't_frames': np.array([frame_id]), 'notes': {}, 
                     'origin_frame': None, 'relative_to': None, 'enabled': True, 'color': self.colors_hex_codes[new_id % len(self.colors_hex_codes)], 
                     'start': int(self.vm.cine_handler.metadata.FirstImageNo), 
                     'end': int(self.vm.cine_handler.metadata.FirstImageNo + self.vm.cine_handler.metadata.ImageCount - 1), 
                     'search_area':(101,101), 'tpl_rng': (31,31), 'subpixel_size': '1.0 pix', 'subpixel_type': 'cubic',
                     'frames_enable': True, 'search_area_enable': True, 'tpl_rng_enable':True, 
                     'update_template_enable': True, 'acceptable_score': 0.8, 'tpl_score': 0.9}
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

    def add_point_to_template(self, t, point, frame_id, score='N/A', update_track_template=True):
        """
        will always add a new point to the template dictionaries lists: points, frameID and score.
        if update_track_template: it will additionally add to the 't_<keyname>' for the track template lists
        """

        point_key = 'points'
        frame_key = 'frames'

        for i in range(2):
            if i == 1:
                point_key = f't_{point_key}'
                frame_key = f't_{frame_key}'
                if not update_track_template: break 
        
            m_arr = np.argwhere(t[frame_key]==frame_id)
            if m_arr.size > 0: 
                m = m_arr[0,0]
                # replace if match at frame id
                t[point_key][m] = np.array(point)
                t[frame_key][m] = frame_id
                if i == 0:
                    t['scores'][m] = score
            else:
                # append new point
                t[point_key] = np.append(t[point_key], np.array([point]), axis=0)
                t[frame_key] = np.append(t[frame_key], frame_id)
                if i == 0:
                    t['scores'] = np.append(t['scores'], score)
            # sort by frame id
            ind = np.argsort(t[frame_key])
            t[point_key] = t[point_key][ind]
            t[frame_key] = t[frame_key][ind]
            if i == 0:
                t['scores'] = t['scores'][ind]

        return t

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
            # parameterize dictionary items
            self.vm.update_status_text.emit(f'Start autotrack of object {template["name"]}')
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
    
    def track(self, start_fr, last_fr, template):
        enable_sa = template['search_area_enable']
        sa_rng = template['search_area'] if enable_sa else (self.vm.cine_handler.metadata.ImWidth - 1, self.vm.cine_handler.metadata.ImHeight - 1)
        tpl_rng = template['tpl_rng']
        score_limit = template['acceptable_score']
        subpixel = SUBPIXEL_VALS[template['subpixel_size']]
        subpixel_type = template['subpixel_type']
        update_tpl = template['update_template_enable']
        tpl_score = template['tpl_score']

        forward = start_fr <= last_fr
        fr_rng = range(start_fr, last_fr+1) if forward else range(start_fr, last_fr-1, -1)

        for i, fr in enumerate(fr_rng):
            try:
                self._curr_fr = fr
                if self.vm.abort_autotrack:
                    break
                progress = int((i+1)/len(fr_rng)*100 + 0.5)
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

                t_frame = template['t_frames'][t_index]
                tpl_center = template['t_points'][t_index]
                sa_center = template['points'][p_index]
                if not enable_sa:
                    sa_center = (math.floor(sa_rng[0]/2), math.floor(sa_rng[1]/2))

                # check if sa and tpl are in range of cine (w, h). if not, break.
                tl_sa = int(sa_center[0] - math.floor(sa_rng[0]/2)), int(sa_center[1] - math.floor(sa_rng[1]/2))
                br_sa = int(tl_sa[0] + sa_rng[0]), int(tl_sa[1] + sa_rng[1])
                tl_tpl = int(tpl_center[0] - math.floor(tpl_rng[0]/2)), int(tpl_center[1] - math.floor(tpl_rng[1]/2))
                br_tpl = int(tl_tpl[0] + tpl_rng[0]), int(tl_tpl[1] + tpl_rng[1])

                not_in_range = [any(x < 0 for x in tl_sa),
                                br_sa[0] >= self.vm.cine_handler.metadata.ImWidth,
                                br_sa[1] >= self.vm.cine_handler.metadata.ImHeight,
                                any(x < 0 for x in tl_tpl),
                                br_tpl[0] >= self.vm.cine_handler.metadata.ImWidth,
                                br_tpl[1] >= self.vm.cine_handler.metadata.ImHeight ]
                
                if any(not_in_range):
                    raise SearchAreaOutOfRange('The search area is out of the range of the frame.')

                # crop sa and tpl
                cfa = self.vm.cine_handler.metadata.CFA
                bpp = self.vm.cine_handler.metadata.RealBPP

                # if color, debayer the image into a 1 channel mono for tracking. no op for Mono cfa
                sa_img = self.vm.cine_handler.get_img(fr)
                sa_img = self.vm.image_tools.debayer(np.array(sa_img, dtype=np.float32), cfa, bpp, force_mono=1)
                sa_img = sa_img[tl_sa[1]:br_sa[1], tl_sa[0]:br_sa[0]]

                # if color, debayer the image into a 1 channel mono for tracking. no op for Mono cfa
                tpl_img = self.vm.cine_handler.get_img(t_frame)
                tpl_img = self.vm.image_tools.debayer(np.array(tpl_img, dtype=np.float32), cfa, bpp, force_mono=1)
                tpl_img = tpl_img[tl_tpl[1]:br_tpl[1], tl_tpl[0]:br_tpl[0]]

                # run cross correlation to get expected location
                data = AutoTrackAlgorithms.template_matcher(
                                        tpl_img=tpl_img, 
                                        sa_img=sa_img, 
                                        sa_center=tuple(sa_center),
                                        sa_rng=sa_rng,
                                        sub_pixel=subpixel,
                                        sub_pixel_type=subpixel_type)

                score = round(float(data.confid_val_ij), 3)
                point = (data.x_pos, data.y_pos)

                # add pt to list if score is above limit
                if score >= score_limit:
                    update_temp =  (update_tpl and score < tpl_score)
                    template = self.add_point_to_template(template, point, fr, score, update_track_template=update_temp)
                    self.fail_cnt = 0
                else:
                    self.fail_cnt += 1
                    if self.fail_cnt == 5:
                        raise ObjectLostException('The object has been lost. Please select a new point and start processing again.')
                self.vm.update_status_text.emit(f'{template["name"]} track complete.')

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
