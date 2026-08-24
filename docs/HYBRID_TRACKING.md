# Hybrid tracking design

Hybrid tracking is opt-in. Choosing **Intensity (Classic)** keeps the original
PCA point/template workflow. Choosing **Hybrid (Edge + Intensity)** adds the
geometry-aware workflow described here.

## Setup model

1. The user clicks the exact point whose X/Y position should be reported.
2. PCA places a reinforcement region around it.
3. The region may be moved, resized, or rotated independently. Its offset from
   the exact point rotates with the estimated object pose.
4. Purple pixels show the edge geometry actually entering the score.

The setup frame, exact point, reinforcement patch, and initial angle remain the
global reference for that tracking pass.

## Processing order

Smart processing starts immediately after the setup frame and moves forward to
the end. It then starts immediately before the setup frame and moves backward
to the beginning. Each pass uses the nearest successfully accepted frame in its
own direction as the adjacent reference. This avoids jumping across a failed or
unprocessed frame while still allowing the tracker to grow outward naturally.

## Confidence score

For candidate frame `t`, two complete Hybrid matches are calculated:

- `S_adjacent`: candidate reinforcement geometry versus the nearest accepted
  neighboring frame in the processing direction.
- `S_setup`: the same candidate geometry versus the original setup frame.

Each match already incorporates normalized intensity correlation, edge overlap,
edge density/saturation, peak uniqueness, motion continuity, and any configured
rotation search. The default combined score is a weighted geometric mean:

```text
confidence = S_adjacent^0.65 × S_setup^0.35
```

The geometric mean is intentional: a very weak setup match cannot be hidden by
a locally strong but drifting neighbor match, and a noisy self-consistent patch
cannot receive a high score merely because adjacent frames share the same
noise. The **Neighbor Weight** control in Advanced changes `0.65`; the remaining
weight is assigned to the setup frame.

The per-frame `confidence_components` diagnostic stores adjacent, setup,
combined, neighbor-frame, and setup-frame values. The accepted point table uses
the combined score. The user-confirmed setup frame remains unscored because
self-correlation would report a meaningless perfect value.

## Smart range boundary

The configured threshold is applied to the combined score. Consecutive misses
stop that direction after `smart_miss_limit`; accepted points before that
boundary remain available. This is a pragmatic boundary detector, not proof
that the physical object is absent.

## Tuning and validation

- Increase Neighbor Weight when appearance changes gradually but should remain
  continuous between frames.
- Decrease it when long-term drift is the dominant failure mode.
- Increase Edge Weight for distinctive, stable outlines; reduce it for soft or
  low-contrast targets.
- Validate thresholds on representative footage. Confidence is a matcher score,
  not a calibrated probability.

Coverage is in `tests/test_hybrid_autotrack.py` and
`tests/test_tracking_creation_workflow.py`, including directional pass order and
the adjacent-plus-setup score composition.

