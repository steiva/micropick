# micropick design notes

Why the code is shaped the way it is. Written so that a new working session, or
a new person, can start from two pages instead of rediscovering the reasoning.

Companion to the README, which says what the project does. This says why.

---

## 1. Relationship to the old repository

`ot2-microtissue-manipulator` still works and is kept for reference. It is not
being migrated in place; this is a rewrite, and the two share no configuration.

The old repository's real problem was not its structure but its location: the
picking state machine, the cuboid detection, the floater check and the
recorders all lived in notebook cells. They could not be imported, tested,
reviewed or reused, and every fix existed in exactly one `.ipynb`.

Profiles are deliberately not convertible. The old calibration was an affine
transform with no distortion term, fitted from four points inside a 10 mm
square and then applied across the whole frame. Any automatic migration would
produce a plausible-looking `pixel_map` that was wrong, so `store.load_profile`
detects the old format by its keys (`tf_mtx`, `one_d_ratio`,
`size_conversion_ratio`) and refuses with an explanation.

---

## 2. Layering

```
paths            filesystem roots, nothing else
  |
config           schema (pydantic models) and store (disk I/O)
  |
core             maths and vision. No hardware, no cv2 windows, no I/O
  |
hardware         devices behind protocols, plus mocks
  |
workflows        orchestration: uses core and hardware together
  |
notebook / GUI   loops, windows, buttons, keyboard
```

One rule, enforced by discipline rather than tooling: **`core` never imports
`hardware`, and `hardware` never imports `core`.** Everything above depends on
both. The consequence worth protecting is that all of the maths and all of the
vision can be exercised with no robot attached, which is how most of the bugs
in this codebase were found.

The second rule is that **nothing below the notebook opens a window or reads a
key.** Long operations take `on_progress`, `on_frame` and a cancellation event.
They stay synchronous and blocking: a notebook calls them directly, a GUI runs
them in a thread. Making the core async would serve the GUI and hurt the
notebook, which is the interface actually in use.

Two modules stand outside that rule and always have. `workflows/jog` drives the
robot from a window's own key events, which is the point of it: keys read that
way need no elevated permissions and cannot reach the robot while another
application has focus. `viz/window` holds the one thing both it and the notebook
need — a window sized to the aspect of the frame in it. Neither is imported by
`core`, and both import `cv2` inside their functions, so the package still
loads where there is no display at all.

---

## 3. Calibration

### One sweep instead of a chessboard

Lens distortion and the camera-to-robot relationship are fitted together from a
single robot-driven sweep of a static ArUco marker. There are no camera
intrinsics, no `undistort` stage, and frames are used raw.

This is not a shortcut. The optics change often here — the focus ring gets
turned, the working distance changes with the dish — and every change
invalidates intrinsics fitted with a chessboard weeks earlier. A sweep takes a
few minutes, runs unattended, and produces exactly the mapping that is used.

### Geometry: the camera moves, the scene does not

The camera rides on the gantry, so there is no fixed pixel-to-deck mapping: the
same pixel means a different place depending on where the gantry is. What is
invariant is the offset from the gantry to whatever sits under a pixel:

```
deck_point = gantry_pose_at_capture + f(pixel)
```

`f` is normalised to zero at a reference pixel, so it describes optics alone.
The constant relating that reference pixel to the pipette tip is a separate
calibration and is added by the caller. Keeping the two apart means a new tip
changes one 2-vector and nothing else.

The gantry pose must be read next to the frame the pixel came from. The pose is
part of the conversion, not a correction applied afterwards.

### Degree 3 is the minimum

Radial distortion displaces a point by `x · k1 · r²`, which is **cubic** in
image coordinates. A quadratic polynomial therefore cannot represent it at all
and reduces exactly to an affine fit.

This was measured, not assumed. On the first real sweep, degrees 1 and 2 gave
identical numbers to four decimal places, and degree 3 improved held-out error
roughly twelve-fold. Degree 4 does not help and is worse at the extremes.

`fit_pixel_map` refuses degrees below 3 rather than silently producing a fit
that buys nothing.

The identical numbers at degrees 1 and 2 are also a useful diagnostic: if they
ever differ, something other than lens distortion is present in the data.

### Four corners, not one centre

Every detected marker corner is its own fixed deck point. Fitting all four
jointly gives four times the data, and — more importantly — the corners reach
half a marker further toward the frame edges than the centre can, which is
where distortion is strongest.

Each corner has an unknown deck position. Those enter the least-squares problem
as one free constant per track, so the fit stays linear.

Practical effect: with a 6.8 mm marker at ~26 µm/px, centre-only tracking
covered about 85 % × 83 % of the frame; adding the corners took it to 94 % × 95 %.

### The sweep measures its own extent

Small probe moves give millimetres per pixel; the marker's own detected corners
give its size in pixels. From those two the largest excursion that keeps the
whole marker in frame follows directly. Nothing about the lens, the marker or
the resolution is hard-coded, so changing any of them needs no edits.

### Validation without a ruler

Three checks, all available on real data:

- **Residual** is the spread of a fixed deck point recovered from many
  different gantry poses. It needs no ground truth and includes the robot's
  repeatability, which is not separable from the map anyway.
- **Held-out error** uses leave-poses-out cross validation. Whole poses are
  held out, never individual corners: corners within one pose share a gantry
  reading and are correlated, so splitting inside a pose reports an optimistic
  score.
- **Recovered marker side** should match the printed size. The fit never uses
  it, so agreement is independent evidence. On the first real sweep this came
  out at 6.7723 mm against a nominal 6.8 mm, a 0.4 % disagreement consistent
  with print scaling — while degrees 1 and 2 gave 6.7127 mm, off by 1.3 %.

### Numbers from the first real sweep

25 poses, marker centres only, 2592×1944, field of view ≈ 68 × 51 mm:

| model | held-out mean / max |
|---|---|
| affine (the old method) | 213 / 946 µm |
| homography | 213 / 946 µm |
| degree 2 | 226 / 1047 µm |
| **degree 3** | **24.2 / 78.6 µm** |
| degree 4 | 24.5 / 92.9 µm |

Local scale varied from 25.97 µm/px at the centre to 28.76 µm/px at the edge,
**10.8 %**. That is why `mm_per_px(u, v)` replaced the global
`one_d_ratio` and `size_conversion_ratio`: a single number misreports object
sizes near the dish edge by about that much.

### Pipette tip calibration

Upper camera locates the crosshair disc and the pixel map turns it into robot
coordinates. The gantry drives there using the current offset estimate, parked
a few millimetres to one side so the tip does not hide the crosshair. The lower
camera then measures the residual between tip and crosshair, using the known
20.25 mm radius to the four outer crosshairs for scale.

The offset is read from the **final pose**, not accumulated from commanded
moves, so the approach shift, the automatic correction and any manual nudge all
cancel without being tracked separately.

Two things worth knowing:

- The central crosshair is identified by pattern symmetry — the one whose four
  nearest neighbours sit at equal radii in opposing directions — not by
  proximity to the frame centre. The old rule failed as soon as the disc was a
  few millimetres off centre, and failed by silently returning an outer
  crosshair.
- The lower camera's orientation lives in the profile as a 2×2 matrix,
  defaulting to `[[0,1],[1,0]]`. Its determinant is **−1**: the camera is
  rotated ninety degrees *and* views from the opposite side, so the transform
  is a rotation combined with a mirror. A determinant of +1 there would mean
  the mirror had been forgotten. This is fixed by the module geometry and
  should never need editing; it is data rather than code because in the old
  version it was two lines with the variable names crossed over, where it was
  invisible.

On a fresh installation the offset must be entered by hand first, measured with
a ruler. The routine drives to where it believes the target is before looking,
so an offset wrong by tens of millimetres puts the tip outside the lower
camera's view with nothing to recover from.

The offset and the upper-to-lower homography below are written to the profile by
the routine itself, together. They come out of the same measurement, and while
the homography was saved by a separate step it was easy to skip, which left a
profile holding a fresh offset beside a matrix from an older tip and an older
pose.

### Upper-to-lower homography

Both cameras see the same disc during the tip calibration, so the camera-to-camera
map is free. Its one job is to draw a box around the chosen cuboid on the
lower-camera clip: it holds only for the marker plane and only near the gantry
pose it was fitted at, so it is a viewing aid and never a way to position the
robot.

**Pixels belong to a camera mode.** The tip calibration runs the lower camera at
4000×3000 for precision; the clip records at 2000×1500. A matrix fitted in the
first and applied in the second puts every point at twice its coordinate, off the
frame, and the box simply never appeared — for a whole run of the machine,
without a word, because nothing recorded which mode the numbers meant. Both
resolutions are stored with the matrix now and it is rescaled to the modes in
use. That works because these two modes share a field of view; a mode that crops
the sensor instead scales the axes unequally, which is detected and refused.

**Correspondence comes from the known orientation.** The crosshairs are
identical, so the four outer ones are matched by angle about the disc centre,
using the fact that the lower camera is turned ninety degrees from the upper one
without mirroring: measured as `atan2(dv, du)` with v downwards, that is
`θ_under = θ_over + 90`. The old code matched through `TipTarget.axes` — a
pixels-to-millimetres map carrying a mirror of its own — and trusted the
reprojection error to catch a bad match. It cannot: five points against eight
degrees of freedom fit almost anything, so a correspondence off by ninety
degrees produced a near-zero residual and a box on the wrong cuboid.

**Four points fit, the centre checks.** Four points determine a homography
exactly, so their residual is zero whatever the correspondence and measures
nothing. The central crosshair is held out and reprojected; its error is the only
honest number. The matrix is also decomposed and its determinant and rotation
reported.

**What none of it can prove.** The disc is four-fold symmetric and the fifth
point sits at its centre of symmetry, so no arithmetic on these five points can
reveal a wrong assumed rotation or a mirror: ask for the wrong rotation and the
matching pairs each crosshair with a different neighbour, the fit is exact again,
the held-out centre still lands on the centre, and the decomposition reports back
the rotation that was assumed. That is what symmetry means, not a threshold to be
tightened. The orientation is therefore a hardware fact in the profile
(`TipTarget.under_rotation_deg`), and what confirms it is the first clip: the box
is on the chosen cuboid or it is not. The checks catch everything else — a
mis-detected crosshair, a degenerate arrangement, a disc that moved between the
two views, and the wrong camera mode.

**Pose.** The upper camera rides on the gantry, so the map goes stale as the pose
moves away from where it was fitted. The distance is reported and warned about
past a millimetre, not refused: it used to return nothing at all past two
millimetres, which is the second way the box could vanish in silence, and the
poses involved (`tip_calib`, `observe`) are taught separately and rarely agree
to within that.

---

## 4. Robot

### Verified moves

The Opentrons wrapper only raises on an HTTP error. The robot answers `201` to
a command it then declines to execute, with the reason inside the body, so a
refused move is completely silent when `verbose=False`.

Every move therefore goes through `protocols.move_to`, `move_relative` or
`goto_xy`, which command, read the pose back, and raise `MoveFailed` if the
robot did not arrive.

This was not hypothetical. A real session lost seven probe cycles to a robot
that accepted every command and did not move.

### Relative XY travel

The camera sits at a fixed height, so a calibration sweep is pure XY motion.
`goto_xy` expresses that as per-axis relative moves and never names a Z.

Two reasons. First, with a long tip fitted the robot refuses
`moveToCoordinates` at the height the tip already sits at, while relative moves
at the same height work normally — supplying a Z the caller does not care about
is what triggers the refusal. Second, the step for each axis is computed from
the pose read back rather than from the plan, so a short move is absorbed by
the next one instead of shifting everything after it. Axes already in position
are skipped, which roughly halves the traffic on a raster.

### Backlash

Every pose is approached travelling in the same direction, so lost motion is
identical everywhere. Only an axis whose step would be negative takes a detour
below the target first.

Doing this unconditionally, as the first version did, meant a one-millimetre
detour on **both** axes at every pose. In a raster where one axis usually does
not move at all that was most of the motion, and it left the gantry ringing
when the picture was taken.

### Soft limits let the robot escape

A jog limit is judged by whether a move makes the excursion worse, not by
whether the target is inside the box. Outside the box, moving back toward it is
always allowed; moving further out never is.

The alternative — refusing any target outside the box — is correct while the
robot is inside and useless once it is not. After homing, or after a routine
parks the gantry, every direction is refused and the only way out is to disable
the guard.

Clamping to the boundary lands microscopically outside through floating-point
subtraction, so `Limits` carries a `tol` of 1e-6. Without it the axis reports
itself out of bounds and refuses the next step in that direction.

---

## 5. Camera

### One thread, not one process

A 2592×1944 frame is 15.1 MB. The old design moved every frame through a
lock-protected shared array, costing about **7.7 ms per frame** in pure memcpy
— roughly a quarter of a core at 30 fps, and 1.36 GB/s of memory bandwidth. A
thread hands the frame over by reference in 0.13 µs.

The usual argument for a process is the GIL, but `cv2.VideoCapture.read`
releases it while blocking, so a thread does not hold up the notebook either.
Process isolation from a hung driver is real but is paid for continuously and
collected about once a month.

### Frames carry a timestamp

`read()` returns the most recent frame, which may predate whatever the caller
just did. `read_after(t)` returns the first frame pulled from the driver after
`t`, which is what any measurement following a move must use.

The stamp is taken *before* `cap.read()`, so the guarantee is "pulled from the
driver no earlier than t". That is not the same as the exposure time; a driver
that queues frames can still return one exposed slightly earlier, hence the
`skip` parameter.

### Settling is measured, not waited out

`MarkerTracker.measure` keeps grabbing until several consecutive readings of
the marker agree within a threshold, rather than sampling a fixed number of
frames after a guessed pause.

A gantry settles in a time that depends on the move, the load and the day. The
fixed-pause version skipped 46 of 49 poses on the bench because 0.15 s was not
enough; measuring stillness directly is both faster on short moves and reliable
on long ones, and removes the one tuning parameter that decided whether the
whole sweep succeeded.

### Consumers subscribe, they do not poll

A recorder attaches to the camera's single grab loop and is pushed frames. Two
loops calling `read()` on one device compete and each sees part of the stream.

The two recorder classes in the old notebook differed only in a per-frame
transform (crop and greyscale, versus greyscale) and an overlay (an ROI box,
versus none). Both are now callables passed to one class. The buffer is always
bounded: the unbounded variant filled memory in under a minute at 5 MB per
greyscale frame.

### Fail loudly

If the device reports a different resolution than requested, the camera raises
instead of resizing. A silent scale change invalidates the pixel map with no
other symptom.

Camera controls are set and then read back, because UVC drivers accept a `set`
and ignore it. `ControlReport` says which ones actually took effect. The
auto-exposure candidate list deliberately excludes `0`: many drivers report `0`
for a property they do not support, which would make the read-back check pass
on a control that was silently ignored.

**Known unresolved:** on the bench, the 20MP U3 camera accepts neither
`CAP_PROP_AUTO_EXPOSURE` nor `CAP_PROP_EXPOSURE` through the default backend.
Worth retrying with `cv2.CAP_DSHOW`, and `CameraSpec` has a `backend` field
ready for it. Auto-exposure hunting when the gantry moves from a bright area to
a dark one is the symptom to watch for.

### The crop is a view, not a frame

Most of the lower camera's field is empty: the disc and the dish sit in the
middle. `CameraSpec.crop` says what fraction of it is worth looking at — 0.5 for
that camera, a centred square of half the width, the same crop the old code used.

It applies where a person looks and nowhere else: the clip recorder, the jog
window, a preview. `read()` keeps handing out whole sensor frames, so detection,
the pixel map and the homography are all fitted on what the sensor actually
produced. The old version cropped inside the grab loop, which meant every
coordinate downstream was in a frame whose origin was 500 px from the sensor's,
and the two conventions were told apart only by remembering.

Because of that split there is exactly one place where the crop meets geometry:
a point computed in sensor pixels and drawn on a cropped frame is shifted by the
crop origin, which `center_crop_box` returns without needing a frame in hand.

**The origin and the crop come from one call, because they were two and they
disagreed.** `center_crop_box` answers with the centred *square* whatever it is
asked, so at a crop of 1.0 it returns an origin of (250, 0) for a 2000×1500
frame — the offset of a square that nobody cut. The session subtracted that
origin from every ROI point while attaching the cropping transform only when the
crop was not 1.0, and the profile, as written, asked for no crop at all. So every
box was drawn 250 px — an eighth of the width — to the left of its cuboid, on a
clip recorded whole. Measured on the bench recordings: the boxes sat 251 px
(12.0 mm) from where the tip came down, the same distance in every clip, with no
rotation and no growth toward the edges, while the matrix's own held-out error
was 3 px. A pure translation is a shift of origin, never a bad matrix, and that
is what it turned out to be. `_clip_view` now returns the origin, the recorded
size and the transform together, so a crop of 1.0 says "whole frame" once
instead of being guarded for separately at each end. After the fix the same
recordings put the box 15 px (0.7 mm) from the tip, every box on the tip.

---

## 6. Configuration

A profile is one physical installation. Files inside it are split by **what
writes them**, not by topic, so re-running a calibration cannot clobber
hand-edited picking parameters:

```
profiles/<name>/
    profile.json       metadata and schema version, read first
    cameras.json       device names, resolutions, controls
    calibration.json   pixel map and pipette offset
    picking.json       picking parameters
    positions.json     taught deck landmarks
    history/           timestamped copies of previous calibrations
```

Decisions worth keeping:

- **Version on the profile, checked first.** An unknown `schema_version` is an
  error, not something to guess at.
- **`extra="forbid"` on every model.** A typo in a key used to be ignored
  silently, and the parameter then ran at its default. For a pickup height that
  is a crash, discovered later and blamed on the algorithm.
- **Atomic writes.** Content goes to a temporary file and is renamed over the
  target. A half-written calibration would otherwise be loaded on the next run
  and quietly misdirect the robot.
- **Calibrations are archived before being overwritten.** A sweep costs minutes
  of robot time; if today's is worse than yesterday's there should be something
  to go back to.
- **Device indices are never stored.** They move between reboots and USB ports;
  device names do not. The index is resolved at open time.
- **Camera controls live in the profile and are re-applied on every open.** That
  is what keeps a pixel map valid across restarts, since the map is only correct
  for the focus it was fitted at.
- **Derived values are not stored.** `pickup_height` is a property computed from
  `dish_bottom + pickup_offset`. As a dataclass field its default was evaluated
  once at class definition and never tracked `dish_bottom` again.

---

## 7. Routine and destinations

`Routine` holds the plan, the progress and the ordering. It writes to disk after
every recorded attempt, so a run interrupted to adjust a parameter resumes where
it stopped even across a kernel restart — previously progress lived only in the
object.

`record(delivered=, missed=)` counts objects rather than attempts. The old
boolean could not describe a batch that ended with some of both.

`least_filled` recomputes the order on every call from actual progress. The old
`spread_out` sorted by the *planned* count, producing a fixed order that spread
nothing.

### Destinations are built from a definition, not a size

A plate `Destination` comes from a resolved labware definition
(`config.labware`): the well names are its `wells` and the fill order is its
`ordering`. There is no plate-size input, so there is nothing to disagree with
what is loaded. The old code took a number, looked its shape up in a preset
table and generated well names itself; declaring 384 with a 96 loaded then
worked until the first well past the real plate and failed mid-routine. The
preset table, the base-26 label generation and the 1536 format are gone; a plate
this rig cannot describe with a definition is not a plate it runs.

Orderings come from the definition too: `by_column` is the file's own order,
`by_row` its transpose. Nothing parses `int(well[1:])` (which broke on
multi-character rows) or leans on sort stability. The planning grid and the
progress table take their row and column labels from the ordering, so the table
is shaped like the real plate, custom plates included.

### Changing the plate between runs

Labware in a slot can change between runs but never during one, and the two ways
it changes need different handling:

- **A different format** (96 → 384). The operator tells the robot with
  `move_labware` that the slot is now empty and loads the new plate; the run
  state changes. `loaded_labware` reads that state, and a routine's
  `check_labware` refuses when the slot holds a different definition, or is
  empty. This is machine-checkable and enforced automatically before the first
  move.

- **The same format, a new plate.** The operator just swaps the plate; no
  command is issued, and this is **indistinguishable in software** from carrying
  on with the same plate. It is the dangerous case: a routine restored from disk
  would refill a fresh plate from the middle.

The decision: a routine has its own identity — an operator-set `name` (required
for a plate) plus a generated `run_id` and `created_at`, stored in the progress
file — and **resuming is a deliberate act**. `Routine.load` marks a file that
carries progress as needing confirmation; a session refuses to start it until
the operator, having seen `summary()` (name, run, how many wells are already
filled, the next well, the plate), calls `confirm_resume()`. A genuinely new
plate means a **new routine** (new file, new `run_id`), which the summary makes
obvious.

Two weaker options were rejected. Auto-resuming whenever the definition matches
is exactly the refill-a-new-plate bug. Trusting the run's `labware_id` does not
help either: it changes when labware is reloaded or moved but not when a plate is
physically swapped, so it is neither necessary nor sufficient. Splitting the
problem — enforce the part a machine can check (slot and definition), make the
part it cannot (this physical plate) a conscious confirmation gated on a visible
summary — is the honest division. `check_labware` says so in its docstring: it
verifies the definition in the slot, not the identity of the plate, and is not a
guarantee.

---

## 8. Testing

There is no test suite in the repository yet; everything so far was verified
with throwaway scripts. **This is the largest outstanding gap.**

What the mocks make possible, and what should become the suite:

- `MockRobot` reports the pose it reached with a small error, never the pose
  commanded. A mock that echoed the command would hide the difference until it
  appeared on the bench as an occasional bad calibration point. It can also
  simulate backlash and the silent refusal at the homed height.
- `MarkerScene` renders a real ArUco marker through a known perspective and a
  known radial distortion, so `cv2.aruco` runs on real pixels and the fitted map
  can be compared against the optics that produced the frames.
- The first real sweep should be saved to `tests/fixtures/` and become a
  regression test: any change to the fitting maths must still reproduce roughly
  24 µm held-out error on that data.
- The whole picking state machine runs headless. It needs no YOLO: the detector
  is only ever called through `vision.detect_boxes`, so anything with a
  `predict()` returning boxes and confidences will do, and a synthetic dish whose
  objects are removed on a pickup and left in place on a miss drives every
  branch — empty pickup, partial miss, shake, operator hand-back — in
  milliseconds. That is how the transitions above were checked.

Bugs these caught before they reached hardware: a `pickup_height` that
serialised but would not load back; `read()` returning `False` for the first
milliseconds after a camera opened; a clamped axis trapping itself through
floating-point error; a `mm_per_px` that returned only its x component.

---

## 9. Hardware quirks

Things that cost time to discover.

- **The robot refuses absolute moves at the height a long tip already sits at**
  (≈118 mm in the current setup) while accepting relative moves. It answers
  success either way. Use `goto_xy` for XY travel.
- **`move_to_coordinates`, `move_relative` and `get_position` all require both
  a run and a loaded pipette.** Nothing that moves the robot works before
  `create_run()` and `load_pipette()`.
- **`get_position` returns `(coordinates, response)`**, hence the `[0]`
  everywhere. Read it by key, not by `.values()`: the unpacking order depends on
  how the wrapper happens to build its dictionary, and a future reordering would
  silently swap axes.
- **Custom labware must be uploaded into every run**, after each `create_run`.
- **Marker size limits the sweep.** A marker's centre cannot approach a frame
  edge closer than half its extent. An 11 mm marker at 26 µm/px covers 418 px
  and costs about 6 % of frame coverage against a 6.8 mm one.

---

## 10. State of the code

Written and exercised on mocks; both calibrations have run on the bench.

| module | what it does |
|---|---|
| `paths` | filesystem roots, created on demand for outputs only |
| `config/schema` | profile models with validation |
| `config/store` | atomic profile I/O, legacy rejection, history |
| `core/calibration/pixel_map` | fitting and applying the map |
| `core/routine` | destinations, plans, progress, persistence |
| `hardware/protocols` | `Camera` and `Robot`, verified move helpers |
| `hardware/devices` | camera enumeration, lookup by name |
| `hardware/camera` | background capture, recorder, manager |
| `hardware/labware` | custom definitions, upload, load |
| `hardware/mock` | robot, camera, and a synthetic ArUco scene |
| `core/vision/cuboids` | detection, per-box Otsu, shape filters, rejection labels |
| `core/vision/bubbles` | separating a bubble from a solid on two optical features |
| `core/vision/floaters` | centroid motion against a measured noise floor |
| `workflows/calibrate_camera` | probe, plan, sweep, fit |
| `workflows/calibrate_pipette` | tip offset against the crosshair disc |
| `workflows/jog` | manual control, two input backends |
| `workflows/picking` | the pick-and-place state machine, one step at a time |
| `viz/overlays` | drawing for the picking window, frame in, frame out |
| `viz/window` | one window, sized to the aspect of the frame it shows |
| `notebooks/01_robot_session.ipynb` | the whole session in one place |

---

## 11. The picking session

Written (`workflows/picking.py`), with the vision it uses (`core/vision/`), the
drawing (`viz/overlays.py`) and the notebook wrapper that supplies the loop, the
keyboard and the window. What the port fixed in the old FSM — two sources of
truth for the current well, a global `routine`, an endless
`ANALYZE → AUTO_SHAKE → CAPTURE` cycle, hard-coded shake coordinates, a silent
batch cap, YOLO running in a tight loop while idle — is listed in the module
docstring, next to the code that fixes it.

**The session has no loop of its own.** `step()` performs one transition and
returns an event; the caller holds the loop, the pause, the stop and the window.
Pause and stop arrive as `threading.Event` and are checked between individual
robot moves, not only between states, so a pause during a five-cuboid pickup
takes effect at once.

Four rules were added after the first bench runs, all of them for the same
reason: the machine was doing work that the situation did not call for.

**The volume dispensed and the trip to the well both follow the count actually
held.** A partial miss keeps the successful cuboids and returns only the missed
volume, so the concentration per well stays constant (`miss_policy`,
`keep_successful` / `return_all`). The case that caught us on the bench is the
total miss: with nothing in the tip the session still drove to the plate and
dispensed 0 µl, because the transfer was decided from the count aimed at. It is
now decided from `held`, and `max_empty_pickups` empty pickups in a row hand back
to the operator — the same class of endless loop as the shake retries.

**A run begins idle and stays there until told to start.** `start()`, or
`resume()` under the name the operator already knows, is the only way out of
IDLE; the state blocks inside `step()` rather than returning an event per poll,
so a caller looping on `step()` does not spin. Which key means "go" stays in the
notebook: the session only knows whether it has been told.

**`NEEDS_OPERATOR` is idle with a different reason for waiting**, so it is the
same state in everything that shows: it retracts, returns to the observation
pose, shows a live picture and blocks on the same go-ahead. It used to return an
event per call instead, which spun the caller and, because it never reached
`_gate`, meant a stop raised there was never seen — the run could only be ended
by first resuming it. Neither waiting state assigns the next state from
`resume()`: the transition happens inside `step()` like every other one, so a key
pressed on the display thread cannot move the session while the worker is
mid-state.

**`DETECT_FLOATERS` is the head of the cycle**, not a stage after the frame is
taken. What floats is measured first and the decision frame is taken after,
rather than seconds before. `core/vision/floaters.py` runs there: one detection
pass, then `floater_window_s` of frames reduced to three numbers per object per
frame, scored against the profile's baseline. The schedule is a moment in time,
not a count of cycles — a cycle lasts however long the last transfer took, while
a floater covers `minimum_distance` in a measured 15 s — and anything that stirs
the dish clears the stamp, so a shake or the operator's hands make the next
measurement due immediately. `floater_mode` chooses whether the answer is only
recorded (`observe`, which is how the threshold earns trust on a real dish) or
also kept out of the candidates (`enforce`); a missing baseline or pixel map is
refused when the session is constructed, rather than after the gantry has parked.

A measurement that cannot be believed yields no exclusion at all, in either
mode. It is the absence of information and not an instruction to discard
everything: flagging the whole dish empties the candidate table, exhausts the
shake retries and hangs the run, while a floater let through costs one empty
pickup that `verify_pickup` already catches. For the same reason the frames of a
clip interrupted by a pause are thrown away rather than scored short, and the
zones of the previous reading are dropped rather than carried past the horizon
they were sized for. Objects that could not be measured at all are held out of
the candidates without being counted as floaters.

**The display mode is a property of the state, not of the window, and the
picture travels with the event.** Every `PickEvent` carries a `PickView`: either
"read the camera", in the three states where the operator is watching the dish
itself (`IDLE`, `NEEDS_OPERATOR` and the floater measurement, where the movement
is the whole point), or the frame a decision was made from together with the overlays
measured on *that* frame. A live stream during travel shows motion and says
nothing, so in the other states it does not run at all.

Handing the picture over rather than announcing a mode is what makes the held
frame possible. A caller that reads the camera itself and draws the session's
tables on top cannot hold anything: it necessarily draws contours over a newer
picture than the one they were measured on, which is what the first bench runs
showed. The rule for the caller is now one branch — show what you were given,
touch the camera only when told to.

Three states hand over a picture: `CAPTURE_FRAME`, the bare frame it just took,
with the previous cycle's tables dropped so nothing stale is drawn on it;
`ANALYZE_FRAME`, the same frame with the detections and a white box on the batch
chosen, after the choice so it is in the picture; and `VERIFY_PICKUP`, the new
frame with the circle inside which a surviving detection means the cuboid never
left. The radius is `verify_radius_px`, the same number `_count_misses` compares
against, so what is drawn is what is decided. Everywhere else the last of those
stays up, unchanged, until the next one replaces it.

**The observation pose and the rail light belong to the session.** Both are
preparation for photographing the dish rather than steps an operator takes, so a
GUI would otherwise have to repeat what the notebook does today, and a light left
on puts highlights on the meniscus and shifts every threshold the detector was
tuned at. The pose comes from `profile.where("observe")` like every other deck
landmark, and it is needed on the way out of `NEEDS_OPERATOR` as much as at the
start — two copies of that move would be two things to keep in step. The light
goes out once the run is certain to happen, after the preflight, and `close()`
puts back the state that was found. A caller that leaves the window while the run
continues does not call `close()`, and should not: the light belongs to the run,
not to the window.

### Bubbles, and why rejection is never silent

Pipetting leaves air bubbles in the dish, YOLO boxes one as readily as a
microtissue, and no shape window can help: a bubble is round, convex and
symmetric, so it passes `circularity`, `solidity` and `radial_cv`. The separation
is optical instead, on two features read off the contour Otsu already produces —
a dark core against a bright rim, and a caustic. `core/vision/bubbles.py` has the
physics.

**The features are measured on every detection, whether or not filtering is on.**
The thresholds come from four bubble and four cuboid crops, and on `spec_ratio`
the gap is 13%: cuboids reach 1.41 against a threshold of 1.60. Eight samples do
not fix a threshold, and the only way to replace them with numbers from this
microscope is to accumulate features across real runs — for the objects that were
kept as well as the ones that were dropped. Measuring only when the filter is
enabled would guarantee that data never appears. `mask_median` is recorded
alongside the two ratios because `spec_ratio` is a peak over a median and so moves
with exposure; without it a drift in the illumination is silent.

**A rejected object keeps its reason instead of disappearing.** The three tables
used to be a chain of filtered copies, so an object that dropped out was merely
absent, and absent for one of six reasons. Now every detection is labelled once
and `pickable` and `isolated` are selections on that label. This is not tidiness:
with 13% of margin, "there were no bubbles" and "the filter ate every cuboid" are
both plausible, and they have to look different. They look different in three
places — the reason on the row, magenta on the frame, and the counts on every
event that follows a measurement, including the two exits that shake or hand back,
which are how a frame the filter emptied actually leaves.

**One reason per row, four tests per row.** The reasons are ordered and a row
takes the first that applies, so they partition the detections and the counts add
up; `boxes` is reported next to `detected` because contouring and the ROI drop
objects before the table exists, and a frame where that happened to everything
must not read as a frame the filter emptied. `bubble` is ordered before `floater`
because a bubble drifts, so ordering floater first would label almost every bubble
a floater and drive the bubble count to zero exactly when bubbles are the problem.
The four underlying tests are kept as separate columns anyway: one object can fail
several at once, so any single label undercounts something, and with the booleans
present the order is a presentation choice that can change later without
invalidating anything already recorded.

**The filter ships enabled, and the doubt falls towards keeping the object.**
`require_both` means a cuboid has to breach both features to be discarded, which
is what makes the thin margin survivable; an unmeasurable object is never called a
bubble, because the failure that costs a well is discarding a microtissue, not
aspirating a bubble that was missed. If a real run shows the filter eating
cuboids, `bubble_filter_enabled = False` stops the rejection while the features
keep accumulating.

---

## 12. Conventions

Commits follow Conventional Commits: `feat`, `fix`, `refactor`, `docs`, `test`,
`chore`; imperative mood; first line under 50 characters; blank line before the
body; the body says why, since the diff says what.

`labware/` and `ml_models/` hold inputs and are deliberately **not** created
automatically — a missing one is a real problem, and an empty folder that looks
correct is worse than an error. `profiles/example/` is tracked; real profiles
are not.

Comments explain decisions and constraints, not mechanics. If a line needs a
comment saying what it does, it usually needs rewriting instead.
