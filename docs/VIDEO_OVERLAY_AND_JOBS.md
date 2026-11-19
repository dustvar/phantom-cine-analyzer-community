# Video Overlay and whole-job files

The upgraded application adds **Video Overlay** below Camera Comparator and a
persistent **Open Job / Save Job / Save As** bar. The original application and
source recordings are unchanged.

## Overlay two test runs

1. Open Video Overlay. The first two workspace Cines are selected automatically;
   either Browse button can choose another Cine or an ordinary video.
2. Both images start at 50% opacity. Adjust each contribution, or choose
   **Difference** for absolute pixel differences. Images are centered at native
   pixel size; horizontal/vertical controls translate Video B without rescaling.
3. Cine T0 defaults to that file's trigger. Recordings can have different dates:
   each frame's capture timestamp is measured relative to its own trigger.
4. A positive **Time offset** moves that stream later by the specified number of
   its stored frame intervals; a negative value moves it earlier. For example,
   B +2 means A frame 10 pairs with B frame 8 when rates and frame origins match.
5. Uncheck **Use Cine trigger as T0** to enter a recorded frame number, or choose
   **Set displayed frame as T0**. Set matching physical events to T0 in both
   videos to align events instead of their hardware triggers. **Reset alignment**
   restores trigger alignment, zero offsets and centered images.
6. Scrub, step or play the common timeline. Replay FPS changes viewing speed,
   independently of each video's capture rate. Save Job preserves this setup.

The common timeline spans the union of both recordings at A's stored cadence.
The nearest recorded frame is chosen in each stream; the readout shows original
frame labels, common time and each selection's timing error in microseconds.
No optical-flow interpolation is used. Missing frames and areas outside the
recording remain blank. Difference is blank when either capture is unavailable.
Different frame rates may repeat or skip frames. Preview playback follows elapsed
time and may skip preview updates on a slow machine; stepping remains exact.

TIME64 integer components are subtracted before conversion to seconds, preserving
sub-microsecond precision. Without a timestamp tag, the overlay explicitly labels
its estimate from FirstImageNo, capture FPS and saved decimation. Ordinary video
has no recorded Cine trigger and defaults to its first frame as T0. Frame offsets
use the median recorded interval; they are an approximation for irregular capture
cadence. Native pixel alignment does not account for different lenses, sensor
sizes, camera poses or lens distortion. The tool is a preview comparison; it does
not write a combined video or change analysis/calibration on either source.

## Save and reopen a whole job

**Save Job** (Cmd/Ctrl-S), **Save As** (Cmd/Ctrl-Shift-S) and **Open Job**
(Cmd/Ctrl-O) use `.pcajob` files. Choose Cine footage with Cmd/Ctrl-Shift-O.
On the installed upgraded Mac app, double-clicking
a job opens it in a new Track & Measure window, including when the launcher is
already running. The launch script also accepts a job path directly.

The archive stores:

- All loaded analysis Cines (up to four), selected and hidden panes, playheads,
  clip ranges, calibration and units, image adjustments, active object and tool.
- Tracked coordinates, timestamps, scores, rotation angles, templates, Hybrid
  candidate/confidence data, names, colors, object settings and measurement tables.
- Viewer sequence edits, added-video references, cuts, ramps, levels, zoom effects,
  playback/camera data boxes and custom text.
- Main graph selection, axis limits and annotations; image pan/zoom, path/heat-map
  choices, preview order, timeline zoom and editor panel state.
- Camera Comparator settings by source, its current source/playhead, and both
  Video Overlay sources, opacities, alignment, mode and playhead.

Opening recreates readers and tools and redraws plots from saved results; it does
not rerun tracking. Playback reopens paused. Temporary workers, decoded image
caches, undo history and detached graph windows are not serialized. Finish or
cancel a tracking run before saving/opening so the snapshot is consistent.

**Original footage is referenced, not embedded. Keep the original Cine/video
files.** Copy them with the job if moving to another computer. Relative paths
are tried first, followed by the original absolute paths. If footage is missing,
Open Job asks you to locate it, and searches that folder for other missing files.
File size and a header fingerprint must match; an unrelated replacement is
rejected. Missing/invalid media and cancelled relinking leave the current job
unchanged. Job saving is explicit; close does not automatically save your work.

The format is a versioned ZIP containing `job.json` and typed NPY arrays. It never
loads pickle or arbitrary Python objects. Writes use a temporary file and atomic
replacement. Archive sizes, array headers, saved settings and frame ranges are
checked before installation. Existing `.pcaedit` and `.pcacompare` presets remain
available for their narrower uses.

## Implementation and validation

- `video_overlay.py`: synchronization, native-pixel compositing and difference.
- `video_overlay_ui.py`: independent readers, serial background decoding, controls.
- `workspace_job.py`: safe archive codec, media resolution and state validation.
- `workspace_job_ui.py`: workspace snapshots, job controls and restore orchestration.
- `cine_metadata.py`: precise capture-minus-trigger timestamps.
- `launcher-source/src/main/jobOpener.js`: shell-free document launch arguments.
- Tests cover timestamp precision, offsets, mismatched rates, missing captures,
  tracking/calibration/edit round trips, relinking and atomic save failure.

Mac packaging uses `launcher-source/job-file-association.plist` entries in the
application Info.plist. Regenerate ASAR integrity after launcher changes and sign
with the appropriate identity when distributing. Local installation keeps a
recoverable backup; this is not a newly notarized distribution release.
