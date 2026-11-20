# Event Finder

Use **Tools → Event Finder** after recording. It searches for changes in a
rectangle and gives you native Cine frame numbers, including negative
pre-trigger numbers.

## Search a recording

1. Select an open Cine, open a saved `.cine`, or choose a camera recording.
2. Draw a rectangle around the path of the event, or choose the **4 × 4 point**
   and click a known location. The rectangle checks for local changes as well as
   overall brightness. The point measures average brightness in a 4 × 4 image area. A feature that stays outside the point will not change its reading.
3. Set the first and last frames to search and choose the sampling interval.
   **Every 1 frame** checks every frame. An interval of 4 observes at least
   one sample of a change visible for four consecutive frames inside the range.
   The sampled frame can be faint. An interval of 10 can skip that event entirely.
   Both endpoints of the selected range are always checked.
4. Select a candidate to inspect its strongest frame, then step through the
   neighboring frames to establish onset and disappearance. The strongest
   change can occur on a different frame from the largest visible bubble.
   Raise sensitivity for weaker changes or lower it if too many candidates appear.

## Read a completed camera recording on the Mac

Connect the camera to the Mac through its Ethernet adapter. On a VEO with a
10 Gb link, use the XIP on the camera label (normally 172.16.x.y). On the
standard Gigabit link, use the regular IP (normally 100.100.x.y). Give the
Mac's wired interface a different address in the corresponding subnet. The
factory network mask is 255.255.0.0. See the VEO manual for models with
custom network settings.
[Phantom VEO manual, chapter 3](https://www.phantomhighspeed.com/-/media/project/ameteksxa/visionresearch/documents/manuals/veomanual.pdf?download=1)

Open the camera recording picker, enter the address, and list the completed RAM
Cines. Finish any other image playback or download using the same camera
interface before opening a recording. PH16 allows one image-transfer stream
per interface. Leave the selected RAM recording in place until the scan and
frame review finish. PCA checks its trigger timestamp and stored state during
transfer to detect replacement. It sends read requests for stored frames and
does not arm, trigger, change exposure or delete Cines.

The camera path uses the standard PH16 TCP image connection. The recorded
frames travel over the link in batches, and PCA crops the search rectangle on
the Mac. The ROI cuts analysis work. Scan time also depends on the total amount
of frame data transferred and the actual Ethernet speed. A saved Cine can be
scanned independently after disconnecting the camera. The VEO 1310S was tested on a direct
10GBASE-T link to a Mac. PCA listed a completed RAM recording at its XIP and
read actual frames using the standard PH16 TCP image connection.
[PH16 camera protocol, §§2–3, 4.7, 5.9–5.10](https://github.com/DiamondLightSource/ADPhantom/blob/f0dab1241fc236189cc2d124cc97d43985a4eea6/documentation/v16proto-2.3.pdf)

## Faster camera scans

The standard camera image command sends a complete image for each requested
frame. PCA computes the measurement only on the chosen rectangle or point.
Selecting every tenth frame requests those frames directly from the camera.
Selecting a shorter frame range also reduces the amount transferred. Most
uncompressed monochrome Cines on disk support reading the rectangle directly.

**Fast (8-bit)** halves image bytes compared with a 16-bit transfer on cameras
that support both formats. Every frame remains available at a sampling interval
of 1. Use full precision for faint changes that may disappear at 8-bit precision.
The estimate during a scan uses the speed observed on the current connection.

For sampled searches, first, peak and last refer to observed frames. Step through
the surrounding images or scan a shorter range with interval 1 to refine the
timing. Changing only sensitivity can reuse the last completed measurements
in the open session. Camera recordings are rechecked before cached measurements
are used.

## Interpreting results

A reference image estimates the usual background from frames spread throughout
the recording. The scan scores local deviations and broader brightness changes
inside the ROI, calibrating against ordinary variation in that recording. It
groups neighboring elevated frames into candidates and ranks them by the
strongest observed change. A stationary bubble present throughout a shot can
become part of the reference. Vibration, shadows, or illumination changes can
also produce candidates. Review the source frames to decide what happened.
