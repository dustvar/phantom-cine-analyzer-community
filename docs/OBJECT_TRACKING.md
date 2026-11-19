# Object Tracking — v1.10.0

PCA now offers three automatic tracking choices in **Track → Setup**. All setup
parameters are accessible in the scrolling panel before selecting an object;
right-click is optional. **Results** contains the existing point views, object
list and measured coordinates. Manual tracking remains available. Press **?** for
**How to use** and **How it works**.

## Choosing and configuring a method

| Method | Reports | Useful when |
| --- | --- | --- |
| Classic | Intensity-pattern point coordinates | The selected feature keeps a similar appearance |
| Edge Assist | Selected point and in-plane rigid rotation | Edges/texture remain attached to a rigid object |
| Tumbling | Projected silhouette area centroid | A rotating/tumbling object changes its visible outline |

Classic retains normalized intensity-template correlation and the original template-update
confidence rule. Version 1.14.2 searches at native resolution and refines only
candidate neighborhoods at the selected subpixel spacing. It caches the unchanged
reference patch instead of rereading the setup frame on each step. Source pixel-center
coordinates remove the previous oversampling half-pixel offset; existing saved
measurements are not rewritten. Newly processed results can differ from older runs.
Edge Assist's matching/refinement primitives have moved into `edge_tracking.py`;
the PCA integration still provides its existing Smart Range traversal, candidate
archive, threshold filtering, calibration and graph behavior.

Choose a clear setup frame, select a method, set its region size, and click an
interior object pixel. For Edge Assist the click is the exact reported point;
its adjustable reinforcement region can cover a different rigid feature. For
Tumbling the box must contain the whole object and a small margin of background.
Move/resize it and use **Preview mask** to inspect segmentation. Choose a new
object to set a different foreground seed. The green outline identifies accepted
foreground; an available rejected candidate is shown in amber. A box that cuts
off foreground is rejected during setup.

The two frame bounds use the Cine's original frame numbers (including negative
pretrigger values). Tumbling requires its setup frame inside the range and
processes each direction with independent state. **Track object** replaces that
object's measurements. **Cancel** or Escape retains completed measurements.
**Setup frame** returns to the anchor. Changes to tumbling parameters mark the
results as needing a rerun; mask preview does not overwrite measured points.

For Tumbling, start with **Adaptive color / texture** on changing backgrounds.
It uses spatial color segmentation plus motion from textured features, not a
stationary-background subtraction model. Dark/Bright modes use a user-controlled
brightness threshold. Acceptance confidence and appearance-update confidence are
separate: the latter prevents uncertain frames from teaching the reference.
Appearance learning rate 0 freezes the appearance reference; geometry still
follows accepted masks. Motion, area-change and contour-growth bounds constrain
what a plausible object can do. Confidence is a heuristic, not a probability.

Save a **Job** to retain settings, accepted coordinates, compact mask contours,
rejection diagnostics, calibration and normal workspace state. Reopening does not
retrack. Original media is referenced, not embedded. No new job format is needed.

## Tumbling algorithm

1. Normalize source gray/RGB pixels to a fixed setup-frame intensity range. PCA
   supplies raw/debayered pixels; Viewer grades and playback/export edits are not
   measurements or tracking input.
2. Initialize foreground/background using an interior seed and enclosing region.
   Adaptive mode calls OpenCV GrabCut: color-mixture foreground/background models
   and graph-cut segmentation. Dark/Bright modes interpolate a cutoff from local
   background median toward the selected foreground level.
3. Predict location using accepted velocity and robust median Lucas–Kanade flow
   on foreground features. Forward/backward flow consistency excludes weak
   correspondences. Flow is a proposal, not the final measured coordinate.
4. Shift the prior mask into a bounded local crop. Its interior anchors foreground;
   the boundary may grow/shrink. Recompute segmentation and connected components.
5. Gate candidates by motion, area ratio, clipping, appearance and overlap. Rank
   plausible candidates using 0.35 motion + 0.20 area + 0.30 histogram similarity
   + 0.15 overlap. A competing candidate within 0.10 score is treated as ambiguous.
6. Accept only above the configured threshold. Rejected observations contain no
   centroid and do not update accepted geometry/motion/appearance. Repeated misses
   stop that direction. Histogram and foreground-brightness learning occurs only
   above both acceptance and update-confidence thresholds.
7. Compute `(mean(x pixels), mean(y pixels))` on the full accepted binary mask.
   Store those coordinates through PCA's existing calibration/trajectory code.
   Display contours are simplified; they are not used to calculate the centroid.

This is a **projected area centroid**, not a physical center of mass. Perspective,
self-occlusion, transparency, changing lighting and visible silhouette changes can
shift it independently of the physical mass center. No 3-D coordinates, hidden
surfaces or orientation are inferred. Angle is unavailable and displays as a dash.
Motion graphs use signed position differences; see COMPACT_WORKSPACE.md for the 1.11.0 correction and displacement references.

Adaptive segmentation is intended for textured/changing backgrounds but cannot
guarantee all such scenes. Similar colors, severe blur, rapid appearance changes,
similar crossing objects and occlusion remain difficult. Inspect masks, tune
bounds, and validate intended customer clips against manually labeled frames.
The new mode has synthetic-ground-truth coverage; customer footage has not yet
been supplied for validation of this mode.

## Independent engines

`edge_tracking.py` and `tumbling_tracker.py` import only Python's standard library,
NumPy and OpenCV. `autotrackalgorithms.py` and `classic_tracking.py` retain PCA Classic's NumPy/OpenCV/SciPy
implementation and a compatibility bridge to Edge Assist. `tumbling_pca_adapter.py`,
`tracking_setup_mixin.py`, `tracking_setup_ui.py` and `tracking_help.py` are PCA UI/
media/persistence integration and are not included with the engine handoff.

Run `scripts/export_tracking_engines.py /path/to/new/handoff` using the PCA Python
environment to export the two engines, examples, independent tests, dependency
notes, source hashes and original notices. The export preserves code provenance
and does not choose a new license for the project owners. See the handoff's
`DEPENDENCIES.md` for the focused manager-facing inventory.

The algorithms use OpenCV primitives, including
[GrabCut](https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html) and
[Lucas–Kanade optical flow](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html).
No trained weights, model downloads, neural framework or cloud service is used.

## Validation

- Existing Classic/Edge regression tests, including subpixel matches, rotation,
  Point Lock, Smart Range loss recovery and confidence filtering.
- Independent Edge API tests for translation, rotation, fixed point offset, blank
  frames and invalid initialization.
- Tumbling ground truth for changing outlines/rotation and textured changing
  backgrounds, bright/dark silhouettes, high-bit-depth data, reverse traversal,
  rejected/disappeared objects and setup validation.
- Connected Qt setup tests for all three methods, per-object settings before
  clicking, mask preview, actual worker execution, errors, cancellation, job data
  serialization and two-tab help.
- Native Qt previews at normal and smaller window sizes; complete application
  regression suite including whole-job reopening and existing Viewer workflows.
