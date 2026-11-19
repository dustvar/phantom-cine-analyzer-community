# PCA 1.12.0 validation

The final application regression suite passed **295 tests** in 52.84 seconds on 27 October 2026. Tests used the local macOS ARM64 Python 3.12 / PySide6 6.9.1 runtime and the actual Qt application stylesheet. Offscreen rendering and mouse/keyboard events were used for repeatable UI checks.

Coverage includes Cine parsing, playback/editing/export helpers, three tracking engines, independent rotated search/ROI geometry, reference pixels/frames, job persistence, local derivative fits, heat-map values, cancellation, and repeated window deletion plus garbage collection. The guided tests decode generated Cine footage and run real workers; they do not substitute match outputs.

The independent UI reviewer selected eight criteria, recorded defects and recommendations, and reran affected workflows after fixes. Its separate final scorecard records grades, screenshots and remaining validation limits.

A larger search area cannot resolve ambiguous geometry: for a straight paper edge, select the region center and use Vertical only if the real motion is known to be vertical. This is an explicit motion constraint. Tumbling measures the projected silhouette centroid, not the physical center of mass.

The Windows archive is assembled from the official embedded CPython 3.13.17 x64 distribution, Windows x64 dependency wheels, and the updated source. Its manifest records hashes and native binary architecture checks. The archive is checked for extraction integrity and agreement with release source before delivery.

**Not tested here:** executing this new package on Windows 11, Windows-native fonts/DPI behavior, or tracking the oscillating-paper Cine shown in the photographs. That footage was not available as a file. The installation check and a representative Cine/job/export on the work PC remain necessary before a customer demonstration. Existing .pcajob jobs and their referenced footage should be kept outside the application folder.
