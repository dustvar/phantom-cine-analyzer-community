# Dependencies and commercial integration

Scope: **only `edge_tracking.py` and `tumbling_tracker.py`**, and their standard
Python demonstration. PCA's GUI, original Classic tracker, Cine handling, report
libraries and video-export libraries are outside this standalone dependency set.

| Component | Used for | Inspected version | Upstream license |
| --- | --- | --- | --- |
| Python standard library | Dataclasses, numeric scalar helpers; example JSON/CLI | Python 3.13 | PSF/Python license |
| NumPy | Arrays, histograms, centroids, numeric operations | 2.2.0 | BSD 3-Clause |
| OpenCV native library | Correlation, gradients, interpolation, morphology, connected components, GrabCut, optical flow | 4.11.0 | Apache 2.0 |
| OpenCV Python wheel packaging | `cv2` Python distribution | opencv-contrib-python 4.11.0.86 in PCA | MIT; bundled components have their own notices |

NumPy's BSD license and OpenCV's Apache license allow commercial use subject to
their terms. Preserve required copyright/license notices and Apache NOTICE
material where supplied, and indicate changes where required. The MIT packaging
license is separate from OpenCV's native-library license. Binary wheels can bundle
additional libraries; their exact platform/build notices accompany the selected
wheel and must travel with a redistributed build as applicable. An algorithm-only
source handoff and a bundled executable are different distributions.

The `licenses/` directory contains unmodified notices from the **inspected PCA
NumPy/OpenCV distributions**, including OpenCV's third-party notice collection.
This is an inventory of those versions, not a declaration about every downstream
build. The suggested headless wheel uses the same OpenCV release and omits GUI
support; use that wheel's own notices in the receiving product. No OpenCV contrib
algorithm, pretrained model, neural-network framework, GPL tracking package,
external service or network connection is required by these engine modules.

The custom tracking code's ownership/licensing is separate from its dependencies;
this export preserves project provenance and does not relicense that code.

Primary sources checked 2026-10-09:

- [NumPy license](https://numpy.org/doc/stable/license.html)
- [OpenCV license by version](https://opencv.org/license/)
- [OpenCV Python distribution licensing](https://github.com/opencv/opencv-python#licensing)
- [Python license](https://docs.python.org/3/license.html)
- [OpenCV GrabCut documentation](https://docs.opencv.org/4.x/d8/d83/tutorial_py_grabcut.html)
- [OpenCV optical-flow documentation](https://docs.opencv.org/4.x/d4/dee/tutorial_optical_flow.html)
