# Camera Comparator

The **Camera Comparator** workflow sits directly below Viewer in the left navigation.
It uses an independent media reader and settings. Viewer cuts, speed ramps, zooms,
added videos, grading, and tracking state do not change the comparison.

## Using it

1. Select a loaded source or choose **Open footage…**. A source preview is available
   even before the comparison is configured.
2. Confirm **Source sampling** (unbinned or 2×2). The current Cine reader exposes
   optional raw sensor-mode codes, but their binning meaning is not verified; neither
   a capability flag nor small image dimensions is treated as proof of active binning.
3. Use **Source details / overrides** when the model, pixel pitch, acquisition rate,
   exposure, or source crop center needs correction. Crop center X/Y are measured
   in native sensor pixels relative to the optical axis. Zero assumes centered readout.
   Ordinary video requires acquisition/geometry information; its file playback FPS
   is deliberately not used as camera acquisition FPS.
4. Select a target, installed option, acquisition FPS, and a verified preset.
   The sampling filter narrows the list to unbinned or 2×2 modes. Presets whose
   published ceilings are below the requested FPS are omitted. This is a catalog of
   verified common operating points, not a claim to enumerate every possible ROI.
5. Compare the masked source and target. Dark gray marks excluded source area;
   hatching marks target pixels that cannot be supplied by the recorded source.
   Re-aim X/Y represents hypothetical camera re-aiming, not a verified sensor ROI offset.
6. Adjust playback, phase, and the source-frame interval. **Measure feature on source**
   accepts two clicks and reports target pixels across that feature. A matching loaded
   Cine's existing physical calibration is included when its units are physical.
7. Save a `.pcacompare` preset or export target-only / side-by-side MP4, MOV, or AVI.

Source details collapse initially for recognized cameras. Overrides remain available.
Both previews share an independent display-gamma control. No original images are changed.

## Geometry and image formation

Pixel footprints are calculated from native pitch × binning factor, with the source
recorded image's offset relative to the optical axis. The default assumes the same
optics, pose, and magnification. Recorded pixel dimensions alone are insufficient
when source and target pitches differ.

Target pixels use separable area integration of linear source pixels. Partial pixels
extending beyond source coverage are hatched rather than filled with invented content.
Color Cine is demosaicked in linear space using its CFA and white-balance gains;
2×2 target modes produce monochrome output. This approximates spatial sampling and
is not a hardware sensor SNR, spectral response, colorimetry, or full-well simulation.
Smaller target pixels cannot recover detail absent from the source.

Ordinary RGB video is approximately linearized as sRGB. Its pixels must correspond
to the declared sensor samples; any previously baked scaling/cropping must be accounted
for in source assumptions. Cine footage is preferable for quantitative comparisons.

## Timing

Cine TIME64 entries are converted to relative seconds by subtracting integer seconds
and fractions separately. This avoids precision loss from subtracting large epoch
floats. A bounded timestamp block is validated before reading; pixel data stays mapped.
Normal frame intervals are averaged to reduce timestamp quantization while excluding
gaps. Actual timestamps control sampling even when header FPS is rounded or inconsistent.
Without timestamps, cadence is explicitly assumed from acquisition FPS / saved decimation.

For target rate F and phase p (0 to <1), desired captures are at `(k+p)/F`. The nearest
recorded source capture is selected when within half the normal source interval, with
small timestamp-quantization tolerance. Noninteger ratios are marked approximate.
Missing captures remain missing, each source capture is selected at most once, and
large timestamp gaps do not allocate an array for every missing capture.

- **Same playback FPS:** one selected target capture per output frame. Target-only
  export ends after its selected frames. Side-by-side export lasts for the source
  stream and explicitly labels the target's last-frame hold after it finishes.
- **Synchronized event time:** source advances through recorded timestamps; target
  holds its latest available selected capture. Before the first selected target
  capture it shows a labeled placeholder. Holds do not count as captured pictures.
  The displayed slowdown is event-replay slowdown, not target-rate / presentation-rate.

Target rates above the available stored cadence or camera preset ceiling are rejected.
The selected frame interval's real duration and number of available captures are shown.

Exposure and blur stay as recorded. The tool checks published target minimum exposure
and frame-period bounds, but does not claim to verify hardware-specific timing margins.
Incompatible exposure is flagged in preview and export. EDR effects, when present,
remain part of the source footage. Noise, sensitivity and dynamic range are not simulated.

## Camera catalog and provenance

`app-source/modules/trackmeasure/comparator_data/profiles.json` records checked dates,
manufacturer URLs, native geometry, resolution increments, explicit modes, and options.
Profiles: TMX7510, T2110, T2410, T3610 and T4040. T2110-E225 is a capped option;
FAST exposure/speed options remain explicit and are never assumed installed.

Known documentation differences:

- TMX7510 sensor height appears as 14.8 mm on the product page and 15.4 mm in its
  datasheet. Mapping uses the documented 1280×800 active pixel array × 18.5 µm
  (23.68×14.80 mm). Physical die dimensions do not enter the geometry.
- T3610 1280×640 appears at 46,250 fps on the product page and 47,510 in the datasheet.
  The catalog uses the conservative 46,250 ceiling and exposes the discrepancy.
- T4040 uses the current manufacturer minimum of 1.04 µs, rather than older 1 µs sheets.

The **Specifications and sources** button exposes provenance and unresolved notes.
No physical camera/hardware comparison was performed as part of software validation.

## Persistence, export and isolation

The independent version-1 `.pcacompare` format follows existing atomic-write and
source-fingerprint conventions. It contains source assumptions, comparison settings,
and source/target profile snapshots. Wrong sources, unsupported schemas/settings and
changed profiles are rejected. Existing `.pcaedit` projects are unchanged.

Preview and export share `Comparison.render`, geometry and capture selection. Export
uses an independent reader in a cancellable thread, writes a temporary file, and replaces
the destination only on success. Failure/cancellation preserves an existing destination.
Exports are silent. Settings and a simulation label occupy a band outside the image.
Small target images may be padded to make labels readable; side-by-side images are
fitted without stretching. Listed camera resolution describes the simulated image,
not the annotated export canvas.

Preview caches at most three decoded linear frames. Loading, preview rendering and
export run in background workers; queued preview requests coalesce during scrubbing.
A slow display can skip presentation updates, while export includes every planned frame.

## Validation

Run the repository suite with the PCA Python environment:

```sh
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

Comparator coverage includes identity/physical geometry, fractional area integration,
color ordering, binned sources, missing scene regions, mode/option limits, TIME64
precision, integer/noninteger cadence, sampling phase, gaps, both playback modes,
source metadata requirements, presets, native navigation, source switching, decoded
exports in all three containers, cancellation, and encoder failures.

Real-file validation also uses a locally supplied Cine, checks source/editor isolation, renders the
native interface, and decodes target-only and synchronized side-by-side MP4s. Software
validation does not replace a future calibration-target comparison against actual cameras.
