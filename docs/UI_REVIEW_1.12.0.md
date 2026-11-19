# Independent PCA UI and functionality review — final

**Verdict: every material criterion below now scores at least 9/10 within the tested scope.** This conclusion follows multiple fix-and-retest rounds; the first review did not pass. These are independent engineering/UX judgments based on actual rendered Qt screens and exercised behavior, not just a source review.

Reviewed 2026-10-27, PCA 1.12.0 Guided Tracking, native macOS ARM64 Qt 6.9.1, using the app's stylesheet. The target Windows 11 package was not executed on a Windows machine by this reviewer. These scores therefore describe the shared application and workflows tested here, not a Windows compatibility certification.

## Scorecard

The standard for 9 is a clear, usable interface with reliable normal and correction paths in the exercised cases, and no remaining material finding in that area. 10 would require wider customer-footage, device and accessibility validation.

| Criterion | Initial review | Final | Evidence supporting the final grade |
|---|---:|---:|---|
| Video priority and workspace allocation | 8.5 | **9.0/10** | Tall default image; short editing tools on the side; timeline available on demand; empty Track plots no longer consume the screen. Video and graph can be resized vertically. |
| Responsive appearance and readable controls | 7.5 | **9.0/10** | Inspected 1024×720 and 1280×820 logical layouts, including 150% Qt scaling. Side-only inspectors, usable Settings width, readable buttons and stable window width. The Region second row appears correctly before Settings is opened. |
| Guided setup, state and discoverability | 6.5 | **9.0/10** | Method chooser → Target → Region → Search → Review → Complete & Track works. Review edits survive Back/Next; Tumbling exposes relevant controls; stale instructions and conflicting mode controls are removed. |
| Precise geometry and independent references | 7.0 | **9.5/10** | Search movement/rotation does not move the ROI or measured point; undersized search expands to contain the ROI. Clicking a resize handle without dragging is a no-op. Point, region center and fixed reference coordinate remain separate. |
| Playback, navigation and editing feedback | 8.0 | **9.0/10** | Floating playback, FPS control, seek, hide/show, zoomed background panning and handle priority work. Setup-frame return is visible, and geometry edits return to the anchor after scrubbing. Hover-wheel does not silently change unfocused values. |
| Tracking execution and reporting | 7.0 | **9.0/10** | Actual generated Cine runs complete with all three engines. Untuned Edge Assist now evaluates the requested range instead of silently retaining only the anchor. A mode switch cannot turn Complete into a no-op. Progress and cancellation recover normally. |
| Motion plots, heat maps and numerical transparency | 9.0 | **9.0/10** | Local-fit/raw derivatives are explicit; original coordinates stay unchanged. Signed X/Y displacement references, timestamp handling, speed through an origin crossing, noisy sinusoidal acceleration and missing-data gaps pass targeted checks. |
| Preservation, cancellation and session stability | 8.5 provisional | **9.0/10** | Job roundtrips preserve tracked arrays; draft cancellation restores prior data; active cancellation preserves partial measurements and restores controls. Repeated-window teardown and the previously failing callback paths now pass. |

## Evidence from the final passes

- **55 combined UI tests passed independently** in 33.05 s, covering Compact workspace, four-Cine interactions and guided tracking. This includes teardown of repeatedly created windows, calibration, hidden/restored Cines, playback, heat maps, jobs, curve/timeline/data-card behavior, guided geometry and completion. Log: `/tmp/grader-final-ui-suite.log`.
- **13 geometry and kinematics tests passed independently**, including noisy oscillation, irregular timestamps, fixed-reference invariance, missing samples, rotated search rejection and one-axis matching. Log: `/tmp/grader-numerical.log`.
- The implementation agent's broader **295-test suite also passed**; that result was inspected but is distinguished from the reviewer's independently launched suites. Log: `/tmp/pca112-full-final.log`.
- Real decoded synthetic Cine: 320×240, 30 frames, vertically moving disk with interior detail, 2000 fps metadata, Cine numbering −10..19. Classic produced 30 points with maximum 0.5 px axis error. Tumbling produced 30 centroid measurements with 0 px error on this simple silhouette. Default Edge produced 30 points with maximum 0.1 px X error and 0 px Y error.
- Default Edge's actual event-loop run completed in 0.92 s. Earlier apparent slow runs were a QTest polling/GIL artifact; they are **not** app-performance evidence.
- Actual cancellation after 100 ms retained 5 partial points, hid progress, restored Add Object and produced no signal exceptions.
- Three-engine job save/reopen preserved the measured coordinate arrays exactly. Fixed-pixel picking did not change the ROI or anchor point.
- Rendered final captures were inspected for Viewer, timeline, Track, chooser, guided steps, Settings, Review, Tumbling, Results and heat-map controls. The reviewer inspected local screenshots and geometry measurements at multiple window sizes.

## Specific findings that were fixed

1. Edge's smart-range preparation overwrote requested frame bounds with the anchor extent before tracking, preventing an ordinary default run.
2. A click on the pink resize handle enlarged 31×31 to 55×55 because its grab offset was ignored.
3. Back/Next discarded Review name, range and fixed-reference choices.
4. Narrow Settings and method selectors clipped controls; opening Results could force the window wider than the screen.
5. Tumbling Search displayed disabled, irrelevant rigid-box controls.
6. Geometry could be changed while its setup-frame ROI was invisible after scrubbing.
7. Old step instructions conflicted with the new guide; Manual mode could silently disable Complete & Track.
8. Empty plots consumed most of the video space before tracking.
9. Scene-owned wrappers and delayed callbacks survived Qt widget destruction, causing a native teardown crash or deleted-object errors. The fix keeps ownership and callback lifetimes aligned; a Qt upgrade was not required.
10. Duplicate completion callbacks could collapse a Results drawer that had just been opened.

The initial review report and screenshots preserve the failures rather than retroactively marking them as passes.

## Remaining recommendations and limits

- Run the supplied installation check and a representative saved job on the actual Windows 11 work computer before a customer demonstration. This review could not verify native Windows fonts, DPI integration, codecs or startup.
- Validate the original oscillating-paper Cine when available. The measurement fixes have controlled-data evidence, but that exact footage was not present here. Compare signed Y-acceleration with raw positions and an appropriate filter window; smoothing should not conceal a tracking error.
- Keep a small, permission-cleared regression set containing a straight edge, a rotating rigid feature and a tumbling silhouette against clutter. The current synthetic tests are useful but cannot guarantee segmentation of arbitrary customer footage.
- For future polish, show the object name and selected engine in the guide breadcrumb when space permits. The current compact step label and method tooltip are usable, so this is not a release blocker.

The Tumbling coordinate is the **projected silhouette centroid**, not a measurement of physical center of mass. The final release archive itself remains the implementation agent's packaging responsibility; this review does not claim to have executed its Windows binaries.
