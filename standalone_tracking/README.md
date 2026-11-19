# Independent object-tracking engines

Two image-array engines for integration into another application. Neither imports
PCA, PySide/Qt, Cine readers, plotting libraries, SciPy or scikit-image. The original
PCA Classic particle tracker is deliberately outside this bundle.

- `edge_tracking.py`: the existing Edge Assist geometry/intensity matcher and
  Point Lock, extracted from the PCA integration. `EdgeAssistTracker` adds an
  array-in/observation-out sequence API. Its default acceptance score is 0.40.
- `tumbling_tracker.py`: adaptive silhouette segmentation, motion prediction,
  confidence-gated appearance learning and projected-area centroid measurements.
  Default mode is adaptive segmentation; dark/bright threshold modes are optional.

Python 3.10+ is required; validation used Python 3.13, NumPy 2.2.0 and OpenCV
4.11.0. The algorithm modules need only NumPy and OpenCV. The PCA installation has
`opencv-contrib-python==4.11.0.86`; these modules use standard OpenCV functionality,
so a separate application can use `opencv-python-headless==4.11.0.86` instead.
Install only ONE OpenCV wheel variant in an environment; they all provide `cv2`.
The headless alternative should be validated in the receiving application's build.

In an isolated environment:

```sh
python -m pip install -r requirements.txt
python example.py --output trajectories.json
python -m unittest discover -s tests -v
```

The example synthesizes two sequences, runs both engines and writes their measured
trajectories. It does not require PCA, Cine files, a display, a video codec, a
network service or model downloads. Source media is supplied by the host app.

## Integration contract

```python
from tumbling_tracker import AdaptiveSilhouetteTracker, TrackerSettings
tracker = AdaptiveSilhouetteTracker(TrackerSettings(
    confidence=0.60, update_confidence=0.75, adaptation=0.20,
    max_motion=60, growth=15))
first = tracker.initialize(rgb_frame, (left, top, width, height),
                           seed=(inside_x, inside_y), frame=setup_frame)
result = tracker.step(next_rgb_frame, frame=setup_frame + 1)
if result.accepted:
    x, y = result.centroid
else:
    # Store the reason; do not add a coordinate or an interpolated measurement.
    rejected_frame = result.record()
```

```python
from edge_tracking import EdgeAssistTracker, EdgeSettings
tracker = EdgeAssistTracker(EdgeSettings(confidence=0.40))
tracker.initialize(rgb_frame, point=(x, y), region_size=(51, 51),
                   region_center=(fixture_x, fixture_y), frame=setup_frame)
result = tracker.step(next_rgb_frame, frame=setup_frame + 1)
if result.accepted:
    x, y = result.point
    in_plane_degrees = result.angle
```

Images are finite gray or **RGB**, not OpenCV's default BGR, with constant
resolution/channel layout. Pixel coordinates are `(x, y)`, x right and y down.
Frames increase or decrease monotonically; use one tracker per object **and per
direction**, initialized independently on the same setup frame. Call from a
worker thread owned by the host. Do not call a tracker instance concurrently.
Provide sequential source frames for best results; skipping frames increases
uncertainty. Capture timestamps belong to the host application and are needed
for physical velocities; playback FPS is not the measurement time base.

Tumbling returns the centroid of foreground pixels, not a physical center of
mass, rigid-body origin or 3-D position. It does not output orientation.
`TrackResult.mask` is a local binary mask; `origin` locates it in the full image.
`record()` saves compact diagnostic contours and quality components. Contours are
simplified for display; measurements use the full mask. Rejected results have no
centroid. Repeated misses set `stopped`; reinitialize on a clear frame to resume.
Edge results likewise omit coordinates when rejected and stop at their miss limit.

`EdgeAlgorithms` exposes lower-level matching and refinement methods used by the
PCA adapter. PCA retains its own frame traversal, Smart Range candidate archive,
post-run threshold filtering, calibration and plotting; those application
behaviors are not part of the standalone sequence wrapper.

## Accuracy and validation

These are custom pipelines built using NumPy/OpenCV primitives, not wholly
from-scratch replacements for those libraries. Tumbling uses GrabCut color
segmentation and Lucas–Kanade feature motion. It can adapt to changing outlines
and to tested textured/changing backgrounds, but similar foreground/background,
blur, shadows, occlusion, crossing similar objects, rapid surface-color changes
and clipping can cause rejection or incorrect segmentation. Inspect the setup
mask and validate against labeled examples from the intended camera/application.
Confidence is a heuristic, not a probability or a certified accuracy estimate.

The included tests use known synthetic geometry to check translation, rotation,
changing outlines, changing backgrounds, high bit depth, forward/reverse tracking
and loss behavior. Real customer footage has not been validated for this new mode.
See `TECHNICAL.md` and `DEPENDENCIES.md` for the algorithm and dependency details.

## Source maintenance and rights

The engine files in this handoff are copies of the files used by the PCA adapter.
Regenerate the handoff using `scripts/export_tracking_engines.py`; do not maintain
divergent copies. `manifest.json` identifies the exported source hashes.

Technical extraction does not assign a new license to project code. The original
repository license is retained as `PROJECT_ORIGIN_LICENSE`; the code owners can
choose the agreed terms for its separate integration. Third-party notices are
listed separately. This bundle contains no PCA Classic implementation or GUI.
