"""No PCA or GUI: synthesize footage and run both array-based engines."""
import argparse
import json
from dataclasses import asdict
from pathlib import Path
import cv2
import numpy as np
from edge_tracking import EdgeAssistTracker, EdgeSettings
from tumbling_tracker import AdaptiveSilhouetteTracker


def tumbling_image(frame):
    y, x = np.mgrid[:180, :260]
    image = np.stack([55 + 16*np.sin(x/8 + frame*.15),
                      110 + 30*np.cos(y/10), 70 + 10*np.sin((x+y)/5)], axis=2).astype(np.uint8)
    mask = np.zeros((180, 260), np.uint8)
    cv2.ellipse(mask, (60+4*frame, 88+frame),
                (int(17+5*np.sin(frame*.5)), int(11+3*np.cos(frame*.3))),
                frame*17, 0, 360, 1, -1)
    image[mask > 0] = (200, 35, 170)
    return image


def edge_image(frame):
    image = np.full((180, 260), 25, np.uint8)
    x, y = 70+3*frame, 80+2*frame
    cv2.rectangle(image, (x-16, y-16), (x+16, y-10), 230, -1)
    cv2.rectangle(image, (x+10, y-16), (x+16, y+16), 230, -1)
    cv2.circle(image, (x-12, y+13), 5, 145, -1)
    return image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('trajectories.json'))
    args = parser.parse_args()
    tumble = AdaptiveSilhouetteTracker()
    edge = EdgeAssistTracker(EdgeSettings(rotation_range=0, point_lock=False))
    tumble_results = [tumble.initialize(tumbling_image(0), (31,65,58,46), (60,88)).record()]
    edge_results = [asdict(edge.initialize(edge_image(0), (70,80), (51,51)))]
    for frame in range(1,18):
        tumble_results.append(tumble.step(tumbling_image(frame), frame).record())
        edge_results.append(asdict(edge.step(edge_image(frame), frame)))
    args.output.write_text(json.dumps(dict(tumbling=tumble_results, edge=edge_results), indent=2), encoding='utf-8')
    print(f'Saved {sum(r["accepted"] for r in tumble_results)}/18 tumbling and '
          f'{sum(r["accepted"] for r in edge_results)}/18 edge observations to {args.output}')


if __name__ == '__main__':
    main()
