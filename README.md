# Phantom Cine Analyzer Additional Features

This repository is a source-level handoff of playback, multi-Cine, interface,
and tracking improvements made to Phantom Cine Analyzer (PCA) 1.1.18. It is a
community modification, not an official Vision Research or AMETEK release.

The original application remains subject to the included Non-Commercial Open
Source License. No Cine footage, packaged applications, installers, signing
keys, or private build credentials are included in this repository.

## What changed

- A PCC-style seven-button transport in Viewer, Track, 2 Pt, 3 Pt, 2 Line, and
  Area modes, plus full-Cine first/last-frame buttons around the scrubber.
- A dedicated Viewer with clip bounds and up to four simultaneous Cine panes.
- Reliable multi-object selection and autotrack-dialog behavior.
- The original **Intensity (Classic)** tracker remains available unchanged.
- An opt-in **Hybrid (Edge + Intensity)** tracker uses an exact tracked point,
  a movable/rotatable reinforcement region, rotation search, edge visualization,
  smart range detection, dual-reference confidence, and a live post-processing
  confidence filter (0.90 by default).
- Checked tracking objects populate a named two-column point-preview grid. A
  live Fade Paths overlay can reduce path opacity near the current tracked
  points without changing stored measurements.
- Cursor-centered wheel zoom, a persistent right-side panel, and an adjustable
  accent color.

The existing graph calculations and presentation were retained. The only
graph-class additions are the explicitly requested multi-Cine overlay and
Angle/Angular Speed support; playback and dual-confidence code do not alter
those classes. See [the modification log](app-source/MODIFICATIONS.playback.txt)
for the complete feature list.

## Repository layout

- `app-source/modules/trackmeasure/` — Python/PySide Track & Measure module.
- `launcher-source/` — Electron launcher and packaging scripts.
- `tests/` — headless unit and UI regression tests.
- `docs/` — developer-oriented change maps and design notes.
- `environment.yml` — reproducible Conda environment for development.

## Development setup

```sh
conda env create -f environment.yml
conda activate PCA
QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -v
```

To run Track & Measure directly:

```sh
python app-source/modules/trackmeasure/launch_app.py /path/to/example.cine
```

The launcher can be prepared with `npm install` from `launcher-source/`.
Packaging copies the local Track & Measure source into the app. An alternate
module repository can be supplied through `TRACKMEASURE_REPO_URL`; credentials
must not be committed in the URL or source tree.

## Reverting the confidence experiment

The tag `checkpoint-before-dual-confidence` identifies the fully working state
immediately before adjacent-plus-setup confidence was added. Developers can
inspect it without changing branches:

```sh
git diff checkpoint-before-dual-confidence..HEAD
```

See [Developer handoff](docs/DEVELOPER_HANDOFF.md),
[Playback controls](docs/PLAYBACK_CONTROLS.md), and
[Hybrid tracking](docs/HYBRID_TRACKING.md) for implementation details.
