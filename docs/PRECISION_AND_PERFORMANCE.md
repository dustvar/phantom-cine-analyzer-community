# PCA 1.14.2 — point picking, orientation and performance

## Compact controls

Tools, calibration and Choose Cines now share the top toolbar. Open contains
Open Job, and Save contains Save Job and Save As. The right-side Tracks control
is for tracked-object results and settings in the tracking workspace; Add Object
starts a new track. Info and Adjust remain available without leaving the video.
Both platform packages use the purple PCA impossible-triangle icon with a
transparent background.

## Multiple Cines together

Choose up to four Cines. Use Layout beside the Cine count to select Auto, Grid,
Side by side or Stacked. Each pane's Move menu changes its position without
changing the Cine identity, tracking, or timing. Zoom with that pane's + / − / Fit
controls or mouse wheel, and drag a zoomed image to pan it independently.
The layout, pane order and individual views are saved with the job.

Sync visible aligns the visible original recordings by capture time relative to
their own trigger T0, including recordings with different capture frame rates.
The active Cine controls the common time. Images outside another Cine's recorded
time range or across missing captures are marked unavailable instead of silently
holding its last frame. When capture timestamp tags are absent, timing is estimated
from the saved frame numbers, camera frame rate and decimation, and identified as
an estimate. Turn Sync visible off to preview an individual edited timeline with
its speed changes, text and framing effects. Overlay remains a separate tool for
superimposing two recordings.

## Selecting a point

Use Add Object and select a tracking method. A magnified inset now stays visible
while choosing the target or a fixed reference, even with the graph panel closed.
Move the mouse over the feature, then use the arrow keys to move the crosshair by
one image pixel. Hold Shift for 0.1-pixel steps. Enter confirms the candidate.
The arrow directions follow the screen even after rotating the image.
Tumbling Objects still measures the silhouette centroid; its initial click selects
the object, rather than changing the centroid into a tracked texture point.

## Rotate an upside-down recording

Open Adjust on the right. Rotate video is the first control, with left/right turns,
a 180-degree choice and reset. Each Cine retains its own orientation in the job.
Viewer Undo/Redo also preserves this edit. Added timeline videos can have their
own orientation. Text and data boxes remain upright on the rendered canvas.

Measurements and fixed references retain original sensor X/Y coordinates. Original
Cine export preserves the camera pixels and orientation. MP4/MOV/AVI and Rendered
Cine export include the rotation; Rendered Cine stores display RGB, not raw sensor data.

## First playback from an external drive

PCA streams a Cine instead of loading a potentially 100 GB recording into RAM.
A first read from an external drive can be much slower than replay after Windows
has cached the same footage. A subsequent smooth replay is consistent with this
effect, but does not identify the drive's throughput by itself.

Reading and display conversion now overlap in a bounded lookahead buffer. The
playback status shows actual displayed FPS separately from the requested rate.
Hover over it to inspect recent read/decode and image-processing times. The
original frames remain available for exact stepping, tracking and export.

Classic tracking no longer jumps back to reread the same reference frame at every
step. It locates candidate matches at native resolution and refines small regions
on the chosen subpixel grid. The source data and recorded capture timing remain
unchanged. Results are checked on controlled trajectories; a chosen interpolation
spacing is not a guarantee of physical measurement accuracy.

BOS reuses normalization and matching preparation of the fixed reference. That
cache is replaced when the pin, reference averaging or matching settings change.
The optimized and uncached measurements match exactly in regression tests.

Edge Assist prepares only the image patch needed for matching and reuses rotated
templates. Tumbling Objects avoids unnecessary full-frame color conversions and
mask scans. Video Overlay reuses synchronized timestamp arrays and keeps
monochrome calculations in one channel until display. Tracking graphs update
their playhead without redrawing the whole plot; hidden graphs refresh when
reopened. Path drawing batches trajectory segments while preserving heat maps
and editable-region selection.

Controlled Mac checks measured a 600-frame Classic pass at 11.64 seconds,
Tumbling at 19.02 to 11.54 seconds, and a 120-frame Edge pass at 11.00 to 10.17
seconds. Overlay composition for two cached 1280-by-800 monochrome streams
fell from 12.54 to 2.19 ms. These are test cases, not predictions for a particular
Windows PC, search region, recording or external drive. The Edge, Tumbling,
Overlay and BOS optimized paths matched their prior outputs in these checks.
Classic uses a changed matching implementation and additionally fixes a previous
half-pixel grid bias; its results were checked against known image motion.

If sustained external-drive bandwidth remains below the requested playback rate,
use a short Original Cine trim on the computer's local SSD for interactive work.
An application optimization cannot make a slow disk deliver frames faster than
its available bandwidth.

## Windows installation

Extract the complete portable package into a local folder such as
`%USERPROFILE%\PCA`, outside a OneDrive-backed Desktop. Run Check Installation.cmd,
then Start PCA.cmd. Create PCA Desktop Icon.cmd gives you a Desktop shortcut
without placing all the application files in a synchronized Desktop folder.
Copy saved jobs and source footage separately, and relink moved media when asked.
An app-only update is also provided for an existing 1.14.1 portable installation.
It retains the already-installed runtime and avoids extracting all libraries again.
Follow its included instructions; use the full portable package for a new install.
