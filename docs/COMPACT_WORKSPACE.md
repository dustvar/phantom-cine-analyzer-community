# PCA 1.12 · Guided Tracking

Extract the entire Windows portable ZIP into a new folder. Keep your Cine/video footage
and .pcajob jobs before removing the old application folder. Run Check Installation.cmd,
then Start PCA.cmd. No previous application folder is needed.

## Viewing and editing

The video fills most of the workspace. Short editing actions are on the right of Viewer.
Show timeline reveals segments and trim controls; hide it again to enlarge the footage.
The small playback overlay has the PCC transport and playback FPS. Playback below the
image hides/reveals it. The seek bar stays below the video. Wheel zooms; drag empty space
when zoomed to pan. Tools at the left switches workflows. Video Overlay remains available.
Info, Adjust and Tracks appear only in the right-side rail; click again or Close to hide.
The empty plot stays hidden until measurements exist; Plot can show it explicitly.
Drag the horizontal divider between the image and plot to choose their relative heights.

## Add or edit a tracking object

1. Tools → Track → Add Object. Choose Classic, Edge Assist or Tumbling.
2. Target: click a distinctive point, or choose Draw region and drag a region whose center
   will be measured. Point selection is recommended but optional for Classic/Edge Assist.
3. Tracking region: fit the pink box using its handles or numeric size/angle. Choose tracked
   point can select a point inside or outside the box without moving it. Use region center
   measures its center. Tumbling measures the silhouette centroid; fit its green seed region
   and use Preview silhouette mask. More segmentation controls are under Settings.
4. Search: move, resize or rotate the yellow box. The pink box does not move with it. The
   yellow box expands on release if necessary to contain the pink box. Matching respects
   this geometry; Edge Assist carries it with subsequent fixture rotation. Vertical only /
   Horizontal only are useful when known motion follows one axis and a straight edge gives
   no information along its length. These are explicit assumptions, not measured evidence.
   Tumbling shows its adaptive motion/growth limits directly in this step.
5. Review: name, first/last frame, confidence, and displacement reference. Choose the first
   measured point, a source frame, or a fixed image pixel. For a fixed pixel, type X/Y or
   Pick reference pixel on the footage. This pixel never moves or tracks anything.
   Back/Next preserves these choices. The Frame button returns to the setup image; editing
   geometry also returns there so the changes stay visible.
6. Complete & Track. The visible progress strip includes Cancel tracking. Inspect the
   resulting trajectory and confidence in the Tracks drawer. Edit reopens the guide.

Cancel during setup removes a new draft or restores an existing object exactly. Cancel
while processing keeps completed measurements. Save Job after finishing or cancelling
setup; keep the original footage with the job. A ? button opens usage and technical help.

## Graphs, heat maps and acceleration

Path Settings contains heat-map value, including signed X/Y displacement. Lowest is green;
highest is red, relative to each object's range. Missing reference measurements stay gray.
For oscillation use signed Y-Speed / Y-Acceleration to distinguish direction; Speed and
Acceleration are magnitudes. A fixed displacement reference does not change velocity.

Acceleration amplifies position jitter. Derivatives below the plot offers Local fit or
Raw differences. Local fit defaults to a cubic over 21 measured samples and retains the
original points. Its window is adjustable from 5 to 101 samples. Larger windows smooth
more but can suppress brief peaks; compare with raw values and inspect the tracked path.
Actual capture times are used. Missing positions, invalid intervals and large timestamp
gaps are not bridged. No filter repairs an incorrect track or recovers physical motion
from an ambiguous straight edge. Tumbling's projected centroid is not a physical center
of mass and can shift as the silhouette changes.

## Validation scope

This package is assembled on macOS with Windows x64 binaries. Native Qt workflows and
numerical regression tests run locally; execution on Windows 11 still needs confirmation
on the work PC. The oscillating-paper footage was pictured but its Cine file was not available
for measurement validation. Existing jobs recalculate motion from saved tracked points.
