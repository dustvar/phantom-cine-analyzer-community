"""Portable edit recipes, tied to the source Cine without modifying it."""
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from viewer_timeline import validate_edit, total_frames


def source_identity(path):
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(65536)
    return {'size': path.stat().st_size, 'header_sha256': hashlib.sha256(header).hexdigest()}


def save_project(path, source, first, last, edit, grade, settings):
    payload = dict(format='pca-viewer-edit', version=1, source_name=Path(source).name,
                   source=source_identity(source), clip=[first, last], edit=deepcopy(edit),
                   grade=deepcopy(grade), image_settings=deepcopy(settings))
    path = Path(path)
    handle, temporary = tempfile.mkstemp(prefix='.pca-edit-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_project(path, source, frame_count):
    if Path(path).stat().st_size > 5_000_000:
        raise ValueError('This edit file is too large')
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('format') != 'pca-viewer-edit' or payload.get('version') != 1:
        raise ValueError('This is not a supported PCA Viewer edit file')
    if payload.get('source') != source_identity(source):
        raise ValueError('These edits belong to a different Cine. Select that Cine first.')
    payload['edit'] = validate_edit(payload.get('edit'), frame_count)
    for copy in payload['edit']['copies']:
        extra=copy.get('source_path')
        if extra and copy.get('identity') != source_identity(extra):
            raise ValueError('An added video is missing or changed: '+str(extra))
    bounds = payload.get('clip')
    if not isinstance(bounds, list) or len(bounds) != 2 or not all(type(x) is int for x in bounds) or not 0 <= bounds[0] <= bounds[1] < total_frames(payload['edit']):
        raise ValueError('Invalid saved In/Out range')
    def valid_number(value, minimum, maximum):
        return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
    grade = payload.get('grade', {})
    if not isinstance(grade, dict) or not valid_number(grade.get('black'), 0, 1) or not valid_number(grade.get('white'), 0, 1) or grade['white'] - grade['black'] < .00099 or not valid_number(grade.get('gamma'), .1, 5):
        raise ValueError('Invalid saved levels')
    curve = grade.get('curve')
    if not isinstance(curve, list) or len(curve) != 5 or not all(valid_number(v, 0, 1) for v in curve) or curve != sorted(curve) or curve[0] != 0 or curve[-1] != 1 or type(grade.get('enabled')) is not bool:
        raise ValueError('Invalid saved curve')
    settings = payload.get('image_settings', {})
    if not isinstance(settings, dict) or not all(valid_number(settings.get(k), lo, hi) for k, lo, hi in [('image_brightness', -65535, 65535), ('image_gamma', .01, 100), ('image_contrast', 0, 100)]) or not all(type(settings.get(k)) is bool for k in ('toggle_ftone', 'raw_enabled')):
        raise ValueError('Invalid saved image adjustments')
    payload['image_settings'] = {key: settings[key] for key in ('image_brightness', 'image_contrast', 'image_gamma', 'toggle_ftone', 'raw_enabled')}
    return payload
