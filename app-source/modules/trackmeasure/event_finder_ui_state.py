"""Validate local Event Finder settings before restoring an analysis job."""
from math import isfinite


def validate_event_settings(state):
    if not isinstance(state, dict):
        raise ValueError('Invalid Event Finder settings.')
    path = state.get('path', '')
    if not isinstance(path, str):
        raise ValueError('Invalid Event Finder Cine path.')
    if not path:
        return {}
    sensitivity = state.get('sensitivity', 'balanced')
    if sensitivity not in ('low', 'balanced', 'high'):
        raise ValueError('Unknown Event Finder sensitivity.')
    position = state.get('position', 0)
    if type(position) is not int or position < 0:
        raise ValueError('Invalid Event Finder frame.')
    roi = state.get('roi')
    if roi is not None and (not isinstance(roi, (list, tuple)) or len(roi) != 4
                            or any(type(n) is not int for n in roi) or min(roi) < 0
                            or min(roi[2:]) < 1):
        raise ValueError('Invalid Event Finder region.')
    signature = state.get('file_signature')
    if signature is not None and (not isinstance(signature, (list, tuple)) or len(signature) != 2
                                  or any(type(n) is not int for n in signature)):
        raise ValueError('Invalid Event Finder file identity.')
    hits = state.get('events')
    if hits is not None:
        if roi is None or signature is None or not isinstance(hits, (list, tuple)):
            raise ValueError('Event Finder results need a region and a source identity.')
        for hit in hits:
            if (not isinstance(hit, (list, tuple)) or len(hit) != 5 or
                    any(type(n) is not int for n in hit[:3]) or not 0 <= hit[0] <= hit[1] <= hit[2]
                    or not isinstance(hit[3], (float, int)) or not isfinite(hit[3]) or not isinstance(hit[4], str)):
                raise ValueError('Invalid Event Finder candidate.')
    total = state.get('total_events', len(hits or ()))
    if type(total) is not int or total < len(hits or ()):
        raise ValueError('Invalid Event Finder candidate count.')
    return dict(total_events=total, path=path, position=position, sensitivity=sensitivity, roi=roi,
                file_signature=signature, events=hits)
