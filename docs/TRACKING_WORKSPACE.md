# PCA 1.14.3 — tracking workspace

## Inspect the graph

Scroll over the graph to zoom around the pointer. Click and drag empty graph space
to pan; both axes update their scales. The −, Fit and + controls at the upper left
zoom out, show all plotted data or zoom in. Horizontal grid lines help compare
values with the left axis. The green vertical guide follows the current video
time; the horizontal guide marks the active object's available value.

Hover a plotted point to enlarge it and read its X/time, Y/value and source frame.
Click to select it. With the graph focused, Left and Right move through that line's
samples and seek the video. Delete removes the selected source measurement, then
recalculates derived data. For speed or acceleration, a plotted sample corresponds
to a source frame near the derivative's time; the readout identifies that frame.

Choose Delete range, pick the first point and then the last point on the same
line, and press Confirm. Both endpoints are included. Pan and zoom to select the
intended samples precisely. Cancel leaves the measurements untouched. Undo restores
the last deletion when the affected tracking data has not subsequently changed.
Deleting measurements also removes their stored candidates and associated metadata,
so changing an acceptance threshold cannot bring explicitly deleted points back.

## Calibrate and choose units

Click the ruler. The magnified inset follows the candidate calibration point.
Arrows move one sensor pixel; Shift plus an arrow moves 0.1 pixel. Enter confirms.
The red line connects the first calibration point to the second candidate.
Enter the known distance and a recognized physical unit after the second point.

The unit selector beside the graph metric becomes available after physical
calibration. An inch calibration can display measurements in centimetres,
millimetres or another supported length unit without changing the stored sensor
coordinates. Speed and acceleration use the corresponding distance unit per
current time unit or time unit squared. Angle remains angular data.

New Cines start in pixels; they do not silently inherit another recording's scale.
Saved jobs retain their per-Cine calibrations and the graph display-unit choice.
Calibrate each Cine before comparing physical measurements from different views.

## Choose and set up tracking

The colored Add Object button is first in the tracking toolbar. Classic, Edge
Assist and Tumbling use equally sized method choices. Close the chooser with X
or Escape to leave without creating an object.

Edge Assist offers Normal, Tight and Extreme presets before setup. Normal uses a
coarser search to reduce work; Tight and Extreme refine position and angle more
finely and take longer. The chosen parameters remain visible and editable in
setup. Subpixel spacing is a numerical search resolution, not a guarantee of
physical accuracy. Texture, motion blur, image noise and the selected region still
determine whether a track is reliable. Review the resulting path and confidence.

Idle automatic-tracking clicks no longer add or replace measurements. Use Add
Object or Edit to change an automatic target. A zoomed Cine can still be dragged
to pan; the separate pan-mode button has been removed. The small pane-hide button
is omitted from the tracking header; with several Cines, Hide this Cine remains
available in a pane's move menu.

## Live previews and path appearance

Open Tracks to see the object close-ups. Visible Point Views update from the same
displayed video frame during playback. Close-ups are cropped before grading to
avoid repeatedly processing a whole Cine for each small preview.

Path Settings opens the appearance controls. Apply transparency is independent
of the panel being open, so closing it retains the effect. Heat Map and transparency
can be used together while zooming. A radius of zero applies transparency to the
whole path; a positive radius limits it to points near current tracked positions.
Heat-map color represents the selected metric; transparency represents visibility,
not a different measurement.

## Export an analysis video

Open Save → Export Tracking Analysis after completing tracking. Choose Video with
tracking graphics, or Video with tracking graphics and graph. Set the inclusive
source-frame range, playback FPS and output size, then choose MP4 or Rendered Cine.
The graph guide follows the exported frame. Export uses a snapshot of the enabled
objects and appearance settings and runs in the background with a Cancel control.

MP4 is a normal, widely used video format and does not need an audio track.
Rendered Cine stores the displayed image with graphics baked into its pixels; it
does not retain the original raw sensor values for those pixels. The source Cine
is left unchanged. Keep the source recording and a saved .pcajob when measurement
data or later editing will be needed. Use Save Job to retain editable analysis;
use Export Tracking Analysis to create a presentation video.

## Windows update

An existing working PCA Windows Portable 1.14.1 or 1.14.2 installation can use the
small 1.14.3 update. Keep its runtime folder. Follow UPDATE - START HERE.txt to back
up the old app folder and replace the application files. A new installation needs
the full portable ZIP. Run Check Installation.cmd, then Start PCA.cmd; use Create
PCA Desktop Icon.cmd for the approved purple triangle icon on the Desktop.
