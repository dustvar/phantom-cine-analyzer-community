# Phantom Cine Analyzer Additional Features

This repository is a source-level handoff of playback, multi-Cine, interface,
and tracking improvements made to Phantom Cine Analyzer (PCA) 1.1.18. It is a
community modification, not an official Vision Research or AMETEK release.

The original application remains subject to the included Non-Commercial Open
Source License. No Cine footage, packaged applications, installers, signing
keys, or private build credentials are included in this repository.

## What changed

- **Terminal workspace (1.15.0 beta).** Choose Cines and Camera Control are the two startup actions.
  The left rail puts Calibrate in its own Setup section above analysis and measurement.
  Info, Adjust and the tracking inspector open from the header and close with a second click.
  The Settings menu contains interface color and help.
- **Camera monitor (1.15.4 beta).** Each camera connection opens in **Linear**, which maps
  signal proportionally to display brightness. Click **Auto** to set the curve from the
  frame on screen. That curve holds until you change it or click Auto again. Use the
  adjacent menu for presets or to edit the curve. Reset returns to Linear.
  These controls affect the live monitor only. Raw Cine recordings retain their original pixels.
  Cycle the purple guides or inspect live and recorded
  frames with the mouse wheel, trackpad pinch and drag. The yellow navigator shows the
  visible area inside the full frame.
- **Camera discovery (1.15.3 beta).** Open **Camera Control** and plug in the camera.
  On a dedicated Mac adapter, **Ethernet setup → Set up camera network…** saves the
  standard Phantom networks once. Camera Control keeps searching while open and opens
  the live view when one compatible camera answers. Live preview has been verified on
  the KT-1610 over 10 Gb Ethernet. Its capture settings are read-only in this release.
- **Camera Control (1.15.1 beta).** The standalone Phantom C980 and VEO 1310S support live
  preview and state-dependent capture controls. The C980 rate menu includes its standard
  sensor windows. Exposure changes were verified on an armed C980 and restored after testing.
  The connected camera heading shows model, sensor type, installed RAM and serial number
  when reported. Ethernet shows the active link speed and the adapter's supported speed.
  Expand its details for Cine RAM, removable storage and addresses.
  Open the frame-rate menu and choose an output resolution under **Standard** or **Binned**
  for the desired fps. For example, at 100 fps a VEO 1310S with 2×2 binning can use the
  full sensor at 1280 × 960 standard or 640 × 480 binned. The exposure menu puts the
  longest exposure for the selected rate first. Choosing a preset updates the camera
  immediately. For an exact value, type it and press Enter or leave the field.
  The trigger slider shows time before the trigger in green and time after it in red,
  along with the total recording time. Drag the slider and release it to set the trigger
  position. PCA shows the values and frame capacity accepted by the camera.
  **Black reference (CSR)** is available on supported cameras.
  **Arm** starts buffering in RAM and **Trigger** captures the event. Use
  **Event Finder → Camera recordings…** to review a completed Cine. Camera Control
  communicates directly over Ethernet.

- **Event Finder (1.14.16).** Search a rectangle or a 4 × 4 brightness probe in a saved Cine or completed
  Phantom RAM recording. Choose an available camera from the list or enter its IP address.
  Compatible cameras on a directly connected Mac can transfer packed 12-bit frames
  over 10 Gb Ethernet with XIMG. Check every frame or request every Nth frame over a chosen range. Jump
  between ranked candidate intervals and inspect the original frames. See the
  [Event Finder guide](docs/EVENT_FINDER.md) for the USB-C Ethernet workflow.

- **BOS speed ramps (1.14.12).** Right-click a cut on the Speed track to ramp
  between playback rates over a duration in finished-video seconds. Choose a
  linear change or ease in and out. Preview and MP4 use the same timing.

- **BOS Record Studio (1.14.11).** Edit the overlay, vectors, speed and titles
  on stacked tracks with a shared movie-time ruler. Position titles and the
  displacement key directly on the picture. Camera captions can include the
  recorded model, exposure and resolution. See the [BOS guide](docs/BOS_DETAIL.md).

- **BOS range sampling (1.14.10).** Choose how many frames to analyze, with
  guidance based on the slowest planned playback rate and the desired output
  cadence. Record Studio keeps the source timing and frame numbers. Vector
  styling adds a half-pixel stroke and switchable outlines. See the
  [BOS guide](docs/BOS_DETAIL.md).

- **BOS Vector Studio (1.14.9).** Customize vector appearance, use an extra-dense
  grid, and edit the color map and arrows on separate tracks in analyzed clips.
  Record Studio supports cuts, fades and restyling the cached vectors before MP4
  export. Source playback rates extend to 1,000 fps. See the
  [BOS guide](docs/BOS_DETAIL.md).

- **Interactive tracking workspace (1.14.3).** The graph now supports zoom/pan,
  hover values, point and inclusive range deletion with Undo, and calibrated unit
  selection. Live object previews, ruler precision picking, Edge Assist accuracy
  presets, and analysis export with an optional graph are included. See
  [Tracking workspace guide](docs/TRACKING_WORKSPACE.md).
- **Precision and playback (1.14.2).** Guided point selection has its own live
  magnifier, image-pixel arrow nudges, Shift for 0.1-pixel steps and Enter to
  confirm. Classic matching refines locally instead of enlarging the entire
  search window and reuses decoded references. **Adjust → Rotate video** turns
  footage upright while keeping scientific measurements in sensor coordinates.
  Rendered exports include orientation; Original Cine preserves source pixels.

- **BOS measurement methods (1.14.1).** Choose optical flow or subpixel
  cross-correlation to measure dot displacement. Turn on **Reduce overlay noise**
  for a quieter display while retaining the underlying measured vectors. Saved jobs
  and video exports preserve the selected settings.

- **Viewer trimming and tracking fades (1.14.0).** Drag the white playhead to
  seek and the lower green brackets to mark the inclusive export range. **Original
  Cine** streams a contiguous selection from one supported v1 Cine, preserving camera
  data and timestamps. **Rendered Cine** saves edits, text and graphics as
  uncompressed 24-bit RGB on the output timeline. For a clean opening followed by
  particle trails, track the particles, seek to impact in Viewer, and choose
  **Tracking fade → Use playhead**. The fade duration is in playback seconds.
  Choose MP4 to share the result or save a job to keep editing the tracks.

- **BOS fine detail (1.13.1):** Fine detail uses 17 × 17 px dot patches on a
  4 px grid to resolve narrower fronts. Standard uses larger patches for a quieter
  map. Dot matching uses local brightness, so a bright plume no longer weakens
  visible dots across the frame. Hover over a gap to check why it was masked.
  For a less noisy reference, choose 4 or 8 consecutive still frames starting at
  the pin. Keep the entire reference span before flow begins. See [BOS sampling
  and coverage](docs/BOS_DETAIL.md).

- **Background-Oriented Schlieren (1.13.0):** open BOS under Tools, pin a stationary
  pre-flow frame, and drag a rectangle over the background dots. The overlay shows
  measured horizontal, vertical or total apparent movement in image pixels. Hover to
  read a patch, save a frame, or export an inclusive frame range as an MP4.

- **Responsive playback (1.12.1):** Play, reverse and fast-forward read ahead from
  the selected Cine while the controls stay responsive. Pause holds the last
  displayed frame. Scrubbing and single-frame stepping remain available for
  exact inspection, independent of preview speed.

- **Guided tracking (1.12.0):** a tall video-first viewer with side editor tools,
  optional timeline, floating playback, and a resizable video/plot divider. Add Object
  walks through target, ROI, oriented search and review. Independent geometry, optional
  region-center measurement, fixed displacement references and automatic zoomed panning.
  Local-fit derivatives reduce sensitivity to point jitter while preserving raw positions.
- **Compact workspace (1.11.0):** collapsed tool menu and details drawer, title-bar
  tracking tools, direct playback FPS, draggable search windows, and click-before-
  scroll editing. All trackers support frame or fixed-pixel displacement references,
  with X/Y heat maps and corrected signed-velocity calculations. See the
  [compact workspace guide](docs/COMPACT_WORKSPACE.md).

- **Three tracking methods:** visible Setup/Results for Classic, Edge Assist and
  adaptive Tumbling Objects, with a two-tab help guide. Edge Assist and Tumbling
  have portable NumPy/OpenCV-only engines; Classic remains PCA-specific. See
  [Object Tracking](docs/OBJECT_TRACKING.md) and the
  [standalone handoff guide](standalone_tracking/README.md).

- **Modern interface:** charcoal panels and restrained Phantom accents throughout
  the app and launcher. Viewer controls are grouped into **Playback**, **Timeline**,
  **Trim**, and **Finish**, with plain-language hints. See the
  [Viewer interface guide](docs/VIEWER_INTERFACE.md).
- **Camera Comparator** is retained in source and old job settings but hidden from
  current navigation. Its original implementation includes verified Phantom
  camera presets, physical FOV masks, linear spatial binning, lower capture-FPS
  simulation, phase control, synchronized comparisons, saved presets, and annotated
  MP4/MOV/AVI export. Source assumptions and accuracy limits are explicit. See
  [Camera Comparator](docs/CAMERA_COMPARATOR.md).
- A PCC-style seven-button transport in Viewer, Track, 2 Pt, 3 Pt, 2 Line, and
  Area modes, plus full-Cine first/last-frame buttons around the scrubber.
- A dedicated Viewer with clip bounds and up to four simultaneous Cine panes.
- Pane selection keeps the current analysis mode, selected details tab, frame,
  calibration, adjustments, and tracking data. Use **−** in a pane header to
  hide it; the eye tabs under **Hidden Cines** restore it. At least one Cine
  stays visible, and the remaining panes expand to fill the workspace.
- Calibration clicks preserve the image background through point selection,
  the scale dialog, and subsequent redraws. A red guide follows the cursor
  between the first and second calibration points.
- Cine metadata honors the file's SETUP length, preventing older files' tagged
  data from being misread as a camera model or high-precision frame rate.
- Viewer **Levels & Curves** provides a histogram, black/white points, midtones,
  a draggable curve, Auto Levels, reset, and bypass, independently per Cine.
- Viewer **Speed & Effects** splits the selected Cine into sections with individual
  playback FPS and junction transitions with left/right/center placement
  and easing controls. Drag a junction to move its cut or enter the frame in its
  dialog. Nearby Undo/Redo, reversible section skipping,
  merge, loop preview, and saved edit recipes support a simple editing workflow.
- **Multiply Video** appends one fresh copy; the adjacent **Add New Video** button
  appends a Cine or ordinary video. Cyan chain-link joins identify separate
  videos. Timeline − / +, a scrollbar, and Fit make long sequences easier to edit.
- Segment **Zoom** offers 1×/2×/3×/custom crops, a draggable/resizable yellow frame,
  and animated zoom-in/out durations. **Text** adds timed, draggable explanations.
  Data and text boxes can stay on screen or move with the zoomed footage.
- **Data & Text** adds two independently enabled, movable boxes. **Playback Data**
  defaults to slowdown and playback FPS at bottom right, with optional capture FPS.
  **Camera Data** defaults to the Cine's camera model, capture FPS, and resolution
  at bottom left. Each supports Stacked/Straight, size, color, and optional decimals.
  Playback readouts follow speed ramps; whole numbers are the default.
- Viewer **Export Clip** saves the edited In/Out range as MP4, MOV, or AVI,
  including the sequence, image adjustments, section speeds, ramps, zooms, and
  enabled data/text. Exports are silent, including added ordinary videos.
  Size, quality, progress, and cancellation are available. See
  [sequence editing](docs/VIEWER_SEQUENCE.md), [Viewer editor](docs/VIEWER_EDITOR.md),
  and [levels and metadata](docs/VIEWER_TOOLS.md).
- Reliable multi-object selection and autotrack-dialog behavior.
- The original **Intensity Tracking (Classic)** tracker remains available and
  now defaults to 1/10-pixel positional interpolation for new objects.
- The opt-in **Intensify Tracking + Edge Assist (Beta)** tracker uses an exact tracked point,
  a movable/rotatable reinforcement region, rotation search, edge visualization,
  smart range detection, dual-reference confidence, and a live post-processing
  confidence filter (0.90 by default).
- Checked tracking objects populate a named two-column point-preview grid. A
  live Fade Paths overlay can reduce path opacity near the current tracked
  points without changing stored measurements.
- Cursor-centered wheel zoom, a persistent right-side panel, and an adjustable
  accent color.

The existing graph calculations and presentation were retained. The only
graph-class additions are the explicitly requested multi-Cine overlay and
Angle/Angular Speed support; playback and dual-confidence code do not alter
those classes. See [the modification log](app-source/MODIFICATIONS.playback.txt)
for the complete feature list.

## Repository layout

- `app-source/modules/trackmeasure/` — Python/PySide Track & Measure module.
- `launcher-source/` — Electron launcher and packaging scripts.
- `tests/` — headless unit and UI regression tests.
- `docs/` — developer-oriented change maps and design notes.
- `environment.yml` — reproducible Conda environment for development.

## Development setup

Development requires access to the pyphantom helper packages listed in
`environment.yml`. The native build uses the versions in
`packaging/runtime-requirements.txt`.

The native Mac app is at 1.14.16. The Windows x64 packaging notes describe the
previously prepared portable build (1.14.3). See [startup and Windows portability](docs/STARTUP_AND_WINDOWS.md) for
runtime requirements, per-user settings, packaging and validation limits.

```sh
conda env create -f environment.yml
conda activate PCA
QT_QPA_PLATFORM=offscreen PYTHONPATH=app-source/modules/trackmeasure python -m unittest discover -s tests -v
```

To run Track & Measure directly:

```sh
python app-source/modules/trackmeasure/launch_app.py
```

Choose a Cine from the application. An optional command-line argument is a
JSON configuration file containing `{"cine_path": "/path/to/example.cine"}`.
Video export uses the FFmpeg binary bundled with `imageio-ffmpeg==0.6.0`;
existing environments need `python -m pip install imageio-ffmpeg==0.6.0`.

The launcher can be prepared with `npm install` from `launcher-source/`.
Packaging copies the local Track & Measure source into the app. An alternate
module repository can be supplied through `TRACKMEASURE_REPO_URL`; credentials
must not be committed in the URL or source tree.

## Reverting the confidence experiment

The tag `checkpoint-before-dual-confidence` identifies the fully working state
immediately before adjacent-plus-setup confidence was added. Developers can
inspect it without changing branches:

```sh
git diff checkpoint-before-dual-confidence..HEAD
```

See [Developer handoff](docs/DEVELOPER_HANDOFF.md),
[Playback controls](docs/PLAYBACK_CONTROLS.md), and
[Hybrid tracking](docs/HYBRID_TRACKING.md) for implementation details.

### Video Overlay and complete jobs

Use **Video Overlay** to blend two recordings, align their Cine triggers or custom
T0 frames, shift them by frames, and inspect a pixel-difference view. **Save Job**
now preserves complete tracking, measurement, calibration, Viewer and comparison
workspaces in `.pcajob` files; **Open Job** restores them without retracking.
Keep the original footage alongside your work. See [Video Overlay and jobs](docs/VIDEO_OVERLAY_AND_JOBS.md)
for synchronization details, relinking and the saved-state scope.
