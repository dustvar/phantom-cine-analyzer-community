import math
import numpy as np
from scipy.signal import find_peaks


class MeasModel:
    def __init__(self):
        pass

    def meas_displacement(self, points):
        """
        Calculate the absolute displacement between two points.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.

        Returns:
            Tuple[float, float, float]: A tuple containing the absolute displacement in x-direction,
                                        y-direction, and the absolute distance between the two points.

        Raises:
            ValueError: If the points list is empty or None, or if the points list does not have exactly two elements.
                        If any point in the list does not have exactly two coordinates.
         """

        if points is None or len(points) != 2:
            raise ValueError("Points list must have exactly two elements.")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates.")

        pixel_x_disp = abs(points[0][0] - points[1][0])
        pixel_y_disp = abs(points[0][1] - points[1][1])

        abs_pixel_dist = math.dist(points[0], points[1])

        return pixel_x_disp, pixel_y_disp, abs_pixel_dist

    def meas_speed(self, points, fps, activeframe):
        """
        Calculate the speed between two points over a given time period.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.
            fps (float): Frames per second.
            activeframe (Tuple[int, int]): The range of frames to consider.

        Returns:
            Tuple[float, float, float]: A tuple containing the speed in x-direction,
                                        y-direction, and the absolute distance between the two points per second.

        Raises:
            ValueError: If any argument is None.
                        If the points list is empty or None, or if the points list does not have exactly two elements.
                        If any point in the list does not have exactly two coordinates.
        """
        if points is None or fps is None or activeframe is None:
            raise ValueError("All arguments must be non-null")

        if len(points) != 2:
            raise ValueError("Points list must have exactly two elements")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        frame_period = 1/fps
        framedelta = abs(activeframe[1] - activeframe[0])
        timedelta = framedelta*frame_period

        pixel_x_disp, pixel_y_disp, abs_pixel_dist = MeasModel.meas_displacement(self, points)
        if framedelta == 0:
            speedx, speedy, abs_speed = 0.0, 0.0, 0.0
        else:
            speedx, speedy, abs_speed = pixel_x_disp / \
                timedelta, pixel_y_disp/timedelta, abs_pixel_dist/timedelta
        return speedx, speedy, abs_speed

    def meas_angle(self, points):
        """
        Calculate the angle between three points.

        Args:
            points (List[Tuple[float, float]]): A list of three points, each represented by a tuple of two floats.

        Returns:
            float: The angle in degrees.

        Raises:
            ValueError: If the points list is empty or None, or if the points list does not have exactly three elements.
                        If any point in the list does not have exactly two coordinates.
            ValueError: If the points are colinear and the angle cannot be calculated.
            ZeroDivisionError: If the points are colinear and the angle cannot be calculated.
        """

        if len(points) != 3:
            raise ValueError("Points list must have exactly 3 elements")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        p1 = points[0]
        p3 = points[1]   # Middle point, pivot point, or fulcrum
        p2 = points[2]

        # This takes into account canvas scaling:
        a = math.sqrt((p2[0] - p3[0])**2 + (p2[1] - p3[1])**2)
        b = math.sqrt((p1[0] - p3[0])**2 + (p1[1] - p3[1])**2)
        c = math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)

        # Protection against divide by zero
        try:
            angle = math.degrees(math.acos((a**2 + b**2 - c**2) / (2 * a * b)))
        except ZeroDivisionError:
            raise ValueError("Divide by zero error")

        return angle

    def meas_angle_blines(self, points):
        """
        Calculate the angle between four points.

        Args:
            points (List[Tuple[float, float]]): A list of four points, each represented by a tuple of two floats.

        Returns:
            Union[float, str]: The angle in degrees, or 'null result' if the points are collinear.

        Raises:
            ValueError: If the points list is empty or None, or if the points list does not have exactly four elements.
                        If any point in the list does not have exactly two coordinates.
            ValueError: If any vector in the points list has zero magnitude.
        """

        if len(points) != 4:
            raise ValueError("Points list must have exactly 4 elements")

        p0 = points[0]  # First point
        p1 = points[1]  # Second point
        # Third point, we will force p2 and p0 to match in position for calculation
        p2 = points[2]
        # Fourth point, we shift p3 in space a distance p2-p0, so law of cosines is viable
        p3 = points[3]

        if p0 is None or p1 is None or p2 is None or p3 is None:
            raise ValueError("Each point in the list must have valid coordinates")

        # Vectorization of points (from origin)
        v1 = (p1[0] - p0[0], p1[1] - p0[1])
        v2 = (p3[0] - p2[0], p3[1] - p2[1])

        if any(vector == (0, 0) for vector in (v1, v2)):
           if v1 == (0, 0) or v2 == (0, 0):
               raise ValueError("Vectors must have non-zero magnitude")

        # Dot product of the two vectors (a-dot-b = mag(a)*mag(b)*cos(theta))   where a-dot-b = AxBx + AyBy + AzBz + ....
        v2_dot_v1 = (v1[0]*v2[0] + v1[1]*v2[1])
        mag1 = math.sqrt(v1[0]**2 + v1[1]**2)
        mag2 = math.sqrt(v2[0]**2 + v2[1]**2)

        if mag1 == 0 or mag2 == 0:
            raise ValueError("Vectors must have non-zero magnitude")

        return math.degrees(math.acos(v2_dot_v1 / (mag1 * mag2)))

    def meas_angular_rate(self, points, fps, activeframe):
        """
        Calculate the angular rate between points over a given time period.

        Args:
            points (List[Tuple[float, float]]): A list of points, each represented by a tuple of two floats.
            fps (float): Frames per second.
            activeframe (Tuple[int, int]): The range of frames to consider.

        Returns:
            float: The angular rate in degrees per second.

        Raises:
            ValueError: If any argument is None.
                        If the points list is empty or None, or if any point in the list does not have exactly two coordinates.
        """
        if points is None or fps is None or activeframe is None:
           raise ValueError("All arguments must be non-null")

        if any(point is None for point in points):
            raise ValueError(
                "Points list must have non-empty elements and each point must have valid coordinates")

        # Get angle between points and scales points
        ang = MeasModel.meas_angle(self, points)

        # Capture of temporal values and deltas
        frame_period = 1/fps
        framedelta = abs(activeframe[2] - activeframe[0])
        timedelta = framedelta*frame_period

        if framedelta == 0:
           angular_rate = 0.0
        else:
           angular_rate = ang / timedelta
        return angular_rate

    def meas_angular_rate_between_lines(self, points, fps, activeframe):
        """
        Calculate the angular rate between two tail-to-tip segments/vectors.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.
            fps (float): Frames per second.
            activeframe (Tuple[int, int]): The range of frames to consider.

        Returns:
            float: The angular rate in degrees per second.

        Raises:
            ValueError: If any argument is None.
                        If the points list is empty or None.
                        If any point in the list does not have exactly two coordinates.
            ZeroDivisionError: If the frames delta is zero.
        """

        if points is None or fps is None or activeframe is None:
           raise ValueError("All arguments must be non-null")

        if any(point is None for point in points):
            raise ValueError(
                "Points list must have non-empty elements and each point must have valid coordinates")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        # Gets angle between two tail-to-tip segments/vectors
        angle = MeasModel.meas_angle_blines(self, points)

        # Capture of temporal values and deltas
        frame_period = 1 / fps
        framedelta = abs(activeframe[3] - activeframe[0])
        timedelta = framedelta * frame_period

        if framedelta == 0:
            return 0

        angular_rate = angle / timedelta

        return angular_rate

    def meas_area_between_three_points(self, points):
        """
        Calculate the area between three points.

        Args:
            points (List[Tuple[float, float]]): A list of three points, each represented by a tuple of two floats.

        Returns:
            float: The area in pixels squared.

        Raises:
            ValueError: If the points list is empty or None.
                        If any point in the list does not have exactly two coordinates.
        """
        if any(point is None for point in points):
            raise ValueError(
                "Points list must have non-empty elements and each point must have valid coordinates")

        if len(points) < 3:
            raise IndexError("Points list must have at least three elements")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        p0 = points[0]  # middle point
        p1 = points[1]
        p2 = points[2]  # we will force p2 and p0 to match in position

        # Shoelace Algorithm: 2*Area = det(x1 x2 over y1 y2) + det(x2 x3 over y2 y3) + det(xn x1 over yn y1)
        Area = (1/2) * ((p0[0]*p1[1] - p1[0]*p0[1])
                        + (p1[0]*p2[1] - p2[0]*p1[1])
                        + (p2[0]*p0[1] - p0[0]*p2[1]))

        return Area

    def meas_area_with_two_points(self, points):
        """
        Calculate the area between two points.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.

        Returns:
            float: The area in pixels squared.

        Raises:
            ValueError: If the points list is empty or None.
                        If the points list does not have exactly two elements.
                        If any point in the list does not have exactly two coordinates.
        """
        if len(points) != 2:
            raise ValueError("Points list must have exactly two elements")

        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        p0, p1 = points

        deltaX = abs(p1[0] - p0[0])
        deltaY = abs(p1[1] - p0[1])

        Area = deltaX * deltaY
        return Area

    def avg_pixel_value(self, points, image):
        """
        Calculate the average pixel value within a rectangle defined by four points defined/bounded by p1 & p2.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.
            image (numpy.ndarray): The image to calculate the average pixel value from.

        Returns:
            float: The average pixel value within the rectangle.

        Raises:
            ValueError: If the points list is empty or None.
                        If the points list does not have exactly two elements.
                        If any point in the list does not have exactly two coordinates.
                        If the image is not a numpy.ndarray.
                        If the image dimensions are invalid.
        """
       # This crops out a rectangle between four points defined/bounded by p1 p2
       # p1 must always be in upper left  relative to p2, and these conditionals handle that:

        # Ensure points and image are not None
        if points is None or image is None:
            raise ValueError("Points and Image must not be None")

        # Ensure points have at least two elements
        if len(points) < 2:
            raise ValueError("Points must have at least two elements")

        # Ensure points have valid coordinates
        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        p1, p2 = points

        # Ensure image is a numpy.ndarray with valid dimensions
        if not isinstance(image, np.ndarray):
            raise TypeError("Image must be a numpy.ndarray")
        if image.ndim != 2:
            raise ValueError("Image must have two dimensions")
        if image.shape[0] < max(p1[1], p2[1]) or image.shape[1] < max(p1[0], p2[0]):
            raise ValueError("Image dimensions are invalid")

        # Crop the image
        if p2[0] > p1[0] and p2[1] > p1[1]:
            crop = image[int(p1[1]):int(p2[1]), int(p1[0]):int(p2[0])]
        elif p2[0] < p1[0] and p2[1] > p1[1]:
            crop = image[int(p1[1]):int(p2[1]), int(p2[0]):int(p1[0])]
        elif p2[0] < p1[0] and p2[1] < p1[1]:
            crop = image[int(p2[1]):int(p1[1]), int(p2[0]):int(p1[0])]
        elif p2[0] > p1[0] and p2[1] < p1[1]:
            crop = image[int(p2[1]):int(p1[1]), int(p1[0]):int(p2[0])]
        else:
            raise ValueError("Points must have valid coordinates")

        avg_pixel_val = np.mean(crop).astype(float)
        return avg_pixel_val

    def noise_values(self, points, image):
        """
        Calculate the standard deviation and variance of a crop of the given image defined by the given points.

        Args:
            points (List[Tuple[float, float]]): A list of two points, each represented by a tuple of two floats.
            image (numpy.ndarray): The image to calculate the crop from.

        Returns:
            Tuple[float, float]: A tuple containing the standard deviation and variance of the crop.

        Raises:
            ValueError: If the points list is empty or None.
                        If the points list does not have exactly two elements.
                        If any point in the list does not have exactly two coordinates.
                        If the image is not a numpy.ndarray.
                        If the image dimensions are invalid.
        """
       # The square root of the variance is the RMS value or standard deviation

        # Ensure points and image are not None
        if points is None or image is None:
            raise ValueError("Points and Image must not be None")

        # Ensure points have at least two elements
        if len(points) < 2:
            raise ValueError("Points must have at least two elements")

        # Ensure points have valid coordinates
        for point in points:
            if len(point) != 2:
                raise ValueError(
                    "Each point in the list must have exactly two coordinates")

        p1, p2 = points

        # Ensure image is a numpy.ndarray with valid dimensions
        if not isinstance(image, np.ndarray):
            raise ValueError("Image must be a numpy.ndarray")
        if image.ndim != 2:
            raise ValueError("Image must have two dimensions")
        if image.shape[0] < max(p1[1], p2[1]) or image.shape[1] < max(p1[0], p2[0]):
            raise ValueError("Image dimensions are invalid")

        # Crop the image
        if p2[0] > p1[0] and p2[1] > p1[1]:
            crop = image[int(p1[1]):int(p2[1]), int(p1[0]):int(p2[0])]
        elif p2[0] < p1[0] and p2[1] > p1[1]:
            crop = image[int(p1[1]):int(p2[1]), int(p2[0]):int(p1[0])]
        elif p2[0] < p1[0] and p2[1] < p1[1]:
            crop = image[int(p2[1]):int(p1[1]), int(p2[0]):int(p1[0])]
        elif p2[0] > p1[0] and p2[1] < p1[1]:
            crop = image[int(p2[1]):int(p1[1]), int(p1[0]):int(p2[0])]
        else:
            raise ValueError("Points must have valid coordinates")

        std_dev = np.std(crop, dtype=np.float64).astype(float)
        std_var = np.var(crop, dtype=np.float64).astype(float)

        return std_dev, std_var

    def fourier_transform(self, signal, frame_rate, n_peaks=2):
        freqs = np.fft.fftfreq(len(signal), d=1/float(frame_rate))
        freqs = freqs[np.logical_and(freqs > 0, freqs < frame_rate/2)]

        signal = signal.astype(float) - np.mean(signal)
        fourier = np.fft.fft(signal)[1:len(freqs)+1]
        fft_mag = np.abs(fourier)
        
        peaks, _ = find_peaks(fft_mag)
        largest_peaks_indices = np.argsort(fft_mag[peaks])[-1:-n_peaks-1:-1]
        largest_peaks = peaks[largest_peaks_indices]

        return freqs, fft_mag, largest_peaks

#endregion  