from PySide6.QtCore import Signal, QObject, QTimer, QMetaObject, Qt, Slot, QEventLoop, QTimer, QMetaObject, Qt, Slot, QEventLoop
import numpy as np
import logging, os

import measmodel
import reportmodel
from pyphantom_cine.cine_handler import CineHandler
from pyphantom_config.config import JsonConfig
if os.name == 'nt':
    from pyphantom_ipc.ipc_api import IPCController
else:
    class IPCController:
        def __init__(self): pass

        def register_message_handler(self, message_type, handler):
            def dummy_respond(message_type, data):
                return bytes([0, 0, 0, 0])
            
            if handler:
                handler(bytes([0, 0, 0, 0]), dummy_respond)

        def start(self): 
            return bytes([0, 0, 0, 0])


from calibration import *
from pyphantom_image.imagetools import ImageTools, ImageProcessingConfig
from simplemeas_tools import *


# constants
class SimpleMeasVM(QObject):
    meas: measmodel.MeasModel
    report_handler: reportmodel.ReportHandler
    config: JsonConfig
    imtools: ImageTools
    cal: Calibration  
    ipc: IPCController
    active_tool: BaseTool
    cine_handler: CineHandler

    #region SIGNALS
    new_track_data = Signal(object, int)
    new_cine_load = Signal(str, object)
    draw_frame = Signal(str, object, int, str, object)
    draw_points = Signal(str, object, str)
    draw_text_label = Signal(str, object, str)
    draw_lines = Signal(str, object, int, bool, bool, bool, str)
    get_new_scale = Signal(object, object)
    track_fig_changed = Signal(object)
    draw_roi = Signal(object, object, str)
    process_tool_complete = Signal()
    update_status_text = Signal(str)
    update_progress = Signal(int)
    format_progress = Signal(str)
    track_complete = Signal(bool, bool)
    clear_scene = Signal()
    active_frame_changed = Signal(int)
    brightness_changed = Signal(object)
    gamma_changed = Signal(object)
    contrast_changed = Signal(object)
    ftone_changed = Signal(bool)
    mouse_pos_changed = Signal(str)
    pixel_val_changed = Signal(str)
    last_frame_changed = Signal(str)
    first_frame_changed = Signal(str)
    image_scale_changed = Signal(str)
    trigger_time_changed = Signal(str)
    time_from_trigger_changed = Signal(str)
    request_choose_cine = Signal(str)
    workspace_changed = Signal(object, int)
    #endregion
    
    #region ACCESSORS
    @property
    def length_units(self):
        if not getattr(self, 'workspace_contexts', []):
            val = self.config.get('length_units')
            if val is not None: self._length_units = val
        return self._length_units
    @length_units.setter
    def length_units(self, new_length_units):
        self.config.set('length_units', new_length_units)
        self._length_units = new_length_units
        self._area_units = f'sq-{new_length_units}'
        if 0 <= getattr(self, 'active_cine_index', -1) < len(getattr(self, 'workspace_contexts', [])):
            self.workspace_contexts[self.active_cine_index]['_length_units'] = new_length_units
            self.workspace_contexts[self.active_cine_index]['_area_units'] = self._area_units
    @property
    def known_length(self):
        if not getattr(self, 'workspace_contexts', []):
            val = self.config.get('known_length')
            if val is not None: self._known_length = val
        return self._known_length
    @known_length.setter
    def known_length(self, new_known_length):
        self.config.set('known_length', new_known_length)
        self._known_length = new_known_length
        if 0 <= getattr(self, 'active_cine_index', -1) < len(getattr(self, 'workspace_contexts', [])):
            self.workspace_contexts[self.active_cine_index]['_known_length'] = new_known_length
    @property
    def image_scale(self):
        if not getattr(self, 'workspace_contexts', []):
            val = self.config.get('image_scale')
            if val is not None: self._image_scale = val
        return self._image_scale
    @image_scale.setter
    def image_scale(self, new_image_scale):
        try:
            self.config.set('image_scale', new_image_scale)
            self.cal.update_cal(1, new_image_scale)
            self._image_scale = new_image_scale
            if 0 <= getattr(self, 'active_cine_index', -1) < len(getattr(self, 'workspace_contexts', [])):
                self.workspace_contexts[self.active_cine_index]['_image_scale'] = new_image_scale
        except ValueError as e:
            self.update_status_text.emit(f'Error setting image scale: {e}')
            logging.exception(e)
        except TypeError as e:
            self.update_status_text.emit(f'Error setting image scale: {e}')
            logging.exception(e)
    @property
    def time_units(self):
        if not getattr(self, 'workspace_contexts', []):
            val = self.config.get('time_units')
            if val is not None: self._time_units = val
        return self._time_units
    @time_units.setter
    def time_units(self, new_time_units):
        self.config.set('time_units', new_time_units)
        self._time_units = new_time_units
        if 0 <= getattr(self, 'active_cine_index', -1) < len(getattr(self, 'workspace_contexts', [])):
            self.workspace_contexts[self.active_cine_index]['_time_units'] = new_time_units
    @property
    def time_scale(self):
        if not getattr(self, 'workspace_contexts', []):
            val = self.config.get('time_scale')
            if val is not None: self._time_scale = val
        return self._time_scale
    @time_scale.setter
    def time_scale(self, new_time_scale):
        self.config.set('time_scale', new_time_scale)
        self.cal.update_time(new_time_scale)
        self._time_scale = new_time_scale
        if 0 <= getattr(self, 'active_cine_index', -1) < len(getattr(self, 'workspace_contexts', [])):
            self.workspace_contexts[self.active_cine_index]['_time_scale'] = new_time_scale
    @property
    def autogen_report_name(self):
        val = self.config.get('autogen_report_name')
        if val is not None: self._autogen_report_name = val
        return self._autogen_report_name
    @autogen_report_name.setter
    def autogen_report_name(self, new_autogen_report_name):
        self.config.set('autogen_report_name', new_autogen_report_name)
        self._autogen_report_name = new_autogen_report_name
    @property
    def active_frame(self):
        return self._active_frame
    @active_frame.setter
    def active_frame(self, new_active_frame):
        if hasattr(self, 'cine_handler') and self.cine_handler.cine_loaded:
            max_frame = self.cine_handler.metadata.ImageCount - 1
            new_active_frame = max(0, min(new_active_frame, max_frame))
        self._active_frame = new_active_frame
        if hasattr(self, 'workspace_contexts') and 0 <= getattr(self, 'active_cine_index', -1) < len(self.workspace_contexts):
            self.workspace_contexts[self.active_cine_index]['_active_frame'] = new_active_frame
        self.active_frame_changed.emit(new_active_frame)
    @property
    def mouse_pos(self):
        return self._mouse_pos
    @mouse_pos.setter
    def mouse_pos(self, new_mouse_pos):
        self._mouse_pos = new_mouse_pos
        self.mouse_pos_changed.emit(new_mouse_pos)
    @property
    def last_frame(self):
        return self._last_frame
    @last_frame.setter
    def last_frame(self, new_last_frame):
        self._last_frame = new_last_frame
        self.last_frame_changed.emit(str(new_last_frame))
    @property
    def first_frame(self):
        return self._first_frame
    @first_frame.setter
    def first_frame(self, new_first_frame):
        self._first_frame = new_first_frame
        self.first_frame_changed.emit(str(new_first_frame))
    @property
    def image_scale_text(self):
        return self._image_scale_text
    @image_scale_text.setter
    def image_scale_text(self, new_image_scale_text):
        self._image_scale_text = new_image_scale_text
        self.image_scale_changed.emit(new_image_scale_text)
    @property
    def pixel_val(self):
        return self._pixel_val
    @pixel_val.setter
    def pixel_val(self, new_pixel_val):
        self._pixel_val = new_pixel_val
        self.pixel_val_changed.emit(str(new_pixel_val))
    @property
    def trigger_time(self):
        return self._trigger_time
    @trigger_time.setter
    def trigger_time(self, new_trigger_time):
        self._trigger_time = new_trigger_time
        self.trigger_time_changed.emit(str(new_trigger_time))
    @property
    def time_from_trigger(self):
        return self._time_from_trigger
    @time_from_trigger.setter
    def time_from_trigger(self, new_time_from_trigger):
        self._time_from_trigger = new_time_from_trigger
        self.time_from_trigger_changed.emit(str(new_time_from_trigger))
    @property
    def image_brightness(self):
        return self._image_brightness
    @image_brightness.setter
    def image_brightness(self, new_image_brightness):
        self._image_brightness = new_image_brightness
        if self._cache_enabled:
            self._processed_cache.clear()
            self._processed_cache_size_bytes = 0
        self.brightness_changed.emit(new_image_brightness)
    @property
    def image_gamma(self):
        return self._image_gamma
    @image_gamma.setter
    def image_gamma(self, new_image_gamma):
        self._image_gamma = new_image_gamma
        if self._cache_enabled:
            self._processed_cache.clear()
            self._processed_cache_size_bytes = 0
        self.gamma_changed.emit(new_image_gamma)
    @property
    def image_contrast(self):
        return self._image_contrast
    @image_contrast.setter
    def image_contrast(self, new_image_contrast):
        self._image_contrast = new_image_contrast
        if self._cache_enabled:
            self._processed_cache.clear()
            self._processed_cache_size_bytes = 0
        self.contrast_changed.emit(new_image_contrast)
    @property
    def toggle_ftone(self):
        return self._toggle_ftone
    @toggle_ftone.setter
    def toggle_ftone(self, new_toggle_ftone):
        self._toggle_ftone = new_toggle_ftone
        if self._cache_enabled:
            self._processed_cache.clear()
            self._processed_cache_size_bytes = 0
        self.ftone_changed.emit(new_toggle_ftone)
    @property
    def raw_enabled(self):
        return self._raw_enabled
    @raw_enabled.setter
    def raw_enabled(self, new_raw_enabled):
        self._raw_enabled = new_raw_enabled
        if self._cache_enabled:
            self._processed_cache.clear()
            self._processed_cache_size_bytes = 0
    
    #endregion
    
    def __init__(self, view, meas_model, report_handler, config_model, cine_handler, args):
        super().__init__(view)
        logging.info('Initializing SimpleMeasVM')
        # public properties
        self.meas = meas_model
        self.report_handler = report_handler
        self.cine_handler = cine_handler
        self.config = config_model
        self.image_tools = None
        self.cal = Calibration()

        self.cine_path = ''
        self.active_tool = None
        self.active_object = None
        self.track_data = {}
        self.meas_data = {}
        self.zoom_pos = (0, 0)
        self.transformed_img = None
        self.abort_autotrack = False
        self.playback_speed = 1
        self.n_diff = 4
        self.workspace_contexts = []
        self.active_cine_index = -1
        self.initial_cine_paths = []
        
        # private properties
        self._image_scale = 1
        self._area_units = ''
        self._active_track_tool = None
        self.track_type = 'Auto'
        self.pending_tracking_method = None
        self._raw_enabled = False

        self._graph = 'main_graph'
        self._magnifier = 'magnifier'
        self._template = 'qt_template_img'
        self._active_frame = 0
        self._mouse_pos = None
        self._image_scale_text = None
        self._image_brightness = 0
        self._image_contrast = 1
        self._image_gamma = 1
        
        #cache
        self._cache_enabled = config_model.get('cache_enabled', True)
        self._max_cache_ram_mb = config_model.get('max_cache_ram_mb', 512)
        self._processed_cache = {}
        self._processed_cache_size_bytes = 0
        self._current_processing_params = None

        self._init_ipc()
        self._toggle_ftone = False


        # read config.json and init properties here
        #Default values as per change request
        self._known_length = 1.0
        self.config.set('known_length', self._known_length)
        self.length_units = 'pix'
        self.config.set('length_units', self.length_units)
        self._area_units = 'sq-pix'
        self.image_scale = 1.0
        self.config.set('image_scale', self.image_scale)
    

        val = self.config.get('time_units')
        if val is None: self.time_units = 's'
        else: self.time_units = val

        val = self.config.get('time_scale')
        if val is None: self.time_scale = 1
        else: self.time_scale = val

        val = self.config.get('autogen_report_name')
        if val is None: self.autogen_report_name = True
        else: self.autogen_report_name = val

        val = self.config.get('plot_n_difference')
        if val is None: self.n_diff = 4
        else: self.n_diff = val
        
        # check args for info to save into class data
        requested_paths = args.get('cine_paths', [])
        if isinstance(requested_paths, str):
            requested_paths = [requested_paths]
        if not requested_paths and args.get('cine_path'):
            requested_paths = [args['cine_path']]
        self.initial_cine_paths = list(dict.fromkeys(
            p for p in requested_paths if isinstance(p, str) and p
        ))[:4]
        if self.initial_cine_paths:
            self.cine_path = self.initial_cine_paths[0]

    _workspace_state_fields = (
        'cine_handler', 'cine_path', 'image_tools', 'cal', 'track_data', 'meas_data',
        'active_tool', 'active_object', '_active_track_tool', '_active_frame', 'zoom_pos',
        'transformed_img', 'abort_autotrack', '_image_brightness', '_image_contrast',
        '_image_gamma', '_toggle_ftone', '_raw_enabled', '_processed_cache',
        '_processed_cache_size_bytes', '_current_processing_params', '_image_scale',
        '_known_length', '_length_units', '_area_units', '_time_units', '_time_scale'
    )

    def _new_workspace_context(self, handler, cine_path):
        metadata = handler.metadata
        return {
            'cine_handler': handler,
            'cine_path': cine_path,
            'image_tools': ImageTools(),
            'cal': Calibration(),
            'track_data': {},
            'meas_data': {'_curr_i': 0},
            'active_tool': None,
            'active_object': None,
            '_active_track_tool': None,
            '_active_frame': 0,
            'zoom_pos': (0, 0),
            'transformed_img': None,
            'abort_autotrack': False,
            '_image_brightness': metadata.Bright,
            '_image_contrast': metadata.Contrast,
            '_image_gamma': metadata.Gamma,
            '_toggle_ftone': True,
            '_raw_enabled': False,
            '_processed_cache': {},
            '_processed_cache_size_bytes': 0,
            '_current_processing_params': None,
            '_image_scale': self._image_scale,
            '_known_length': self._known_length,
            '_length_units': self._length_units,
            '_area_units': self._area_units,
            '_time_units': self._time_units,
            '_time_scale': self._time_scale,
        }

    def sync_active_workspace_context(self):
        if not (0 <= self.active_cine_index < len(self.workspace_contexts)):
            return
        context = self.workspace_contexts[self.active_cine_index]
        for field in self._workspace_state_fields:
            context[field] = getattr(self, field)

    def _restore_workspace_context(self, index):
        context = self.workspace_contexts[index]
        for field in self._workspace_state_fields:
            setattr(self, field, context[field])
        self.cal.update_cal(1, self._image_scale)
        self.cal.update_time(self._time_scale)
        self.active_cine_index = index

    def workspace_summaries(self):
        self.sync_active_workspace_context()
        return [
            {
                'index': index,
                'path': context['cine_path'],
                'frame_count': context['cine_handler'].metadata.ImageCount,
                'active_frame': context['_active_frame'],
            }
            for index, context in enumerate(self.workspace_contexts)
        ]

    def close_workspace(self):
        handlers = []
        for context in self.workspace_contexts:
            handler = context.get('cine_handler')
            if handler is not None and handler not in handlers:
                handlers.append(handler)
        if not handlers and getattr(self, 'cine_handler', None) is not None:
            handlers.append(self.cine_handler)
        for handler in handlers:
            try:
                handler.close()
            except Exception:
                logging.exception('Failed to close a Cine workspace handler')

    def load_cine_workspace_cb(self, cine_paths):
        paths = list(dict.fromkeys(
            p for p in (cine_paths or []) if isinstance(p, str) and p
        ))[:4]
        if not paths:
            return False

        logging.info('Loading Cine workspace: %s', paths)
        new_contexts = []
        try:
            for cine_path in paths:
                handler = CineHandler()
                handler.load_cine(cine_path)
                new_contexts.append(self._new_workspace_context(handler, cine_path))
        except Exception as e:
            for context in new_contexts:
                try:
                    context['cine_handler'].close()
                except Exception:
                    pass
            logging.exception('Failed to load Cine workspace')
            self.update_status_text.emit(f'Error loading Cine workspace: {e}')
            return False

        self.close_workspace()
        self.workspace_contexts = new_contexts
        self.active_cine_index = -1
        self.workspace_changed.emit(self.workspace_summaries(), 0)
        self.activate_cine_cb(0, save_current=False)
        logging.info('Cine workspace loaded successfully with %s file(s)', len(paths))
        return True

    def activate_cine_cb(self, index, save_current=True):
        if not (0 <= index < len(self.workspace_contexts)):
            return False
        if save_current:
            self.sync_active_workspace_context()
        self._restore_workspace_context(index)

        metadata = self.cine_handler.metadata
        self.report_handler.active_report.cine_path = self.cine_path
        self.workspace_changed.emit(self.workspace_summaries(), index)
        self.new_cine_load.emit(self.cine_path, metadata)
        self.first_frame = f'{metadata.FirstImageNo}'
        self.last_frame = f'{metadata.ImageCount + metadata.FirstImageNo - 1}'
        self.trigger_time = metadata.TriggerTime_displ
        self.image_brightness = self._image_brightness
        self.image_contrast = self._image_contrast
        self.image_gamma = self._image_gamma
        self.toggle_ftone = self._toggle_ftone
        self.image_scale_text = self.format_scale_text(self.image_scale)
        self.new_track_data.emit(self.track_data, self.active_object)
        self.track_fig_changed.emit(self.track_data)
        self.draw_all()
        return True

    def workspace_frame(self, index):
        if not (0 <= index < len(self.workspace_contexts)):
            return None
        current_index = self.active_cine_index
        self.sync_active_workspace_context()
        self._restore_workspace_context(index)
        frame_index = int(np.clip(self._active_frame, 0, self.cine_handler.metadata.ImageCount - 1))
        processing_params = (
            self.image_brightness, self.image_contrast, self.image_gamma,
            self.toggle_ftone, self.raw_enabled, frame_index
        )
        if self._cache_enabled and processing_params in self._processed_cache:
            img = self._processed_cache[processing_params]
        else:
            img = self._apply_image_tools(self.cine_handler.get_img(frame_index))
            if self._cache_enabled:
                self._processed_cache[processing_params] = img
                self._processed_cache_size_bytes += img.nbytes
        self.transformed_img = img
        img = np.copy(img)
        display_cfa = 'Mono' if self.raw_enabled else self.cine_handler.metadata.CFA
        result = (img, self.cine_handler.metadata.RealBPP, display_cfa)
        self.sync_active_workspace_context()
        if 0 <= current_index < len(self.workspace_contexts):
            self._restore_workspace_context(current_index)
        return result

        
    #region EVENT CALLBACKS
    # add a '_cb' to the end of all callback functions for readability
    def load_cine_cb(self, cine_path):
        return self.load_cine_workspace_cb([cine_path])
            

    def graph_click_cb(self, mouse_pos):
        # if meas is active, collect the points
        if self.active_tool is not None and self.cine_path != '':
            if self.active_tool.clear_points():
               self.clear_scene.emit()
            self.clear_scene.emit()
            self.active_tool.collect_points(mouse_pos, self.active_frame)
            if self.active_tool is not None:
                if self.active_tool == self._active_track_tool and self.active_object is not None:
                    self.track_fig_calculations()
                    self.new_track_data.emit(self.track_data, self.active_object)
                    self.draw_points_lines(self.active_tool.points, self.track_data[self.active_object]['color'])
                    self._draw_frame(self._template, current_point=self.zoom_pos)
                    
                else:
                    self.draw_points_lines(self.active_tool.points)
            else:
                self.update_status_text.emit('')
                self.clear_scene.emit()

    def draw_points_lines(self, points=None, color='red'):
        if points is None:
            points = self.points
        self.draw_points.emit(self._graph, points, color)
        self.draw_lines.emit(self._graph, points, self.active_tool._point_target, self.active_tool._connect_pairs,
                                self.active_tool._connect_box_from_two_points, self.active_tool._connect_at_end, color)
        if self.active_tool._draw_label:
            self.draw_text_label.emit(self._graph, points, color)            
                                
    def playback_speed_changed_cb(self, speed):
        self.playback_speed = speed

    def right_arrow_keypress_cb(self):
        # go to next frame
        if self.cine_path != '' and self.cine_handler.cine_loaded:
            new_frame = self.active_frame + self.playback_speed
            max_frame = self.cine_handler.metadata.ImageCount - 1
            self.active_frame = min(new_frame, max_frame)

    def left_arrow_keypress_cb(self):
        # go to prev frame
        if self.cine_path != '' and self.cine_handler.cine_loaded:
            new_frame = self.active_frame - self.playback_speed
            self.active_frame = max(new_frame, 0)

    def autotrack_cb(self, track_all=False):
        if self.cine_path != '':
            if self.track_type == 'Auto' and isinstance(self.active_tool, AutoTrackTool):
                self.active_tool.collect_points((-1,-1), self.active_frame, track_all)
                if track_all:
                    for t in self.track_data.values():
                        if t['enabled']:
                            self.track_fig_calculations(t)
                else:
                    self.track_fig_calculations()
                self.new_track_data.emit(self.track_data, self.active_object)

    def update_mouse_pos_cb(self, mouse_pos, clear=False):
        if not clear:
            self.mouse_pos = f"Coordinates:\n{(round(mouse_pos[0], 2), round(mouse_pos[1], 2))}"
            if self.transformed_img is not None:
                clipped_x = np.clip(mouse_pos[0], 0, self.transformed_img.shape[1] - 1)
                clipped_y = np.clip(mouse_pos[1], 0, self.transformed_img.shape[0] - 1)
                raw = np.array(self.cine_handler.get_img(self.active_frame))
                clipped_x = np.clip(mouse_pos[0], 0, raw.shape[1] - 1)
                clipped_y = np.clip(mouse_pos[1], 0, raw.shape[0] - 1)
                
                self.pixel_val = f"RAW: {raw[clipped_y, clipped_x]}\n{self.transformed_img[clipped_y, clipped_x]}"
            else:
                self.pixel_val = 'RAW:\nN/A'
        else:
            self.mouse_pos = 'Coordinates:\nN/A'
            self.pixel_val = 'RAW:\nN/A'

    def graph_mouse_motion_cb(self, mouse_pos):
        if self.cine_handler.cine_loaded:
            self.zoom_pos = mouse_pos
            self._draw_frame(self._magnifier, current_point=self.zoom_pos)
    
    def add_new_template_cb(self):
        if self.cine_path != '' and isinstance(self.active_tool, TrackTool):
            self.update_status_text.emit("Click the object center to set the position of a new track object")
            self.active_tool.track_select = True
        else:
            self.update_status_text.emit('Select a cine and/or enable "TRACK" mode to set a new track object')

    def reset_image_tools_cb(self):
        if self.cine_handler.cine_loaded:
            self.image_brightness = self.cine_handler.metadata.Bright
            self.image_contrast = self.cine_handler.metadata.Contrast
            self.image_gamma = self.cine_handler.metadata.Gamma
            pass
        else:
            self.image_brightness = 0
            self.image_contrast = 1
            self.image_gamma = 1

    def redraw_cb(self):
        if self.cine_handler.cine_loaded:
            self.draw_all()

    def cal_pt_click_cb(self):
        # This sends instruction to user on how to define the scale. Needs to be in cb because it needs to occur before point drawing**
        if self.cine_path != '':
            self.update_status_text.emit('Left click the two ends of the scale in the active image')
            self.clear_scene.emit()
            self.active_tool = ScaleTool(vm=self)
            self.report_handler.update_active_report('meas')

    def track_tool_click_cb(self):
        if self.cine_path != '':
            self.clear_scene.emit()
            self.report_handler.update_active_report('track')
            self._update_track_type()
            self.update_status_text.emit('')

    def two_pt_click_cb(self):
        if self.cine_path != '':
            self.report_handler.update_active_report('meas')
            self.clear_scene.emit()
            self.active_tool = TwoPointTool(vm=self)
            self.update_status_text.emit('')
        
    def three_pt_click_cb(self):
        if self.cine_path != '':
            self.report_handler.update_active_report('meas')
            self.clear_scene.emit()
            self.active_tool = ThreePointTool(vm=self)
            self.update_status_text.emit('')

    def two_line_click_cb(self):
        if self.cine_path != '':
            self.report_handler.update_active_report('meas')
            self.clear_scene.emit()
            self.active_tool = TwoLineTool(vm=self)
            self.update_status_text.emit('')

    def area_pt_click_cb(self):
        if self.cine_path != '':
            self.report_handler.update_active_report('meas')
            self.clear_scene.emit()
            self.active_tool = AreaTool(vm=self)
            self.update_status_text.emit('')

    def clear_frame_cb(self):
        if self.cine_path != '':
            self.clear_scene.emit()
        self.active_tool = None
        self.update_status_text.emit('')

    def update_object_name_cb(self, name, template_id):
        t = self.track_data[template_id]
        t['name'] = name
        self.track_data[template_id] = t
        self.track_fig_changed.emit(self.track_data)

    def update_point_element_cb(self, element, element_type, row):
        self.track_data[self.active_object][element_type][row] = element
        if element_type == 'frames':
            ind = np.argsort(self.track_data[self.active_object]['frames'])
            self.track_data[self.active_object]['points'] = self.track_data[self.active_object]['points'][ind]
            self.track_data[self.active_object]['frames'] = self.track_data[self.active_object]['frames'][ind]
            self.track_data[self.active_object]['scores'] = self.track_data[self.active_object]['scores'][ind]
            if 'angles' in self.track_data[self.active_object]:
                self.track_data[self.active_object]['angles'] = self.track_data[self.active_object]['angles'][ind]
        self.track_fig_calculations()
        self.redraw_cb()
        self.new_track_data.emit(self.track_data, self.active_object)
    
    def track_type_changed_cb(self, event):
        self.track_type = event.text()
        self._update_track_type()

    def track_fig_refresh_cb(self):# is this necessary? we can just call redraw in the UI
        self.track_fig_changed.emit(self.track_data)  

    def track_table_selection_cb(self, val):
        if len(val.indexes()) > 0:
            row = val.indexes()[0].row()
            if row in self.track_data:
                self.active_object = row
                self.track_fig_calculations()
                self._draw_frame(self._template, current_point=self.track_data[self.active_object]['points'][0])

    def update_autotrack_params_cb(self, parameter, value):
        if self.active_object is not None:
            self.track_data[self.active_object][parameter] = value

    def apply_params_to_all(self):
        if len(self.track_data) > 1:
            params = ['start', 'end', 'search_area', 'tpl_rng', 'subpixel_size', 'subpixel_type', 'frames_enable', 
                      'search_area_enable', 'tpl_rng_enable', 'update_template_enable', 'acceptable_score',
                      'tracking_method', 'rotation_range', 'rotation_step', 'edge_weight',
                      'adjacent_confidence_weight',
                      'edge_threshold', 'rotation_allowed', 'smart_frames', 'smart_miss_limit',
                      'search_area_multiplier']
            for id, t in self.track_data.items():
                if t['enabled'] and id != self.active_object:
                    for p in params:
                        t[p] = self.track_data[self.active_object][p]
            self.draw_all()

    def export_cb(self, filepath=''):
        try:
            data = None
            p = None
            self.report_handler.active_report.cine_path = self.cine_path
            if filepath != '':
                # if filepath was passed in, overwrite the autogenerated report_path property
                self.report_handler.active_report.report_path = filepath

            if self.report_handler.active_type == 'meas':
                logging.info(f'Exporting {self.report_handler.active_type} report to {filepath}')
                data = self.meas_data
                p = self.report_handler.active_report.export_report(data)
                
            elif self.report_handler.active_type == 'track':
                logging.info(f'Exporting {self.report_handler.active_type} report to {filepath}')
                data = self.track_data
                metadata = {}
                imgs = np.zeros(len(self.track_data), dtype=object)

                for i, t in self.track_data.items():
                    origin_frame = t.get('origin_frame', None)
                    if origin_frame is not None:
                        template_frame = origin_frame
                    else:
                        template_frame = t['frames'][0]
                    
                    #switch frames to catch the origins
                    original_frame = self.active_frame
                    self.active_frame = template_frame
                    imgs[i] = self._draw_frame()
                    self.active_frame = original_frame
                    
                    t['frame_ts_trig'] = self.cal.time_transform(self.cine_handler.get_timestamp(t['frames'])['time_from_trig'])
                    t['X-Vibration'] = (self.vibration('X', obj=i))
                    t['Y-Vibration'] = (self.vibration('Y', obj=i))

                metadata['imgs'] = imgs
                metadata['scale'] = self.image_scale
                metadata['units'] = self.length_units
                metadata['time_units'] = self.time_units.replace('μ', 'u')
                metadata['first_frame'] = self.first_frame
                metadata['trigger_timestamp'] = self.cine_handler.metadata.TriggerTime_displ
                metadata['other_md'] = self.cine_handler.print_metadata()
                metadata['_n_diff'] = self.n_diff
                p = self.report_handler.active_report.export_report(data, metadata)

            if p:
                self.update_status_text.emit(f'{self.report_handler.active_type.capitalize()} Report was exported to: {p}')
            
        except Exception as e:
            logging.exception(e)
            self.update_status_text.emit(f'{e}')
            raise(e)
                
    def vibration(self, val, obj=None):
        if self.cine_path != '':
            if obj is None: obj = self.active_object
            signal = self.track_data[obj][val+'-Displacement']
            frame_rate = self.cine_handler.metadata.DecimatedFrameRate
            return self.meas.fourier_transform(signal, frame_rate) # add n_peaks from config  

    def time_selection_cb(self, event):
        self.time_units = event
        if self.time_units == 's':
            self.time_scale = 1
        elif self.time_units == 'ms':
            self.time_scale = 1e3
        elif self.time_units == 'μs':
            self.time_scale = 1e6
        for t in self.track_data.values():
            self.track_fig_calculations(obj=t)

    def zoom_cb(self):
        if self.cine_path != '':
            self._draw_frame(self._magnifier, current_point=self.zoom_pos)

    def scale_hover_cb(self):
        scale = f'{self.image_scale:.7g}'
        if len(scale) > 6:
            self.image_scale_text = f'{scale} \n{self.length_units}/px'
        else:
            self.image_scale_text = f'{scale} {self.length_units}/px'

    def scale_leave_cb(self):
        self.image_scale_text = self.format_scale_text(self.image_scale)

    def edit_scale_cb(self, scale, length_units):
        self.image_scale = scale
        self.length_units = length_units
        self.image_scale_text = self.format_scale_text(self.image_scale)
        self.track_fig_changed.emit(self.track_data)

    def toggle_ftone_cb(self, enabled):
        """Toggle ftone curve application on/off"""
        self.toggle_ftone = enabled
            
#endregion

#region PUBLIC FUNCTIONS
    def init_ui(self):
        # if value in self.cine_path, load it up
        if self.initial_cine_paths:
            self.load_cine_workspace_cb(self.initial_cine_paths)

        self.update_status_text.emit('')
        self.image_scale_text = self.format_scale_text(self.image_scale)

    def clear_track(self):
        self.track_data = {}
        self.active_object = None
        self._active_track_tool = None
        self.new_track_data.emit(self.track_data, self.active_object)
        self.track_fig_changed.emit(self.track_data)
        
    def draw_all(self, points=None):
        self._draw_frame(self._graph)
        tft = self.cine_handler.get_timestamp(self.active_frame)['time_from_trig']
        self.time_from_trigger = f'{round(tft, 2)} μs'
        self._draw_overlays(points)

# endregion

#region PRIVATE FUNCTIONS 
    def _init_ipc(self):
        self.ipc = IPCController()
        def handle_new_file_path(payload, respond):
            try:
                new_path = payload.decode('utf-8')
                self.request_choose_cine.emit(new_path)
                error_code = 0
                respond('new_file_path_ack', error_code.to_bytes(4, byteorder='little', signed=False))
            except Exception as e:
                error_code = 1
                respond('new_file_path_ack', error_code.to_bytes(4, byteorder='little', signed=False))
        self.ipc.register_message_handler('new_file_path', handle_new_file_path)
        self.ipc.start()

    def _update_track_type(self):
        if self.track_type == 'Manual':
            self.active_tool = ManualTrackTool(vm=self)
            self._active_track_tool = self.active_tool
            self.active_tool.draw_all() 
        elif self.track_type == 'Auto':
            self.active_tool = AutoTrackTool(vm=self)
            self._active_track_tool = self.active_tool
            self.active_tool.draw_all()

    def _refresh_template(self, template_id):
        if template_id != None:
            self.active_object = template_id
            pt = None
            arr = np.argwhere(self.track_data[self.active_object]['frames']<=self.active_frame)[:,0]
            if arr.size > 0: 
                index = np.max(arr)
                pt = tuple(self.track_data[self.active_object]['points'][index])
                pt = (int(pt[0]), int(pt[1]))
                fr = self.track_data[self.active_object]['frames'][index]
            if pt is not None:
                self._draw_frame(self._template, index=fr, current_point=pt)
        
    def _draw_frame(self, graph=None, index=None, current_point=None):
        if index is None: 
            index = self.active_frame
        if graph == self._graph:
            index = np.clip(index, 0, self.cine_handler.metadata.ImageCount - 1)
            
            #cache key
            processing_params = (
                self.image_brightness, self.image_contrast, self.image_gamma,
                self.toggle_ftone, self.raw_enabled, index
            )
            
            #check cache image
            if self._cache_enabled and processing_params in self._processed_cache:
                img = self._processed_cache[processing_params]
                self.transformed_img = img
            else:
                #raw
                img = self.cine_handler.get_img(index)
                img = self._apply_image_tools(img)
                
                if self._cache_enabled:
                    frame_size_bytes = img.nbytes
                    max_cache_bytes = self._max_cache_ram_mb * 1024 * 1024
                    #eviction
                    while (self._processed_cache_size_bytes + frame_size_bytes > max_cache_bytes 
                           and len(self._processed_cache) > 0):
                        oldest_key = next(iter(self._processed_cache))
                        oldest_img = self._processed_cache[oldest_key]
                        self._processed_cache_size_bytes -= oldest_img.nbytes
                        del self._processed_cache[oldest_key]
                    
                    self._processed_cache[processing_params] = img
                    self._processed_cache_size_bytes += frame_size_bytes
                
                self.transformed_img = img
        else:
            img = self.transformed_img

        # if graph is none, return the image
        if graph is None:
            return img
        
        self.draw_frame.emit(graph, img, self.cine_handler.metadata.RealBPP, self.cine_handler.metadata.CFA, current_point)
       
    def _draw_overlays(self, points=None):
        if self.cine_handler.cine_loaded:
            self._refresh_template(self.active_object)
            if self.active_tool:
                if self.active_tool == self._active_track_tool:
                    self.active_tool.draw_all()
                else:
                    self.draw_points_lines(self.active_tool.points)
            

    def _apply_image_tools(self, img):
        cfa = self.cine_handler.metadata.CFA
        bpp = self.cine_handler.metadata.RealBPP
        r_gain = self.cine_handler.metadata.WBGain[0]
        b_gain = self.cine_handler.metadata.WBGain[1]
        black = self.cine_handler.metadata.BlackLevel
        white = self.cine_handler.metadata.WhiteLevel
        cm_calib = self.cine_handler.metadata.cmCalib
        bright = self.image_brightness/2**int(bpp)
        ftone = self.cine_handler.metadata.fTone
        if not self.toggle_ftone: ftone = None

        if white <= black:
            white = black + 1

        if self.raw_enabled:
            if cfa != 'Mono':
                #TODO: is this ok?
                img = img.astype(np.float32) if img.dtype != np.float32 else img
                img = self.image_tools.apply_wb(img, r_gain, b_gain, cfa, bpp)
            if bpp == 8 or bpp == 10 or bpp == 12:
                #TODO: is this ok?
                img = img.astype(np.uint16) if img.dtype != np.uint16 else img
                img = self.image_tools.cast_to_16bpp(img)
            img = img * (1 << (16 - bpp))
            return img

        # defect pixel correction
        img = img.astype(np.float32) if img.dtype != np.float32 else img
        img = self.image_tools.apply_dmap_correction(img, cfa, bpp)
        
        if cfa == 'Mono':
            img = self.image_tools.apply_brightness(img, bright)
            img = self.image_tools.apply_contrast(img, float(self.image_contrast))
            img = self.image_tools.apply_gamma(img, float(self.image_gamma), bpp)
            if ftone is not None:
                img = self.image_tools.apply_ftone(img, ftone)
            img = self.image_tools.stretch_image_rng(img, 16, int(black), int(white))
            img = self.image_tools.cast_to_16bpp(img)
            return img

        else:
            bright = self.image_brightness #TODO: check if this is correct for color
            config = ImageProcessingConfig(
                r_gain=r_gain,
                b_gain=b_gain,
                brightness=bright,
                contrast=self.image_contrast,
                gamma=self.image_gamma,
                cfa=cfa,
                bpp=bpp,
                ftone=ftone,
                cm_calib=cm_calib,
                black_offset=black,
                white_level=white,
                enable_timing=False
            )

            img = self.image_tools.apply_color_pipeline(img, config)
           
            return img
        
    def track_fig_calculations(self, obj=None):
        if self.track_data != {}:
            t = self.track_data[self.active_object] if obj is None else obj
            time_from_start_values = []
            time_from_trigger_values = []
            for frame in t['frames']:
                timestamp_data = self.cine_handler.get_timestamp(frame)
                time_from_start_values.append(timestamp_data['time_from_start'])
                time_from_trigger_values.append(
                    timestamp_data.get('time_from_trig', timestamp_data['time_from_start'])
                )
            t['frame_ts'] = self.cal.time_transform(np.array(time_from_start_values))
            t['frame_ts_trig'] = self.cal.time_transform(np.array(time_from_trigger_values))
            rel_id = t['relative_to'] if 'relative_to' in t else None
            if rel_id != None:
                rel_obj = self.track_data[rel_id]
                rel_frames = rel_obj['frames']
                rel_points = rel_obj['points']
                # For each frame in t['frames'], find the corresponding point in rel_obj, else [0,0]
                origin = []
                for f in t['frames']:
                    if f in rel_frames:
                        idx = np.where(rel_frames == f)[0][0]
                        origin.append(rel_points[idx])
                    else:
                        origin.append(np.zeros_like(rel_points[0]))
                origin = np.array(origin)
            else:
                # determine what the origin frame should be
                origin_frame = t['origin_frame']
                frames = t['frames']
                points = t['points']
                if origin_frame is None:
                    origin = points[0]
                else:
                    idxs = np.where(frames == origin_frame)[0]
                    if idxs.size > 0:
                        origin = points[idxs[0]]
                    else:
                        origin = points[0]
                        t['origin_frame'] = None

            frame_diff = self._n_diff(t['frame_ts'], self.n_diff)
            disp = np.abs(t['points'] - origin)
            diffs = self._n_diff(disp, self.n_diff)

            t['X-Displacement'] = disp[:,0]
            t['Y-Displacement'] = disp[:,1]
            t['Displacement'] = np.linalg.norm(disp, axis=1)
            t['X-Speed'] = diffs[:,0] / frame_diff
            t['Y-Speed'] = diffs[:,1] / frame_diff
            t['Speed'] = np.linalg.norm(diffs, axis=1) / frame_diff

            t['X-Acceleration'] = self._n_diff(t['X-Speed'], self.n_diff) / frame_diff[self.n_diff:]
            t['Y-Acceleration'] = self._n_diff(t['Y-Speed'], self.n_diff) / frame_diff[self.n_diff:]
            t['Acceleration'] = np.sqrt(np.square(t['X-Acceleration']) + np.square(t['Y-Acceleration'])) / frame_diff[self.n_diff:]

            angles = np.asarray(t.get('angles', np.zeros(len(t['frames']))), dtype=float)
            if len(angles) != len(t['frames']):
                angles = np.zeros(len(t['frames']), dtype=float)
            # Unwrap across +/-180 so angular motion remains continuous.
            t['Angle'] = np.rad2deg(np.unwrap(np.deg2rad(angles)))
            if len(t['Angle']) > self.n_diff:
                t['Angular Speed'] = self._n_diff(t['Angle'], self.n_diff) / frame_diff
            else:
                t['Angular Speed'] = np.array([])
            
            self.track_fig_changed.emit(self.track_data)


    def _n_diff(self, arr: np.ndarray, n):
        return arr[n:] - arr[:-n]

    def _get_template_point_at_frame_id(self, template_id, frame_id):
        points = self.track_data[template_id]['points']
        frames = self.track_data[template_id]['frames']
        i = np.argwhere(frames==frame_id)[:,0]

        if i.size > 0: 
            i = i[0]
        elif i.size == 0:
            i = len(frames)-1
        pt = (int(points[i][0]), int(points[i][1]))
        return pt, frames[i], i
    
    def format_scale_text(self, scale, ext=False):
        if 0.001 <= scale < 1:
            return f'{scale:.2g} {self.length_units}/px'
        elif 1 <= scale < 10:
            return f'{scale:.3g} {self.length_units}/px'
        elif 10 <= scale < 10000:
            return f'{scale:.4g} {self.length_units}/px'
        else: 
            return f'{scale:.2e} \n{self.length_units}/px'

    #endregion
  
