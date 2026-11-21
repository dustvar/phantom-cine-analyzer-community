"""User guidance for the PCA adapters; not required by either portable engine."""
USAGE_HTML = """
<h2>Track an object</h2>
<p>Choose <b>Track</b> in the left rail, then <b>Add Object</b> in the Cine title bar.
Choose <b>Classic</b>, <b>Edge Assist</b>, or <b>Tumbling</b>. The setup panel opens only
while adding or editing. Select an existing object in the title bar and press <b>Edit</b>
to reopen its parameters. Click the right-side Tracks button to show or hide results.</p>
<h3>1 · Choose a method</h3>
<ul><li><b>Classic:</b> PCA's original intensity-template tracker. Best for a distinctive point
whose appearance stays similar. Select a search region large enough for movement between frames.</li>
<li><b>Edge Assist:</b> a rigid object with recognizable edges or texture. It reports the
exact point you selected and its in-plane rotation. An object changing its projected shape
as it tumbles is better suited to the next method.</li>
<li><b>Tumbling:</b> a changing outline, reporting the centroid of the visible segmented area.
Adaptive color segmentation with neighboring-frame motion cues is the default for textured or
changing backgrounds. Dark / bright silhouette modes are useful for simpler lighting.</li></ul>
<h3>2 · Target → Region → Search → Review</h3>
<p>The guided strip keeps the video large. First click a distinctive point (recommended),
or choose <b>Draw region · use its center</b> and drag a rectangle. A region center follows
the fitted rectangle; it is not a physical center of mass. Tumbling instead measures the
projected silhouette centroid and requires a foreground seed.</p>
<p>While choosing a point, the <b>8× Point preview</b> shows the detail beneath the
crosshair. <b>Arrow keys</b> move the candidate by one source-image pixel;
<b>Shift + arrows</b> move by 0.1 pixel. <b>Enter</b> confirms the displayed candidate.
The same controls work when choosing a new tracked point or a fixed reference pixel.
Arrows follow screen directions even when zoomed or rotated; they do not change the
frame during point selection. After confirming, normal frame-stepping shortcuts return.</p>
<p>In <b>Tracking region</b>, move or resize the pink box to useful detail. Edge Assist can
rotate it with the round handle. <b>Choose tracked point</b> moves the measured point without
moving the region; the point may be inside or outside it. <b>Use region center</b> measures the
box center and disables Point Lock. For Tumbling, fit the green seed region around the whole
object, then use <b>Settings → Preview mask</b> to check its segmentation.</p>
<p>In <b>Search area</b>, drag a yellow edge to move it, a corner to resize it, or its round
handle to rotate it. The pink region stays fixed. On release the yellow rectangle expands
if needed to contain the entire pink region. Edge Assist carries this search geometry with
changes in the fixture pose. Matching is confined to this oriented region.</p>
<p>A long straight edge cannot reliably reveal motion along its length. For known one-axis
motion, choose <b>Vertical only</b> or <b>Horizontal only</b>. This constrains the actual search
and holds the other measured coordinate at its setup value; it is an explicit motion assumption.
Leave <b>Free X/Y motion</b> selected for general movement. Tumbling uses its own adaptive
search, configured with Max motion / frame and Contour growth in Settings.</p>
<p>Zoom with the wheel and drag empty video space to pan. Box handles take priority over
panning. The <b>Playback</b> button below the image hides/reveals the floating transport to
uncover anything behind it. <b>Settings</b> opens the detailed parameters; <b>Close</b> tucks
them away. Numeric fields require a click or keyboard focus before the wheel edits them.</p>
<h3>3 · Set bounds and confidence</h3>
<p><b>Accept confidence</b> rejects weak measurements. It is a quality score, not a calibrated
probability. A larger value is more selective. For Tumbling, <b>Update confidence</b> controls
when its appearance reference may learn; <b>Appearance learning rate</b> controls how quickly.
Set <b>Max motion / frame</b> to the largest plausible displacement, and <b>Contour growth margin</b>
to cover changes in outline. Excessively large limits invite background confusion.</p>
<p><b>Silhouette threshold</b> applies only to Dark/Bright mode and ranges from background
brightness toward the selected foreground brightness. Adaptive mode instead uses color
segmentation and its acceptance/update confidence thresholds.</p>
<p>Choose the first/last source frames. Tumbling's range must include its setup frame and runs
independently forward and backward. Edge Assist's automatic start/stop (formerly
<b>Process Smart Range</b>) searches both directions until repeated weak matches; searches within the frame range set in Review.</p>
<h3>4 · Track, inspect, save</h3>
<p>Press <b>Complete &amp; Track</b> on Review. Progress and <b>Cancel tracking</b> remain visible; cancelling processing keeps measurements completed so far.
Cancelling setup removes a new draft or restores the prior object when editing. Open the side <b>Tracks</b> drawer for point views, objects and coordinates. Scrub through the footage to
inspect the mask and trajectory. Tumbling retains rejection diagnostics but does not add
estimated or interpolated coordinates for rejected frames. Its angle column is a dash.
Change its setup parameters and rerun to replace measurements. A preview alone does not
change saved coordinates.</p>
<p>Use <b>Save Job</b> to retain tracks, masks, settings, calibration and workspace state.
The job references its source media; keep the original files available when reopening.</p>
<h3>Displacement reference and heat maps</h3>
<p>Select an object, then <b>Reference</b> (or <b>Ref</b> in a small pane). Choose its first
tracked point, its position at a specific displayed Cine frame, or a fixed image pixel.
For a fixed pixel, enter X and Y or press <b>Pick on video</b>, click the image, then Apply.
Image coordinates start at the top-left: X increases rightward and Y downward. The pixel
stays constant in every frame. A frame reference must contain a tracked measurement.</p>
<p><b>Adjust → Rotate video</b> turns the display left, right or upside down. Tracking
and reference coordinates still use the original sensor axes; display rotation does not
change saved measured positions. Rotation is retained in jobs and rendered exports.</p>
<p>In <b>Path Settings</b>, enable the heat map and choose X-Displacement or Y-Displacement.
Values are signed offsets from the reference. Each object's lowest value is green and highest
is red. Colors are relative to that object's range. <b>Plot</b> shows or hides graphs and
point previews; in small panes it is in the <b>…</b> menu.</p>
<p>The playback FPS control changes viewing speed; analysis still uses capture timestamps.
In Viewer it changes the selected segment's playback speed, which is also used by export.</p>
"""

TECHNICAL_HTML = """
<h2>How the tracking works</h2>
<h3>Classic · PCA intensity tracking</h3>
<p>Classic searches for normalized intensity-template correlation in a user-bounded region.
The native-pixel search identifies candidate peaks; small neighborhoods around them are then
refined at the selected subpixel spacing. This avoids enlarging the entire search area by
100 times for a 1/10-pixel setting. Linear through quintic interpolation describes the local
sampling order. The selected increment is numerical resolution, not guaranteed accuracy.</p>
<p>Template updates retain the original confidence rule. The reference patch is reused until
it changes, avoiding a backward disk read to the setup frame for every processed image.
Positions are measured at source pixel centers, including a fractionally selected point.
Classic remains part of PCA and is not included with the independent engines.</p>
<h3>Edge Assist · fixed geometry and neighboring references</h3>
<p>The selected reinforcement region represents a rigid fixture. Candidate translations and
in-plane rotations are evaluated with normalized intensity correlation and soft edge geometry.
The edge weight balances these cues. Confidence compares the adjacent accepted frame with
the original setup reference, limiting drift. The selected point's fixed offset is transformed
with the fitted pose. Optional Point Lock refines the local feature where sufficiently distinct.
Subpixel and angular output increments describe numerical resolution, not guaranteed accuracy.</p>
<p>The PCA adapter retains its existing Smart Range/candidate filtering behavior. The portable
module also provides a sequence API for independent applications. No PCA, Qt, SciPy or
scikit-image import is required by the Edge Assist engine.</p>
<h3>Tumbling · adaptive silhouette centroid</h3>
<ol><li><b>Initialize:</b> an interior seed and a bounding region identify foreground and
surrounding background. OpenCV GrabCut models their color distributions and segments a mask.
The alternative Dark/Bright modes threshold relative to local background and seed brightness.</li>
<li><b>Predict:</b> the last accepted motion predicts the next region. Lucas–Kanade optical
flow on foreground features, checked in both directions, improves the motion proposal when
features remain usable. A bounded search and contour-growth margin limit exploration.</li>
<li><b>Segment:</b> the prior silhouette is shifted into the new frame. A small interior core
anchors foreground while the boundary can grow or shrink. Local color segmentation is recomputed,
so the whole outline need not resemble the original template.</li>
<li><b>Validate:</b> connected candidates are checked for plausible movement, area change,
appearance similarity, prior-mask overlap and clipping. A heuristic score combines motion
(35%), area (20%), appearance (30%) and overlap (15%). Near-tied plausible candidates are rejected.
Low confidence yields a diagnostic, not a trajectory coordinate.</li>
<li><b>Adapt:</b> accepted geometry and motion become the next reference. Appearance adapts
only above both the acceptance and update thresholds, at the chosen learning rate.
Repeated misses stop that direction. Forward and backward passes have independent state.</li>
<li><b>Measure:</b> for foreground pixels (xᵢ,yᵢ), the projected area centroid is
(x̄,ȳ) = (Σxᵢ/N, Σyᵢ/N). PCA converts these image coordinates using the existing calibration and
trajectory calculations. No angle is inferred for changing silhouettes.</li></ol>
<h3>Motion calculations</h3>
<p>Displacement is (x − x₀, y − y₀); total displacement is its Euclidean length.
With a fixed pixel or frame reference, velocity uses signed coordinate differences divided
by elapsed capture time, regardless of that displacement zero. Taking absolute displacement
before differencing would create false speed dips when crossing the reference, so it is
not used. <b>Raw differences</b> computes acceleration from velocity differences divided by
the interval between their midpoint times. Because this second derivative amplifies pixel jitter,
<b>Local fit</b> is the default: a cubic least-squares position fit over 21 neighboring measured
samples gives its first and second time derivatives. Change the odd window (5–101 samples) or
select Raw differences below the graph. Fits use actual capture times and do not bridge missing
positions, nonpositive intervals, or gaps exceeding three ordinary sampling intervals.
Larger windows reduce noise but can attenuate brief peaks and sharp changes; no window is
universally correct. Raw tracked positions and displacement are unchanged. Acceleration is a
magnitude; <b>Y-Acceleration</b> shows signed vertical acceleration for an oscillating object.</p>
<p>These measurement references are separate from the neighboring-frame appearance references
used by the tracking algorithms. Changing displacement zero does not retrack footage or change
the selected point. Previously saved jobs recalculate motion from their measured points on open.</p>
<h3>Interpretation and limits</h3>
<p>The centroid is <b>not a physical center of mass</b>. Rotation, self-occlusion and perspective
can move the visible centroid even when the true center of mass follows a smooth path.
Texture/color camouflage, abrupt illumination changes, blur, occlusion, clipping and multiple
similar crossing objects can defeat this method. The initial mask must be checked. Rejection
reduces false measurements; it cannot guarantee correctness or recover invisible surfaces.
No single RGB segmentation method is reliable on every changing background.</p>
<p>These are custom tracking pipelines using <b>NumPy</b> and <b>OpenCV</b> numerical/image
primitives, including GrabCut and optical flow; they are not entirely from-scratch image-processing
libraries. They use no downloaded model weights, cloud service or neural network.
The portable bundle includes dependency versions, upstream license links and notices.
PCA's UI/media/graph dependencies are separate from those two engines.</p>
"""
