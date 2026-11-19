# Viewer interface — 1.9.1

The Viewer footer separates four tasks into labeled cards:

| Group | Purpose |
| --- | --- |
| **Playback** | Play, pause, reverse, step, and seek. The frame badge stays in this card. Moving the scrubber changes the preview, not the edit. |
| **Timeline** | See segments and their playback FPS, add or multiply videos, split at the current frame, edit speed/effects, and undo/redo. Click a segment to preview it. Click a junction to edit its transition, or drag it to move the cut. |
| **Trim** | Drag the green handles or use Set In / Set Out to choose what exports. The endpoint buttons jump to those bounds; Reset Clip restores the range. |
| **Finish** | Adjust Levels & Curves, add Data & Text, and Export Clip. |

The blue segment strip is an editing timeline, not a scrollbar. Each segment
labels its **fps playback**. Gold marks the playhead and speed-ramp spans.
Different videos in a sequence use subdued different colors; cyan chain-link
icons still distinguish video joins from cuts within one video.

Use **Zoom − / + / Fit** for timeline magnification. A neutral horizontal
scrollbar appears only when the timeline is zoomed beyond the available width.
Panning and zooming the timeline do not alter source frames, clip bounds, or edits.

**Speed & Effects** replaces the old **Edit Timeline** button. Its tabs are
**Segments**, **Data**, **Zoom**, and **Text**. **Data & Text** replaces
**Include Data** and opens the same overlay controls. Existing saved edits and
whole jobs keep their settings.

The native app and launcher use charcoal surfaces, neutral borders, and a
muted Phantom accent for selection, focus, and primary actions. The accent
picker remains available. Tracking plots retain their existing presentation
and calculations; this change concerns the surrounding interface.

**Cmd/Ctrl+O** opens a saved job; **Cmd/Ctrl+Shift+O** chooses Cine footage.
**Cmd/Ctrl+B** still splits at the current frame, and **Space** controls playback.

Validation covers native Viewer renders at 1024×720 and 1460×950, real Cine
footage, the editor and levels panels, Track/Info, Camera Comparator, and Video
Overlay. Connected Qt regressions exercise the new Split action with undo/redo,
conditional timeline scrolling, and the frame badge's ownership and placement.
