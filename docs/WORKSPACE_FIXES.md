# Workspace and calibration fixes — 1.2.1

The calibration blackout was reproduced with real Qt mouse events. Calling
`parentItem()` on a parentless background pixmap changed its PySide ownership;
when the temporary wrapper was released, the image disappeared from the scene.
The renderer now keeps the background wrapper alive, skips that item's parent
lookup during hit tests, and releases its reference before clearing the scene.
Calibration clicks are dispatched after the mouse event, and the image is
redrawn after the scale dialog closes.

Selecting an existing Cine now emits `cine_activated`, while loading a new
workspace emits `new_cine_load`. Only a new workspace selects Viewer by default.
The analysis mode and selected details tab are preserved across Cine changes.
Widget refreshes block adjustment, clip, frame, and tracker parameter signals
so an outgoing Cine's controls cannot overwrite the incoming Cine's state.

Each pane's **−** button hides it without closing its Cine or deleting data.
Hidden Cines appear as named eye tabs above the views. Restoring a tab selects
that Cine, and the grid expands or contracts around the visible panes. The last
visible Cine cannot be hidden. A newly loaded workspace resets visibility and
clip bounds. Hidden Cines retain their existing contribution to combined graphs.

File loading now prepares handlers on a worker, then installs the workspace on
the UI thread. Failed loads close partial handlers and retain the prior
workspace. Preview rendering restores the active context in a `finally` block,
uses the bounded processed-image cache, and chooses its image format from the
actual frame buffer rather than the active Cine's Raw toggle.

Validation on September 14, 2026:

- 88 tests passed, including connected Qt signal and mouse-event regressions.
- Four real color Cine files loaded and rendered together in a separate Qt test
  instance using the installed PCA Python environment and application stylesheet.
- Track mode, frame position, calibration, and a tracking object survived pane
  changes; hiding down to one pane and restoring all four preserved the data.
- Both calibration mouse clicks, dialog completion, later frame redraws, and
  playback/pause completed without missing images or Qt signal exceptions.
- Rendered single-pane, four-pane, and calibration views were visually checked.

The changes do not modify the Classic/Hybrid matching algorithms or graph math.
