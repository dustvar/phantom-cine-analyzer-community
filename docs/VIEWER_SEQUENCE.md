# Video sequences, zoom, and custom text — 1.7.0

## Repeat footage or add another video

Open a Cine in Viewer. **Multiply Video** and **Add New Video** sit side by side
above the colored timeline.

- **Multiply Video** immediately appends one fresh copy of the selected video.
  It does not ask for a count. Use it again to append another copy.
- **Add New Video** opens a file picker for Cine, MP4, MOV, AVI, M4V, or MKV.
  A readable file is appended after the existing videos; a failed or cancelled
  import leaves the sequence unchanged.
- The video selector beside these buttons jumps between videos. **V1**, **V2**,
  etc. identify them in the timeline and frame labels. Frame numbers retain
  each source's numbering, including negative Cine pretrigger frames.
- A fresh copy contains the complete original source. Existing cuts, speeds,
  zooms, levels, data-box settings, and custom text are not copied. A Cine starts
  at 30 playback fps; an ordinary video starts at its recorded playback rate
  (within the editor's supported range).
- Earlier videos keep their edits. An existing In/Out selection is retained
  when appending; trimmed footage is shaded in the timeline. **Reset Clip**
  restores the sequence's full ranges, including trims retained by appending.

Each video has independent levels, Playback Data, Camera Data, and custom text.
Select a segment to edit its playback speed and zoom. Changes apply only to the
selected video or segment. The original Cine remains the analysis source;
the longer Viewer sequence never changes tracking frame indices or measurements.

## Distinct joins and timeline zoom

**Cyan chain links** mark the join between separate videos. Ordinary curve
icons mark cuts within a video. Click either to edit the speed transition:
left/right/half-on-each-side placement, duration, easing, and adjacent playback
rates are available at video joins too.

A video join stays at the boundary between the sources. Its cut-frame input
is disabled because it connects one video's end to the next video's start.
Ordinary cuts remain draggable and editable by frame number or slider, and
cannot cross a video join. Merge Next also stops at a join.

Use **Timeline − / +** to change how much timeline fits on screen. Drag its
horizontal scrollbar to move through a magnified timeline. **Fit** shows the
whole selected range. Playback follows the playhead when it leaves the visible
part of a magnified timeline. Timeline zoom changes only the editor's view.

**Undo / Redo** beside the timeline cover added videos, cut moves, speed changes,
zoom edits, and text/data edits. A crop-frame or text drag is one undo action.
Reset Timeline returns to a single original video and is undoable.

## Zoom the finished video

1. Split the footage where the zoom should start and select that segment.
2. Open the **Zoom** tab. Choose **1×**, **2×**, **3×**, or a custom factor up to
   20×. A yellow rectangle shows the target crop on the full source image.
3. Drag inside the rectangle to move it. Drag a corner to resize it. The crop
   retains the source aspect ratio, stays inside the image, and matches the
   numeric zoom and center controls.
4. Set **Zoom duration**: zero is instant; 0.50 s takes half a finished-video
   second. Choose Smooth, Linear, Ease in, or Ease out motion.
5. Turn off **Adjust yellow frame**, or press Play, to preview the animation.
   The yellow guide is never exported.
6. Split again and choose **1×** to zoom out, with an optional duration.

The duration is shortened if the next explicit zoom leaves insufficient time.
A plain split preserves an existing zoom animation. Editing the new segment's
zoom creates a new target at that cut. Zoom is a crop enlarged to the output
size; it does not create additional image detail.

## Text during a zoom

Playback Data, Camera Data, and each custom text box have **Keep on screen
during zoom**, enabled by default.

- Checked: the box retains its on-screen size and placement as footage zooms.
- Unchecked: the box stays attached to the original image, enlarges with the
  footage, and can move out of frame.

Data boxes retain their independent Enable, fields, Stacked/Straight, decimals,
size, color, background, and position controls. Added videos read their own
camera metadata. Ordinary videos generally do not contain a camera model.

## Add a timed explanation

Open **Data & Text → Add Custom Text…**, or the **Text** tab, then **Add Text**.
Type the explanation, including line breaks if wanted. Set:

- **Start in this video**: seconds from this video's start in the sequence.
- **Duration**: how many finished-video seconds the text is shown.
- **Text size**, color, background, and horizontal/vertical position.
- **Keep on screen during zoom** for that individual text box.

**Go to text start** previews the text at its beginning. Drag visible text on
the image to position it. Enable hides it without deleting it; Remove deletes
the selected annotation and supports Undo. Multiple independent text boxes
can overlap in time. Timing is measured in playback seconds, so subsequent
speed edits can change which source frames appear underneath a timed caption.

## Save and export

Save Edit stores the sequence, source references, per-video appearance, segment
speeds/zooms, text, and selection in a `.pcaedit` recipe. Keep the additional
source files in place: recipes reference them and do not embed their media.
Loading checks source fingerprints and rejects missing or changed files before
altering the current edit. Earlier one-video recipes migrate automatically.

Export Clip writes the entire included sequence with its timing, animated crops,
and enabled text/data. Different source sizes fit the output without stretching;
black bars are added when aspect ratios differ. Output remains MP4/MOV H.264 or
AVI Motion JPEG. **Exports are silent; imported audio is not included.** Source
files are never modified. Exports remain cancellable and only replace an
existing destination after successful encoding.

## Implementation

The edit schema is version 3. Sections partition a virtual frame axis across
source copies; readers translate that axis into each video's frame indices.
`viewer_timeline.py` provides timing and migration. `viewer_effects.py` shares
animated crop and text rendering across preview/export. `viewer_media.py`
handles Cine and ordinary video readers. `viewer_sequence_mixin.py` keeps the
sequence separate from the analysis source. `viewer_extras_ui.py` supplies
timeline magnification, zoom controls, and timed text editing.

Validation on September 14, 2026 passed 177 unit and connected Qt tests, covering
source repetition, mixed source lengths, fresh appearance defaults, trim and
frame mapping, joins, crop interpolation and dragging, text timing/anchoring,
undo/redo, recipe validation, and the previous workspace/tracking regressions.
A real Cine, edited repeat, and the supplied MP4 exported together as 473 frames
at 30 fps (15.767 seconds). Sampled decoded frames matched the shared preview
renderer within H.264 compression error. Native controls and layout were checked
at 1440×960 and 1024×720 with no Qt signal errors.
