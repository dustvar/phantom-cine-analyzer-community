from enum import Enum
import numpy as np
from pyphantom_config.config import JsonConfig
import os

class CalSpace(Enum):
    IMAGE = 0
    PHYSICAL = 1
    ZOOM = 2

class CalTime(Enum):
    DEFAULT = 0 # Default time scale (seconds)
    USER = 1

class Calibration:
    def __init__(self):
        """
        Initialize the Calibration object.

        This method initializes all the attributes of the Calibration object.
        The attributes include:
        - _zoom_factor: the zoom factor
        - phys_factor: the physical factor
        - _active_space: the active space (default: CalSpace.IMAGE)
        """
        try:
            self.config = JsonConfig(os.path.join(os.path.dirname(__file__), 'config.json'))
            self.phys_factor = self.config.get('image_scale', default=1)
            self.time_factor = self.config.get('time_scale', default=1)
        except:
            self.phys_factor = 1     
            self.time_factor = 1

        self._zoom_factor = 1
        self._active_space = CalSpace.IMAGE
        self._active_time = CalTime.DEFAULT

    def verify_points(self, points):
        '''
        Verify that the input points are valid.
        
        Parameters:
            points (Union[List[Tuple[int, int]], Tuple[int, int]], np.ndarray): The point(s) to be verified.
        
        Returns:
            None
        '''
        if points is None:
            raise TypeError("Input points cannot be None.")
        
        if isinstance(points, np.ndarray) or isinstance(points, list) or isinstance(points, tuple):  # make sure points is an array
            try:
                points = np.array(points)
            except:
                raise ValueError("Invalid input structure")
        else:
            raise TypeError("Input points must be a list, tuple, or array.")    
        
        if np.any(points==None):
            raise ValueError("Input cannot contain empty points.")
            
        if not np.issubdtype(points.dtype, np.integer) and not np.issubdtype(points.dtype, np.floating):
            try:
                points = points.astype(float)
            except:
                raise ValueError("All input points must be integers or floats.")
            
        return points

    def point_transform(self, points, going_to=CalSpace.PHYSICAL, coming_from=CalSpace.IMAGE):
        """
        Transform a point or a list of points from one coordinate space to another.

        Parameters:
            points (Union[List[Tuple[int, int]], Tuple[int, int]], np.ndarray): The point(s) to be transformed.
            going_to (CalSpace, optional): The coordinate space to transform to. Defaults to CalSpace.PHYSICAL.
            coming_from (CalSpace, optional): The coordinate space the point(s) are in. Defaults to CalSpace.IMAGE.

        Returns:
            cal_points (Union[List[Tuple[int, int]], Tuple[int, int]]): The transformed point(s).
        """

        # Check if the input spaces are valid
        if not isinstance(going_to, CalSpace) or not isinstance(coming_from, CalSpace):
            raise TypeError("Invalid input spaces.")
        
        # Check if the input points are valid
        points = self.verify_points(points)

        if coming_from == CalSpace.IMAGE and going_to == CalSpace.PHYSICAL:
            # To go from physical space back to image_space you are simply default cal = 1
            cal_points = points * self._zoom_factor * self.phys_factor

        elif coming_from == CalSpace.PHYSICAL and going_to == CalSpace.IMAGE:
            cal_points = (points / self._zoom_factor / self.phys_factor).astype(int)

        else:
            raise TypeError("Inputs Incorrect")
        return cal_points
    
    def time_transform(self, points, going_to=CalTime.USER, coming_from=CalTime.DEFAULT):
        """
        Transform a point or a list of points from one time space to another.

        Parameters:
            points (Union[List[Tuple[int, int]], Tuple[int, int]], np.ndarray): The point(s) to be transformed.
            going_to (CalSpace, optional): The time space to transform to. Defaults to CalTime.USER.
            coming_from (CalSpace, optional): The time space the point(s) are in. Defaults to CalTime.DEFAULT.

        Returns:
            cal_points (Union[List[Tuple[int, int]], Tuple[int, int]]): The transformed point(s).
        """

        # Check if the input spaces are valid
        if not isinstance(going_to, CalTime) or not isinstance(coming_from, CalTime):
            raise TypeError("Invalid input spaces.")
        
        points = self.verify_points(points)

        if coming_from == CalTime.DEFAULT and going_to == CalTime.USER:
            cal_points = points * self.time_factor

        elif coming_from == CalTime.USER and going_to == CalTime.DEFAULT:
            cal_points = points / self.time_factor

        else:
            raise TypeError("Inputs Incorrect")
        
        return cal_points
        
            

    def update_cal(self, zoom_factor, phys_factor):
        """
        Update the calibration parameters.

        Update the calibration parameters based on the given zoom factor and physical factor.

        Args:
            zoom_factor (float): The zoom factor.
            phys_factor (float): The physical factor.

        Returns:
            None
        """
        if zoom_factor <= 0 or phys_factor <= 0:
            self._zoom_factor = 1
            self.phys_factor = 1
            self.config.set('image_scale', self.phys_factor)
            raise ValueError("Zoom factor and physical factor must be positive, non-zero values.")

        self._zoom_factor = zoom_factor 
        self.phys_factor = phys_factor
        
        try:
            self.config.set('image_scale', self.phys_factor)
        except: pass

    def update_time(self, time_factor):
        """
        Update the calibration parameters.

        Update the calibration parameters based on the given time factor.

        Args:
            time_factor (float): The time factor.

        Returns:
            None
        """
        if time_factor <= 0:
            raise ValueError("Time factor must be positive, non-zero value.")

        self.time_factor = time_factor
        
        try:
            self.config.set('time_scale', self.time_factor)
        except: pass
     