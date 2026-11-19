# Seeing fine structure in a BOS recording

Open **Tools → Background-Oriented Schlieren (BOS)**. Pin a still frame, then
draw a rectangle over the patterned background. Each preview or exported frame
is measured against this fixed reference.

## Tune first, then analyze a range

The main BOS view is a live settings preview. Its requested playback rate is a
target: reading a large Cine and measuring every displayed frame can take longer
than the time available at that rate. Use the achieved-rate readout to distinguish
the requested speed from the speed the preview is actually reaching.

For smooth review after tuning the effect:

1. Pin the no-flow reference, draw the analysis rectangle and adjust the method,
   sampling, full scale, opacity and other controls until the result is useful.
2. Click **Analyze range** at the lower left. Scrub to the desired start and use
   **Mark In**, then scrub to the end and use **Mark Out**. The frame fields allow
   exact endpoints. Both selected endpoints are included.
3. Start the analysis. PCA takes a snapshot of the current settings and processes
   every selected source frame in order. Progress and cancellation remain
   available while the work runs.
4. When analysis finishes, a separate result window opens. Its slider and playback
   rate control work on the already rendered frames; playing them does not repeat
   the BOS calculation. Choose a rate from 1 to 1,000 fps and check the achieved rate.
   The player reports buffering or late frames instead of implying a rate it has
   not achieved. Very high rates still depend on the display and computer.
5. Export an MP4 from that result window to keep a shareable rendered movie. This
   uses the analyzed result; it does not recalculate the original Cine.

The rendered frames are stored losslessly in a local disk cache, with a bounded
set of decoded frames prepared for playback. This temporary cache is removed
when its result window closes, so export an MP4 before closing if you want to
keep the movie. The Cine on the external drive is unchanged. Changes made to
the live preview afterward do not alter an existing result; analyze a new range
to include those changes. A saved job retains the editable setup, while the
exported movie retains the rendered presentation.

## Direction and overlay timing

Choose **Component → Horizontal + Vertical** to see the signed dot shift in the
image plane. The compass maps right to coral, down to gold, left to mint and up
to blue. Intermediate angles blend continuously. Color strength follows total
shift relative to **Full scale**. Magnitude shows only that total.

In live and analyzed playback, drag the speed slider to a whole FPS, click the
side arrows for a 10 fps change, or type an exact rate in the FPS box. Above 240 fps,
the live view advances to the frame due on the clock and shows its measured paint rate.
Direct **Export clip** uses a 60 fps MP4 at these speeds. For example, 1,000 source
frames at 1,000 fps become 60 output frames in one second. Record Studio lets you
choose the MP4 frame rate separately.
The directional compass starts on the footage for **Horizontal + Vertical** results.
Drag it to move it, drag a corner to resize it, or click **Compass** to hide it.
The compass follows the color-map track in playback and export. The dot-shift scale follows the arrows.

## Show Vector Field

Click **Show Vector Field** beneath **Method**. Choose **Arrows + map** to keep the
displacement colors, or **Arrows only** to put the arrows directly on the Cine.
**Density** chooses how many measured sites to display. **Extra Dense** targets twice the grid sites of **Dense**. The number of visible arrows also depends on available measurements and signal strength. Each arrow stays centered on its
measurement site. Its direction and length update as the apparent dot shift changes
relative to the pinned reference. Positive X points right and positive Y points down.

Compare lengths to the **Dot shift** key on the image. **Full scale** sets the
key’s value in source-image pixels. Open **Arrow style** to choose the arrow shape,
stroke width, colors, and how to show magnitude. The default uses a double head
above full scale. The two-color option switches colors at full scale. The
black-to-white gradient gets lighter with magnitude and reaches white at full
scale. For a 0.30 px threshold, set **Full scale** to **0.30 px**. Small shifts below the drawing threshold and unreliable
matches leave gaps. The visualization takes its components from the measured field after any
stationary-patch correction. Display smoothing applies to the color map.

Hover over an arrow for its horizontal shift, vertical shift and magnitude.
**Save vectors…** writes a CSV for the displayed frame. Each reliable measurement
site gets a row, including sites skipped by the drawing density or threshold.
The table records source coordinates, native Cine and reference frame numbers,
trigger time when available, match score, raw and displayed displacement, and
which samples were selected or length-capped for drawing. Values in the table
retain their measured magnitude above full scale. These are optical shifts.
Inferring gas velocity or force requires independent physical information.

Enable the field before **Analyze range** to include the arrows and scale key in
the prepared result. **Export MP4** uses that result. Record Studio’s
**Overlay** and **Vectors** tracks have independent cuts and fades. Select
**Vectors** to change the arrow appearance after analysis. Existing results from
before 1.14.9 store the combined pixels. Analyze the range again to get separate tracks.

## Record Studio

Open **Record Studio** in the analyzed player. The normal playback slider stays available.

- **Overlay duration** starts as one green block. Seek to a cut and click **✂ Split**.
  Pull the facing ends apart to reveal the original footage in the gap. Drag the
  middle of a block to move it. Select a block to set its exact Cine frames and
  **Fade in** or **Fade out** time in playback seconds.
- **Vectors** gives the arrows and their scale the same trim, split, move and fade
  controls. For example, leave **Overlay** full length, split **Vectors** at the
  event and remove the preceding vector block. Set **Fade in** on the remaining
  block to introduce the arrows over an already visible map.
- **Speed** lets you split the timeline and set a playback FPS for each section.
  This is the number of source frames that pass in one second of the finished movie.
- **Text → + Text…** adds a caption, frame count, playback or capture FPS, slowdown,
  or time from trigger. Drag the ends of the text block to choose when it appears.
- **Export MP4** uses these edits. The **MP4** selector sets the encoded frame
  rate. At 240 fps playback, 2,400 source frames take ten seconds. A 60 fps MP4
  represents that motion with 600 frames. Select 240 fps output if every source
  frame is needed. PCA marks 120 and 240 fps exports for full-speed playback in compatible Apple players. Other players may apply their own slow-motion setting.

Studio edits belong to the open result. Export the movie before closing the result window.

## Keep the stationary background steady

At a small **Full scale**, a tiny shared movement of the camera or background can
color almost the entire analysis rectangle. This is different from the local dot
distortion around a flame or gas plume. The display scale stays fixed throughout
the clip; a sudden color change is not an automatic contrast rescale.

The optional stationary-anchor correction estimates shared image translation
from a separate rectangle. Choose a textured part of the background that remains
outside the flame, plume, shockwave and moving foreground objects for the entire
range. Keep the correction off if there is no trustworthy stationary area.
An anchor containing the phenomenon can subtract real signal from the result.

This correction does not rewrite the source recording or make rejected dot
matches reliable. It is a translation correction, not a correction for camera
rotation, changing focus or perspective. Compare with correction disabled when
deciding whether a large-scale signal is physical. Reference averaging and the
overlay noise filter are separate controls; neither replaces a suitable anchor.

## Resolve finer structure

- **Method → Optical flow** uses pyramidal Lucas–Kanade to estimate how far a
  small group of dots moved from the pinned reference. A reverse match and a
  normalized correlation check reject unreliable estimates. This is the default.
- **Method → Cross-correlation** searches possible horizontal and vertical offsets
  and compares local dot patterns using zero-mean normalized cross-correlation.
  A fitted peak gives a displacement between whole pixels. Weak, repeated or
  out-of-range matches remain unpainted. Both methods work from the same fixed
  reference and report displacement in source pixels.
- **Reduce overlay noise** smooths the displayed vectors in quiet regions of each
  frame. The filter backs off around coherent motion and sharp displacement changes.
  It leaves neighborhoods near missing measurements alone. Hover to inspect the
  original measured vectors, or uncheck the box to compare an unfiltered overlay.
  The option is enabled for new analyses. Jobs from older versions open with it off.
- **Sampling → Fine detail** starts at a 17 × 17 pixel window, with patch centers
  four pixels apart. The smaller window averages over less of a narrow front.
  The closer centers sample the front more densely; they do not imply four-pixel
  optical resolution. Fine is slower and more sensitive to camera noise.
- **Standard** starts at a 31 × 31 window every 15 pixels. Use it to check
  whether a feature persists in a quieter map. **Correlation settings → Window**
  can refine either preset. Jobs from older releases retain their saved window and
  previous spacing.
- For a less noisy baseline, choose **Reference → Average → 4 or 8 frames**.
  PCA averages raw measurements starting at the pinned frame. Check that the
  entire displayed span precedes flow and that the camera and pattern did not move.
  Event frames are analyzed individually, with no time averaging.
- Start with **Horizontal (Δx)** and **Schlieren gray** for a front traveling
  across the image. A smaller **Full scale** makes weak shifts more visible, but
  also makes noise more visible. **Soften background dots** changes only how the
  source image looks beneath the measurements.

## Reading a gap

Hover over the patch. The readout distinguishes a valid shift close to zero
from an unreliable match. Inside a plume the dot pattern may be obscured.
A patch can also fail if it has too little texture, moves beyond the search
limit, or gives inconsistent forward and reverse estimates. The software
leaves the source image visible there. A narrow unpainted strip beside the
rectangle is normal: a complete measurement window and its search area have
to fit. At the boundary between valid and rejected samples, PCA also leaves
the original footage visible if too little of the surrounding grid is valid.

The displacement is reported in **source image pixels**. An apparent front width
also includes optical and measurement-window blur; it is not the physical shock
thickness. On a stationary background, small color variations can be measurement
noise. Look for a structure that travels coherently through several frames.
