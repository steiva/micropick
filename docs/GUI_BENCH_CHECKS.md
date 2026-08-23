# GUI checks that need the bench

Everything in the GUI is developed against `--mock`, which covers most of it.
This file collects what mocks cannot answer: the real robot, the real cameras,
the machine the rig is actually driven from.

One entry per check. Each says which commit introduced the need for it and what
failure to expect if something is wrong, so a check that goes badly points at
something rather than just at "the GUI".

---

## Commit 1 — package skeleton, entry point, theme

- [ ] **`micropick-gui` starts on the bench machine.**
  Expected failure if wrong: `ModuleNotFoundError` for PySide6 — the extra was
  never installed there (`pip install -e ".[gui]"`). This is the only check
  here that is about the installation rather than the rig.

- [ ] **The UI font is "Segoe UI Variable Text".**
  The bench machine is the only one that has it; every development machine
  falls back. Compare against a screenshot from a development machine: if they
  look identical, `theme.FONT_FAMILIES` did not match and the fallback ran.

- [ ] **The window is not blurred or half-sized on the bench display.**
  `theme.configure_hidpi` sets a PassThrough rounding policy, which is only
  exercised on a fractional-scale display. Expected failure if wrong: text
  crisp but everything oversized (rounding to 2x), or a window that opens at
  half the requested 1400x900.

- [ ] **The theme follows the operating system's light/dark setting.**
  `qdarktheme` reads it through `darkdetect`, which is per-platform. Flip the
  OS setting with the application open. Expected failure if wrong: nothing
  changes, and `micropick.qss` is not the cause — it holds no colours.

---

## Commit 2 — application shell

- [ ] **Navigation rows are not clipped and do not overlap.**
  The row height is computed from the font's metrics, and the bench machine is
  the only one running the intended font, so this is the only place the number
  is what it will be in use. Expected failure if wrong: the selected row's
  rounded highlight runs into the entry below it, which is what stylesheet
  `padding` and `min-height` did before the height moved into `shell.py` —
  qdarktheme's QProxyStyle paints those but does not measure them.

Nothing else in this commit touches hardware: the status bar's three fields are
static until the session owns them.

---

## Commit 3 — session

- [ ] **Connect brings the robot up in the right order.**
  `create_run()`, then the custom definitions, then `load_pipette()`. The log
  shows all three with the run id and the count of definitions. Expected
  failure if the order is wrong: the connect appears to succeed and the first
  jog step fails instead, because nothing that moves the robot works before a
  run and a pipette exist — DESIGN section 9.

- [ ] **The definitions in `labware/` reach the run.**
  The log line says how many were uploaded and names them. Expected failure if
  wrong: `LabwareError` with the directory it looked in, and no connection —
  deliberately, since a run without them cannot load a plate.

- [ ] **The window does not freeze while connecting.**
  The robot is on the other end of HTTP. Drag the window during the connect;
  it should keep repainting, and Connect should be greyed out until the log
  says the pipette is loaded. Expected failure if the worker is bypassed: the
  window stops responding for the length of the round trip.

- [ ] **Both real cameras open at the resolutions in the profile.**
  The log line reports what was actually opened, and the lower camera should
  report its view crop. Expected failure: `CameraError` naming the requested
  and the delivered resolution — the camera refuses to resize rather than
  invalidating the pixel map silently.

- [ ] **Closing the window parks the axis and releases the devices.**
  Watch the gantry: `leftZ` retracts. Afterwards no python process holds the
  cameras — reopening the application should find them free. Expected failure:
  a second launch reports the device is busy, which means a grab thread
  outlived the window.

- [ ] **A real profile's calibration state reads correctly.**
  The Profile page should show the degree, the pose count and the held-out
  error of the profile fitted on the bench, not "not calibrated".

---

## Commit 4 — camera view

- [ ] **The upper camera at 2592×1944 holds thirty frames a second.**
  The caption reports the rate from `frame_count`. On mocks it reads about 14,
  but that is the synthetic scene rendering an ArUco warp per grab, not the
  widget. Expected failure if the scaling path is wrong: a rate in the single
  digits and a window that lags behind the gantry.

- [ ] **The lower camera at 4000×3000 does not stall the window.**
  This is the frame the whole design is for: 36 MB, reduced by `cv2.resize`
  with INTER_AREA before it becomes a QImage. Drag the window while it is
  live. Expected failure if the reduction is skipped or moved into
  `paintEvent`: repaints in the hundreds of milliseconds, worst while resizing.

- [ ] **The lower camera shows its crop and only its crop.**
  The caption should read `2000×1000  crop 0.5` for a 2000×1500 sensor frame —
  the centred square, not the whole field. Expected failure: the full sensor
  frame on screen, which means `crop` was read as a sensor property rather than
  a view one.

- [ ] **The crosshair sits on what the pipette is over.**
  Jog to a landmark and compare with where the tip comes down. This is the
  check the transform cannot do for itself: it is arithmetic against the crop
  origin, and the origin is the thing that was wrong for a whole run of the
  machine — DESIGN section 5.

- [ ] **A camera unplugged mid-view degrades quietly.**
  `BackgroundCamera` reports the failure after fifty missed grabs. The view
  should keep the last frame and stop counting, not raise. Expected failure: a
  traceback in the log and a dead widget.

---

## Commit 5 — worker base

- [ ] **Cancelling a real calibration sweep stops it between poses.**
  `run_sweep` checks its `cancel` event between poses, not inside one, so the
  gantry finishes the move it is making and then stops. Expected failure if the
  event is not reaching it: the sweep runs to the end and the button did
  nothing. That is the failure mode to watch for — cancellation that is
  accepted visually and ignored underneath.

- [ ] **Progress from a sweep counts real poses.**
  The bar should advance once per pose, to the pose count `plan_sweep` reported
  in the log, and reach it. Expected failure: a bar that stops short, which
  means poses were skipped and the log will say why.

- [ ] **A refused move on the bench arrives as text, not a crash.**
  The robot answers 201 to commands it declines, so `MoveFailed` is raised by
  `protocols.move_to` inside the worker. It should appear as one line in the
  panel with the full traceback in the log, and the application should still be
  running. Expected failure: the window disappears — an exception reaching a
  QThread's `run()` is caught by nothing above it.

---

## Commit 6 — manual control

- [ ] **A held arrow does not queue moves.**
  Hold an arrow key down for a few seconds and let go. The gantry should stop
  when the key is released, not keep stepping. `JogController.busy` is what
  refuses the second move; the page only greys the controls out to show it.
  Expected failure: the robot keeps travelling after the key is up, which means
  something is bypassing the controller.

- [ ] **PgUp and PgDn move Z and nothing else scrolls.**
  The panel is deliberately not a scroll area and the saved-positions list is
  NoFocus. Expected failure: the panel scrolls and Z does not move, or the list
  selection jumps.

- [ ] **The shortcuts do not reach the robot from another window.**
  Put another application in front and press the arrow keys. Nothing should
  move. This is the whole reason `jog_in_window` was preferred over the global
  hotkeys, and `WidgetWithChildrenShortcut` is what carries it here.

- [ ] **A soft limit reads as text, and the robot can come back.**
  Jog into the X limit: the panel says refused or clamped, no dialog appears,
  and the next step in the opposite direction works. Expected failure: every
  direction refused once outside the box, which is the trap DESIGN section 4
  describes and `JogController.allowed` avoids.

- [ ] **Home and Retract Z ask first, and the gantry does what was confirmed.**
  Both are one confirmation away and neither is undoable. Watch the axis.

- [ ] **"Remember in profile" survives a restart.**
  Name a real deck landmark, close the application, reopen it, and check that
  `positions.json` in the profile holds it. This is the path a taught pose
  takes before a calibration or a picking run reads it back with
  `profile.where`.

- [ ] **The camera view keeps up while jogging.**
  With the upper camera live, step the gantry and watch the picture follow.
  Expected failure: the frame lags the motion by more than the move takes,
  which means the display is reading stale frames rather than the newest.

---

## Commit 7 — overlay geometry split from drawing

- [ ] **A recorded clip looks the way it did before the split.**
  The golden test pins one synthetic scene, bit for bit. What it cannot pin is
  a real dish: contours from Otsu, a real dish circle, real status text. Record
  one clip with the picking notebook and compare against a clip from before —
  the boxes, the circles and the panel should be in the same places and the
  same colours. Expected failure if the order were sorted somewhere: the white
  choice box or the magenta bubble outline hidden under a class colour.

---

## Commit 8 — camera calibration page

- [ ] **The dictionary on step 2 is the one the marker was printed from.**
  Nothing in the profile records it, so it is offered rather than assumed.
  Expected failure if it is wrong: "marker not detected at the starting pose",
  which looks exactly like bad lighting and is not.

- [ ] **The sweep's held-out error lands near 24 µm at degree 3.**
  That is the number from the first real sweep, DESIGN section 3. Compare with
  degree 1 or 2 on the same data through `compare_degrees` if it does not:
  identical numbers at 1 and 2 mean lens distortion is the only thing present,
  and a difference there means something else is.

- [ ] **Coverage reaches roughly 94 % × 95 % of the frame.**
  The coverage plot shows it directly. Markedly less means the sweep was cut
  short or poses were skipped, and the log says which.

- [ ] **The residual plot separates one bad pose from a general drift.**
  This is what the plot is for and mocks cannot produce it. One point far above
  the rest is a pose to run again; the whole cloud lifting is a loose camera
  mount or a focus ring that moved, and that is not fixed by repeating the
  sweep.

- [ ] **Cancelling mid-sweep leaves the stored pixel map untouched.**
  Verified on mocks, but verify once here too: the profile is only written by
  the button on step 4. Check `calibration.json` after a cancel — `pixel_map`
  must be whatever it was before, and `history/` must have gained nothing.

- [ ] **After any interrupted sweep, step 1 is done again before the next.**
  The page says so. The gantry stops at the pose it reached and
  `measure_scale` probes from wherever it stands, so a second attempt without
  re-centring fails at the starting pose. Confirm the message appears and that
  re-centring makes the next run work.

- [ ] **The saved map is the map in use.**
  After saving, reload the profile and jog to a pixel through the new map. The
  recovered marker side in the report should also be within about half a
  percent of the printed size — the fit never uses it, so agreement is
  independent evidence.

---

## Commit 9 — detector service

- [ ] **The real weights load, and the interface stops saying "stand-in".**
  Put `cuboid_bbox_v4-11_best.pt` (or whatever the current file is) in
  `ml_models/`, pick it in the chooser, Load. The detector line must read
  `model: <filename>`. Expected failure: `FileNotFoundError` naming the
  directory it looked in. The stand-in is offered last in the list on purpose,
  so it cannot become the default on a bench that has weights.

- [ ] **A stand-in result is never mistaken for a real one.**
  Load the stand-in on a real dish frame and confirm the note appears in both
  the detector card and the result card. This is the check that matters most
  here: the two results look identical on screen and only the label separates
  them.

- [ ] **On a frame from the real upper camera the map is used, not refused.**
  A frame captured at the resolution the pixel map was fitted at should come
  back classified — pickable, isolated, bubble counts — rather than
  "detections only". If it says the map was fitted at another size, the camera
  is running in a different mode from the calibration, which is DESIGN section
  3 and is a real problem, not a display one.

- [ ] **The overlay lands on the cuboids.**
  Compare the QPainter overlay against the same frame put through
  `overlays.annotate` in the notebook. Same colours in the same places. A
  systematic shift is the crop origin again — the failure that put every box
  251 px from its cuboid for a whole run.

- [ ] **Inference does not freeze the window.**
  The real model on a 2592×1944 frame is hundreds of milliseconds. Drag the
  window during Detect; it should keep repainting and the buttons stay greyed
  until it returns.

---

## Commit 10 — plate and routine

- [ ] **`check_labware` passes against a plate the robot actually loaded.**
  Connect, load the destination plate into its slot through the run, then
  Check. The three outcomes were exercised on `MockRobot`; what the bench adds
  is that `loaded_labware` parses the real run state the same way. Expected
  failure: "slot N is empty" while a plate is plainly sitting there, which
  means the plate was put on the deck by hand and never loaded into the run —
  the robot only knows what it was told.

- [ ] **The version note reads correctly on a stock definition.**
  `ot2_api.load_labware` sends version 1 whatever the definition says, and most
  stock definitions are v2 or above, so the mismatch line should appear and
  should be labelled as ignored. Expected failure: no line at all, which would
  mean the run reported the definition's own version and the reasoning behind
  ignoring it needs revisiting.

- [ ] **Resuming a real interrupted run demands confirmation once, and then
  fills the right well.** Interrupt a run, reopen the routine, read the
  summary, confirm, and check that the next well the session goes to is the one
  the summary named. This is the whole safety story of DESIGN section 7 and it
  cannot be rehearsed on mocks past the bookkeeping.

- [ ] **A fresh plate of the same format is given a new routine, not a
  confirmation.** The one case no software check can see. Worth walking through
  once with the operator who will do it.

- [ ] **The 1536 plate is legible on the bench monitor.**
  Wells carry no printed name above 400 of them; the name is in the tooltip.
  Confirm that hovering is enough to identify a well, or the threshold needs
  raising.
