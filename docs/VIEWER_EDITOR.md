# Viewer editor — 1.7.0

See [video sequences, zoom, and custom text](VIEWER_SEQUENCE.md) for Multiply
Video, Add New Video, timeline magnification, animated crops, and annotations.

## Fast approach, slow impact, fast aftermath

1. Load a Cine, select its pane, and open **Viewer → Speed & Effects**. Set In and
   Out to the source range you want. Click or drag the colored timeline to seek;
   the existing scrubber and frame-step controls also work.
2. Seek to the beginning of the impact and click **Split** in the Timeline card
   or **Split Here** in the editor (Command+B on
   macOS, Ctrl+B elsewhere). Seek to the end of the impact and split again.
   A split occurs immediately before the playhead frame. It does not remove
   footage or change the existing playback by itself.
3. Select each section in the table and set **Playback** FPS. At the default
   **Video FPS** of 30, use 120 / 30 / 120 playback FPS for a 4× approach, 1×
   impact, and 4× aftermath. Presets offer ¼×, 1×, 4×, and 10×.
4. Click the small curve icon between the first two slices. Choose **On left
   clip**, **On right clip**, or **Half on each side**, then set a duration such
   as 0.30 s. Choose an easing curve and click **Apply**. Repeat at the next cut.
5. Preview with Play or Space, then choose **Export Clip**. MP4 is a convenient
   sharing format. The export dialog shows the edited duration and frame count.

## Junction transitions

Every cut between included sections has a clickable transition icon. It appears
as soon as a section is split, even before a transition is added. Gold bands
above the slices show the source footage covered by active ramps. The section
panel's **Edit incoming transition…** button opens the same editor, including
when a cut is outside the visible In/Out range or tightly packed among others.

- **Cut at frame**: type a precise Cine frame number or move the linked slider.
  The number is the first frame of the right section, including negative
  pretrigger numbering. Both neighboring sections must retain at least one frame.
- **On left clip**: complete the speed change before reaching the cut.
- **On right clip**: start the speed change at the cut.
- **Half on each side**: half the transition's finished-video time before the
  cut and half after it. Because speeds differ, the source-frame counts on the
  two sides will generally differ.
- **Duration**: seconds in the finished video; zero gives an immediate cut.
- **Speed curve**: smooth (ease in and out), linear, ease in, or ease out.
- **Left playback / Right playback**: adjust the adjacent sections' FPS directly.
- **Remove Transition**: remove the ramp while retaining both sections.

The curve preview marks the cut with a dashed line and shows the time on each
side. Apply records one undo action; Cancel leaves the edit unchanged. When
neighboring transitions request more footage than a section contains, their
lengths are reduced proportionally to fit. The dialog shows the actual fitted
duration. The first included section has no incoming transition. Trimming into
an existing ramp preserves its speed at the trim point.

Drag a junction icon horizontally on the source timeline for a quick cut move.
The boundary and its frame label update as you drag; release commits one edit.
Press Escape before releasing to cancel. **Undo / Redo** sit immediately to the
right of the timeline and also remain available in the editor panel. A cut move
redistributes source frames between the two adjoining sections without deleting
footage, changing their rates, or moving another cut. If a skipped section lies
before the cut, that section's boundary moves while its skipped state is retained.

Each loaded Cine has its own sections, data boxes, and undo history. Switching or
hiding panes preserves them. Multiply Video and Add New Video extend that Cine's
Viewer sequence with independently editable copies and additional sources.

## Data & Text

**Data & Text** opens the **Data** tab with **Playback Data** and **Camera Data** selectors.
Selecting one enables it and shows its independent controls. **Enable** turns
the selected box on or off; both boxes can be visible together.

**Playback Data** defaults to the bottom right, showing slowdown above playback
FPS. Capture FPS is available but unchecked by default:

```text
3,133× slower
30 fps playback
```

**Camera Data** defaults to the bottom left, with model, capture FPS, and source
resolution all checked. For a Cine recorded on a TMX 7510:

```text
Phantom TMX 7510
94,000 fps
1,024 × 640 px
```

All camera details come from the selected Cine; a missing model says **Model not
stored**. Resolution describes the source recording, even when export is resized.

Both boxes default to **Stacked**, with the first line largest, the next smaller,
and the third smallest. **Straight** puts the readouts on one line with the same
size hierarchy. Each field can be shown or hidden independently; turning all
fields off removes that box. Settings for one box do not change the other.

**Show one decimal place** is off by default. Readouts round to whole numbers,
so 94,000 fps footage played at 30 fps shows **3,133× slower**. Enabling decimals
shows **3,133.3× slower**, **94,000.0 fps**, and **30.0 fps playback**. Display
rounding never changes the underlying timing. Playback below 1 fps displays
**<1 fps playback** when decimals are hidden.

For 1,000 fps footage, 10 fps playback reads **100× slower** and 5 fps reads
**200× slower**. Ramps update both dynamic readouts continuously. Layout and
number formatting are retained per Cine, support undo/redo, and are included
in saved edit recipes and exported videos. Older recipes adopt Stacked with
decimals off while retaining their selected data fields and timing.

**Capture FPS** comes from the Cine metadata. **Playback FPS** counts recorded
source frames consumed per second of finished video. The slowdown is capture
FPS divided by playback FPS. If the Cine stores only every Nth recorded frame,
the slowdown additionally accounts for that decimation while retaining the raw
capture FPS label. Missing capture metadata is labeled unavailable. Playback at
capture speed says **Real time**; playback faster than capture says **× faster**.

**Video FPS** in the section editor remains the fixed output-file cadence.
Changing it does not change source frames consumed per second, so it does not
change the real-world slowdown readout. For a 1,000 fps Cine:

| Playback FPS | Slower than reality | Output Video FPS |
| --- | --- | --- |
| 30 | 33× | 30 or 60 |
| 10 | 100× | 30 or 60 |
| 5 | 200× | 30 or 60 |

Drag either box on the video or use its horizontal/vertical controls.
**Main text size** is a percentage of image height; supporting
text scales with it. Choose white/gold/cyan and an optional translucent dark
background. The box fits within the image and shrinks for small resolutions.
Closing the panel leaves enabled boxes visible; uncheck **Enable** to
remove the selected box. The same compact graphics are included in exports.

Fast sections skip source frames and slow sections repeat frames as needed.
Ramps vary that sampling rate smoothly; no optical-flow interpolation is added.
Preview and export share the same time map and output-frame grid, including the
last frame. A busy computer can skip preview refreshes while maintaining the
intended timing. Export writes every output frame. The final duration rounds up
to a whole output frame. Analysis images and measurements remain unchanged.

## Other editing controls

- **Include this section**: uncheck to skip footage during playback and export.
  The source section stays in the table and timeline so it can be restored.
- **Merge Next**: joins the selected section to the following section, retaining
  the selected section's speed, incoming transition, and inclusion setting.
- **Undo / Redo**: up to 50 timeline/data-box actions per Cine, including a single
  undo step for each cut or data-box drag. This history does not cover levels or In/Out.
- **Loop edited preview**: repeats the included In/Out range in the preview;
  exports contain one pass. Reverse and 4× transport remain preview controls
  and do not change the saved section rates or overlay's intended export speed.
- **Reset Timeline**: returns to one section at the current Video FPS while
  retaining both data boxes and loop choices. Reset is undoable.
- **Save Edit… / Load Edit…**: stores a `.pcaedit` recipe containing the timeline,
  both data boxes, loop option, In/Out, Levels & Curves, and existing image adjustments.
  Open the matching Cine before loading its recipe. Size and a header fingerprint
  prevent applying a recipe to unrelated footage. The recipe contains no media.
  Loading is explicit; edits are not automatically restored after closing PCA.
  Older version 1.4 recipes retain their original right-side smooth transitions
  and adopt the new selectable data fields. Loading does not rewrite the recipe.

Exports are 8-bit sharing copies with current image adjustments and overlay.
The source Cine is not changed. Encoding remains cancellable, and an existing
destination is replaced only after encoding succeeds.

## Implementation and validation

`viewer_timeline.py` owns edit schema v3, migrates v1/v2 recipes, and integrates
speed curves in output time. It allocates source frames around each junction,
fits neighboring ramps, and preserves physical frame boundaries across skipped
sections. `viewer_transition_ui.py` provides the dedicated junction editor.
`viewer_editor_mixin.py` connects native controls to per-Cine state, wall-clock
playback, save/load, and overlays. `viewer_overlay.py` shares the compact text
rendering between preview and export; `video_export.py` streams frames to FFmpeg
using a separate Cine reader.

Validation on September 14, 2026 includes 158 unit and connected Qt tests:
left/right/center placement, easing curves, short and competing transitions,
trims, skipped sections, clickable junction controls, Apply/Cancel/Remove and
undo, selectable data fields, stacked/straight layouts, optional decimals,
capture-relative slowdown, independent camera/playback data settings and drags,
cut sliders and typed frames, cut dragging and cancellation, one-step undo/redo,
source partition preservation, recipe migration,
quantized playback, export timing and image content, and the existing metadata,
calibration, workspace, and tracking regressions. A separate deterministic
500-case timing check covers mixed rates, curve types, placements, and skips.

A real 116-frame Tesla-coil Cine was edited through the native controls into
90 / 15 / 75 playback-FPS sections, with a centered 0.25-second smooth transition
and a left-side 0.35-second ease-out transition. Saving and reloading restored
its settings. Export produced 98 decoded frames at 30 fps (3.267 seconds).
Sampled image and data-box content matched the expected render within normal
H.264 compression error. The controls were rendered and checked at 1440×960
and 1024×720.

Version 1.5.1 additionally exercised the actual Include Data controls and Export
button with the real 94,000 fps Cine: Stacked, Straight, and decimals-on variants
each exported six frames at 30 fps. Decoded content matched the shared preview
renderer within H.264 compression error. Changing layout or decimal formatting
did not change the sampled source frames or duration.

Version 1.6.0 verified both data boxes against the real Cine's Phantom TMX 7510,
94,000 fps, and 1024×640 metadata. Native controls exercised separate box drags,
typed/slider cut moves, timeline-handle dragging, Undo/Redo, and recipe save/load.
The edited Cine exported 130 frames at 30 fps (4.333 seconds); decoded frames
and both text regions matched the shared renderer within H.264 compression
error. The updated dialog, timeline buttons, and data panels were checked at
1440×960 and 1024×720 with no Qt signal errors.
