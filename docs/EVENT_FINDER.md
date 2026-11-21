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

Connect the camera through the Mac’s Ethernet adapter, then open **Tools → Event Finder → Camera recordings…**.
PCA searches the connected Ethernet interfaces using Phantom’s discovery protocol.
With one camera, its completed RAM recordings appear automatically. With several cameras,
select the camera you want, then select a RAM Cine and click **Open recording**.
[Phantom camera protocol, section 6](https://github.com/DiamondLightSource/ADPhantom/blob/f0dab1241fc236189cc2d124cc97d43985a4eea6/documentation/v16proto-2.3.pdf)

If you plug in a camera after opening the list, refresh the search. A previously used camera
can appear even when it has not answered the current search. Select it to try its last address.
You can also enter an address manually. For a VEO, use the XIP on the label for 10 Gb Ethernet
and the regular IP for Gigabit Ethernet. The Mac’s wired adapter needs its own address on the
corresponding subnet to open recordings. If macOS asks for Local Network access, allow it so PCA can find the camera.
[Phantom VEO manual, chapter 3](https://www.phantomhighspeed.com/-/media/project/ameteksxa/visionresearch/documents/manuals/veomanual.pdf?download=1)

Finish any other image playback or download using the same camera interface before opening a recording.
PH16 allows one image-transfer stream per interface. Leave the selected RAM recording in place until
the scan and frame review finish. PCA reads stored frames and checks the trigger timestamp and stored
state to detect a recording that has been replaced.

## Camera transfer options

On a compatible Mac and Phantom camera, Event Finder selects **High-speed Ethernet · 12-bit (ximg)** for
scanning. XIMG sends packed 12-bit images over the dedicated Ethernet link.
macOS may ask you to authorize access to the camera interface when you start
the scan. Opening the recording and stepping through frames use the standard
connection. For the full precision available over that connection, choose the
16-bit option before starting the scan.

PCA checks the incoming packets for each requested frame. It retries an
incomplete image. If retries fail, PCA reads that frame over standard Ethernet
at matching 12-bit precision. Repeated failures switch the rest of the scan
to the standard connection. The transfer status shows which path is active. Both paths send the complete image for
each requested frame and measure the selected rectangle or point on the Mac.
Choosing a shorter frame range or a larger sampling interval reduces the amount
transferred. Most uncompressed monochrome Cines on disk support reading just
the rectangle. [Phantom camera protocol, sections 5.10 and 5.11](https://github.com/DiamondLightSource/ADPhantom/blob/f0dab1241fc236189cc2d124cc97d43985a4eea6/documentation/v16proto-2.3.pdf)

**Standard · 8-bit** halves image bytes compared with a 16-bit transfer on cameras
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
