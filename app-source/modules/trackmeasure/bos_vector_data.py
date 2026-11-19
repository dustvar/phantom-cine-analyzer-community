"""Export a single BOS field in source-image coordinates, before display filtering."""
import csv
import math
import os
from pathlib import Path
import tempfile

import numpy as np


def save_vector_csv(path, result, settings):
    """Write reliable measurement sites, including sites omitted for visual clarity."""
    path = Path(path)
    if path.suffix.lower() != '.csv':
        raise ValueError('Choose a CSV file for the vector measurements.')
    source = Path(settings['path'])
    if path.resolve() == source.resolve() or (path.exists() and source.exists() and path.samefile(source)):
        raise ValueError('Choose a destination different from the source Cine.')
    field = result.get('display_field')
    raw = result.get('field')
    if (field is None or raw is None or result['index'] != settings['position']
            or result['reference'] is None):
        raise ValueError('Wait for a measured frame before saving its vectors.')
    valid = field.valid & np.isfinite(field.dx) & np.isfinite(field.dy)
    if not np.any(valid):
        raise ValueError('This frame has no reliable vectors to save.')

    arrows = result.get('vector_samples')
    glyphs = np.zeros(valid.shape, dtype=bool)
    saturated = np.zeros(valid.shape, dtype=bool)
    if arrows is not None:
        glyphs[arrows.rows, arrows.cols] = True
        saturated[arrows.rows, arrows.cols] = arrows.saturated

    first = result['first']
    reference = result['reference']
    stop = min(result['count'], reference + settings['reference_count'])
    time_us = result.get('time_us')
    stamp = '' if time_us is None or not math.isfinite(time_us) else format(time_us, '.9f')
    common = (first + result['index'], stamp, first + reference, first + stop - 1,
              settings['method'], int(settings['motion_compensation']))
    columns = ('cine_frame', 'time_from_trigger_us', 'reference_first_frame', 'reference_last_frame',
               'method', 'anchor_corrected', 'x_px', 'y_px', 'dx_px', 'dy_px', 'magnitude_px',
               'raw_dx_px', 'raw_dy_px', 'match_score', 'selected_for_arrow', 'arrow_length_capped')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent,
                                         prefix='.pca-vectors-', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            writer = csv.writer(stream)
            writer.writerow(columns)
            for row, col in zip(*np.nonzero(valid)):
                dx, dy = float(field.dx[row, col]), float(field.dy[row, col])
                writer.writerow((*common, int(field.x[col]), int(field.y[row]), format(dx, '.9g'),
                                 format(dy, '.9g'), format(math.hypot(dx, dy), '.9g'),
                                 format(raw.dx[row, col], '.9g'), format(raw.dy[row, col], '.9g'),
                                 format(field.confidence[row, col], '.9g'), int(glyphs[row, col]),
                                 int(saturated[row, col])))
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
    return int(np.count_nonzero(valid))
