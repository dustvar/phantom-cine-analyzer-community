# PCC-style playback controls

## User behavior

The transport has seven purple buttons arranged like the PCC reference:

| Position | Action |
| --- | --- |
| Top left | Play backward at 1x |
| Top center | Pause |
| Top right | Play forward at 1x |
| Bottom left | Play backward at 4x |
| Bottom center-left | Pause, then move back exactly one frame |
| Bottom center-right | Pause, then move forward exactly one frame |
| Bottom right | Play forward at 4x |

The last continuous-play button pressed takes effect immediately. A single-frame
button always pauses first. Playback stops at the active clip bound rather than
wrapping or modifying the source Cine.

## Code map

The implementation is deliberately outside the graph classes and is centered
in `app-source/modules/trackmeasure/simplemeas_ui.py`:

- Search for `pcc_transport_controls` to find construction and placement.
- `_playback_bounds` returns the active non-destructive clip interval.
- `_start_playback` switches direction and speed immediately.
- `_pause_playback` stops the timer and synchronizes button state.
- `_advance_playback` advances timed playback and enforces clip bounds.
- `_step_one_frame` pauses and moves exactly one frame.
- `ClipRangeSlider` owns the bracketed In/Out range and scrubber geometry.

The same transport widget is attached to Viewer, Track, 2 Pt, 3 Pt, 2 Line,
and Area layouts. Slider labels are positioned from the actual slider groove
and handle geometry so negative Phantom frame numbers align with its endpoints.

Keyboard bindings live beside the UI event handlers: Space toggles forward
play/pause, J and L play backward/forward, Shift selects 4x, arrow keys step,
Home/End jump to bounds, and I/O set In/Out.

## Verification

`tests/test_viewer_controls.py` covers transport construction, stepping,
direction changes, clip boundaries, and keyboard behavior. The graph classes
are not part of the transport implementation and should remain untouched when
integrating this playback feature by itself.
