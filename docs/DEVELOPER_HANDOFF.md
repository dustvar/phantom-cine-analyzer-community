# Developer handoff

## Architecture

The Track & Measure application follows a small UI-to-model pipeline:

1. `simplemeas_ui.py` constructs the PySide interface and translates gestures.
2. `simplemeas_vm.py` owns Cine/workspace state and applies settings.
3. `simplemeas_tools.py` implements measurement and tracking workflows.
4. `autotrackalgorithms.py` contains the Classic and Hybrid matchers.

The launcher is intentionally separate under `launcher-source/`. Its packaging
hook copies the checked-in Track & Measure module, so a public build does not
depend on a private source host.

## Change map

| Feature | Primary implementation | Regression coverage |
| --- | --- | --- |
| Seven-button playback | `simplemeas_ui.py` transport helpers and `ClipRangeSlider` | `test_viewer_controls.py` |
| Viewer and clip range | `simplemeas_ui.py`, `simplemeas_vm.py` | `test_viewer_controls.py` |
| Up to four Cines | `simplemeas_ui.py`, `simplemeas_vm.py` | `test_multi_cine_workspace.py` |
| Multi-object/autotrack creation | `simplemeas_ui.py`, `simplemeas_tools.py` | `test_tracking_creation_workflow.py` |
| Exact-point comparison and Hybrid fixture reuse | `simplemeas_ui.py` | `test_tracking_creation_workflow.py` |
| Hybrid matching and smart boundaries | `autotrackalgorithms.py`, `simplemeas_tools.py` | `test_hybrid_autotrack.py`, `test_tracking_creation_workflow.py` |
| Blur-tolerant Hybrid edge matching and 0.1 px / 0.1° pose refinement | `autotrackalgorithms.py`, `simplemeas_tools.py` | `test_hybrid_autotrack.py` |
| Public launcher handoff | `launcher-source/` | `test_launcher_source.py` |

Detailed symbol-level notes are in [Playback controls](PLAYBACK_CONTROLS.md)
and [Hybrid tracking](HYBRID_TRACKING.md).

## Important compatibility boundaries

- **Intensity (Classic)** follows the original PCA point/template correlation
  path. Hybrid-specific state and scoring must stay behind the tracking-method
  check.
- Hybrid reinforcement pixels and point offsets live in object-local
  coordinates. Only the per-frame X/Y/angle pose may change; UI overlays use
  an explicit OpenCV-to-Qt sign conversion. The top-right preview preserves the
  native Cine crop and draws a circular angle indicator over it.
  Pose is always solved from the frozen setup-frame patch; the nearest accepted
  frame provides only search/angle continuity and the adjacent confidence term.
- Hybrid's broad angular search remains coarse for runtime, followed by a local
  0.1° scan. Translation uses quadratic correlation-peak refinement and is
  stored at 0.1-pixel resolution. Do not describe resolution as guaranteed
  absolute accuracy without validation against representative footage.
- The Hybrid edge term correlates continuous gradient magnitude for robustness
  to blur. The purple viewport overlay remains a thresholded edge map for
  interpretability. `edge_weight` blends edge and intensity evidence within a
  comparison; `adjacent_confidence_weight` blends adjacent and setup results.
- The inner rotated fixture is the physical visibility boundary and stops its
  processing direction as soon as any corner leaves the image. The outer search
  area may still be shifted inward at the frame edge.
- Add Object must retain event priority over a `HybridRegionItem`; removing that
  ordering recreates the bug where points cannot be added inside an old fixture.
  Exact-point snapping is screen-space tolerant but stores the source point's
  full floating-point coordinate.
- Playback and dual-confidence changes are outside `TrackingGraph` and
  `TrackingGraphWindow`. The feature checkpoint changes those classes only for
  the separately requested multi-Cine overlay and Angle/Angular Speed modes;
  existing graph calculations and visual styling were otherwise retained.
- Clip bounds constrain playback; they do not delete or rewrite Cine frames.
- Each Cine pane owns independent measurements, clip bounds, adjustments, and
  tracker state. Graphs may combine enabled series across panes.

## Suggested review sequence

1. Review `docs/PLAYBACK_CONTROLS.md` and the playback-focused commit diff.
2. Review `docs/HYBRID_TRACKING.md` and compare the dual-confidence commit with
   `checkpoint-before-dual-confidence`.
3. Run the full headless test suite from the repository root.
4. Validate with representative noisy, low-contrast, rotating, and occluded
   Cine sequences before production use.

## Git checkpoints

`checkpoint-before-dual-confidence` is the requested rollback point. To make a
new branch from it:

```sh
git switch -c restore/pre-dual checkpoint-before-dual-confidence
```

To inspect only the confidence experiment:

```sh
git diff checkpoint-before-dual-confidence..HEAD -- \
  app-source/modules/trackmeasure tests docs/HYBRID_TRACKING.md
```

## License and provenance

This tree is distributed under the included NCOSL text. Modifications are
documented in `app-source/MODIFICATIONS.playback.txt`. Do not add Cine footage,
customer data, signing material, account tokens, or private repository URLs.
