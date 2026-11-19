# Viewer tools and Cine metadata — 1.3.0

Version 1.4 adds [section editing, speed ramps, saved edits, and an FPS
visualizer](VIEWER_EDITOR.md). The metadata and levels behavior below still
applies; export timing now comes from the Viewer timeline.

## Using the tools

Select a Cine and open **Viewer → Levels & Curves**. Drag the three interior
curve handles up to brighten or down to darken shadows, midtones, or highlights.
Black point, white point, and midtones also have sliders and numeric inputs.
Auto Levels uses the current frame's first and 99th percentiles, then holds
those settings during playback. Reset restores the identity adjustment; Bypass
temporarily disables these Viewer adjustments. Settings stay with each loaded
Cine for the current workspace, including while its pane is hidden. They are
not saved automatically across closing/reloading a workspace. In version 1.4,
**Save Edit…** includes levels and image adjustments in a `.pcaedit` recipe.

The curve uses monotone cubic interpolation to avoid overshoot. Its lookup
table preserves 16-bit monochrome or 8-bit RGB output. The display and export
apply it after the existing Cine rendering pipeline, leaving tracking buffers,
calibration, and recorded measurement coordinates unchanged.

Click the ruler, click the first point, and move the cursor: a red guide follows
it. The guide disappears when the second point is clicked, before the scale
dialog opens. Enter the real distance between the two marks, such as 1 inch.

Set In and Out in Viewer, then choose **Export Clip**. Without speed edits, the
export includes both endpoint frames and every frame between them, from the
selected Cine only. Version 1.4 follows the section timeline: fast sections may
skip frames, slow sections may repeat them, and skipped sections are excluded.
Choose MP4/H.264, MOV/H.264, or AVI/Motion JPEG; quality; and original/1080/720
height. Set the output cadence (1–120 fps) under **Speed & Effects → Video FPS**.
Resizing preserves aspect ratio and never upscales.
Odd dimensions are padded by at most one pixel for codec compatibility.
The current image adjustments are included; measurement graphics are excluded.

Playback rate is independent of capture rate. For example, 30 selected frames
exported at 30 fps last one second, even if captured at 2,000 fps. MP4 and MOV
place their index at the beginning for easier streaming. No audio is generated.
These are 8-bit sharing copies; the source Cine is untouched.

Export runs in a worker with its own file reader and an immutable settings
snapshot. Frames are streamed to FFmpeg without loading the entire clip into
memory. Cancellation/failure removes only the temporary export. The destination
is replaced only after successful encoding; an existing file survives failure.

## Metadata repair

The bundled reader parsed the newest SETUP fields sequentially, even when an
older Cine's SETUP ended earlier. `cine_metadata.py` uses little-endian,
length-checked offsets and retains the existing memory-mapped pixel decoder.
The stable layout follows Vision Research's [Cine File Format, Appendix 4](https://phantomhighspeed.my.site.com/PhantomCommunity/servlet/fileField?entityId=ka01N000000vtRkQAI&field=File_Attachments__Body__s).
Optional later fields use the layout supported by the bundled reader and are
read only when the complete field fits within SETUP.Length.

For the inspected PCATest.cine, SETUP is 9,344 bytes. The valid frame-rate field
stores **2,000 fps**, corroborated by the frame timestamps. The model-name field
would start at offset 10,128 within SETUP, so it is absent. INFO reports
**Not stored in this Cine**, with hardware ID **12001** and serial **31851**.
The former huge frame rate and corrupt name came from unrelated tagged data.
The hardware ID is not treated as a marketed model name. A newer TMX file still
reports **Phantom TMX 7510**, **94,000 fps**, and its original image settings.
Trigger timestamps are now explicitly labeled UTC. Reusing a reader also
clears its cached timestamp origin.

## Validation (September 14, 2026)

- 103 unit and connected Qt tests, including binary fixtures for old/new SETUP,
  cursor-following calibration, per-Cine grades, all three video formats,
  inclusive ranges, single-frame export, cancellation, and failure cleanup.
- PCATest.cine exercised through the actual Export button and worker: frames
  10–39 produced exactly 30 decoded frames at 30 fps, 1280×960. Mean pixel error
  against the graded Viewer output was 0.96 on the 0–255 sharing scale.
- A real color TMX Cine exported 10 frames at 30 fps, scaled to 768×480; decoded
  RGB mean pixel error was 2.76. Capture metadata and image settings matched the
  previous reader for this newer file.
- Calibration line, corrected INFO, export options, levels, and the 1024×720
  scrollable layout were rendered and visually checked. No Qt signal exceptions
  occurred in the real-file workflow.

The environment pins imageio-ffmpeg 0.6.0. MP4/MOV use libx264 and `+faststart`;
see the [FFmpeg format documentation](https://ffmpeg.org/ffmpeg-formats.html).
