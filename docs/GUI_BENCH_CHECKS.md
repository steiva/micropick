# GUI checks that need the bench

Everything in the GUI is developed against `--mock`, which covers most of it.
This file collects what mocks cannot answer: the real robot, the real cameras,
the machine the rig is actually driven from.

One entry per check. Each says which commit introduced the need for it and what
failure to expect if something is wrong, so a check that goes badly points at
something rather than just at "the GUI".

The entries before "Redesign, phase 1" were written for the old window:
tabs, a status bar, the Profile page, Manual control. The checks still
stand; where they are done moved. The pages are now steps of a task from
Home (Robot & Deck is the Deck step, Tip calibration the Tip step, Plate
plan the Plan step, Manual control Manual pick's Run), the profile's
settings are Robot setup's rows and the Settings sheet's second column,
the status bar's light is Light in the top bar, its tip button the Tip
step, and a camera's focus is set under the picture in Robot setup's
camera sheet. Liquid handling's groups and blocks are now a plan of steps:
what its entries check of the robot stands, and the screen's own checks are
under "Liquid Plan and Run".

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
  the centered square, not the whole field. Expected failure: the full sensor
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

- [ ] **The keys drive the robot from anywhere in the window.**
  On the manual page, click the Manual control tab, then the camera combo,
  then the picture, and press an arrow after each click. The gantry steps
  every time and the page never changes. Expected failure: a key changes the
  page, or does nothing until a button on the panel is clicked first. The
  shortcuts are `WindowShortcut` and the page tabs are `NoFocus`, which
  together are what makes this true.

- [ ] **The shortcuts do not reach the robot from another window.**
  Put another application in front and press the arrow keys. Nothing should
  move. This is the whole reason `jog_in_window` was preferred over the global
  hotkeys, and `WindowShortcut` (not an application-wide or global one) is
  what carries it here.

- [ ] **A soft limit reads as text, and the robot can come back.**
  Jog into the X limit: a toast says so, no dialog appears,
  and the next step in the opposite direction works. Expected failure: every
  direction refused once outside the box, which is the trap DESIGN section 4
  describes and `JogController.allowed` avoids.

- [ ] **Retract goes straight up and asks nothing (#108).**
  Tip low over the dish, Retract in the top bar, on Home and on a step: the tip
  rises where it stands to the travel height, "Retracting…" while it moves.
  Again above it: nothing moves. Bind a key in Settings, Keys: it does the same
  and the pad's Keys lists it. Home is in Settings, Advanced, and asks first.

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
  re-centering fails at the starting pose. Confirm the message appears and that
  re-centering makes the next run work.

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

---

## Commit 11 — robot bring-up as a decision

- [ ] **Connect on a robot left powered on finds its run.**
  The block should name the run id, `idle`, the pipette and every slot the run
  holds. "Continue with this run" then makes the manual page live without any
  gantry movement. Expected failure: "no current run" while the Opentrons app
  shows one — `get_all_runs` is paged to the last 20 and the current one should
  always be in it; if it is not, that is worth a look before anything else.

- [ ] **Continue on a run without a pipette loads one.**
  Create a run from the Opentrons app without a pipette, then Connect here and
  Continue. The log should say the pipette was loaded, and a jog step works.

- [ ] **The reusable statuses are right.**
  `idle`, `running` and `paused` are assumed to take setup commands; stopped,
  failed and succeeded are assumed not to. Confirm on at least one finished
  run: Connect should say "only a new run is possible", and if instead a
  finished run still accepts commands, `REUSABLE_STATUSES` in
  `gui/session.py` is the one place to widen.

- [ ] **New run + home homes.**
  Watch the gantry go to all three limits. Afterwards a jog step works
  immediately. Expected failure if the order were wrong: a run with a pipette
  that refuses every move with no visible reason - the state this method
  exists to make impossible.

- [ ] **`create_run` timing appears in the log.**
  Each new run logs "run … created in N s". Create several in a row on a robot
  left on and read the numbers back: this is the slowdown that was an
  impression, made into a series.

## Commit 12 — labware page

- [ ] **A stock tip rack loads into its slot and the deck shows it.**
  Connect, open Labware, click slot 10, choose "Opentrons OT-2 96 Tip Rack
  300 µL", Load. The slot fills and names the rack; the log has "loaded
  opentrons_96_tiprack_300ul (opentrons) into slot 10". Re-read changes
  nothing. Expected failure: the robot answers with an error for a load name
  it does not have, which would mean the installed `opentrons-shared-data`
  is newer than the robot's software.

- [ ] **A custom plate loads on a run that was adopted, not created.**
  Connect to a robot left on with its run, then load the Greiner 1536 into a
  slot. The definition is uploaded by the page itself before loading, so this
  works whether or not the connect uploaded it. Expected failure: "the robot
  rejected …" from `upload_definition`, whose text carries the robot's own
  reason.

- [ ] **Replace empties the slot first, and Remove empties it.**
  Load a plate into slot 5, choose another and press "Replace in slot 5": the
  log shows the move off deck and then the load, and the slot shows the new
  plate. Remove (off deck) leaves the slot empty on the deck and in the run.
  Then check on the Routine page: `check_labware` sees the same state.

- [ ] **What the page shows is what the robot reports.**
  Load a plate through the Opentrons app or a notebook while this page is
  open; press Re-read. The plate appears. The page has no memory of its own,
  so there is nothing to go stale but the last read.

- [ ] **The status bar knows about a tip this application never picked up.**
  Pick up a tip from the notebook (or the Opentrons app), then Connect here
  and carry on with the run. The bar shows "TIP ON" in amber before anything
  is clicked, and the Labware page's Pick up tip is greyed out. Expected
  failure: "no tip" with a tip plainly on the pipette - which would mean the
  command log is not being read from the end, or the pick-up was in a
  different run than the one carried on with.

- [ ] **A tip comes off the rack and goes into the trash.**
  With a rack in slot 10 selected, choose A1 and Pick up tip: the gantry goes
  to A1, presses on, the bar turns amber, the card says "from slot 10 A1"
  and the chooser has moved on to B1. Pick up tip is now disabled. Drop in
  trash: the gantry moves over the fixed trash at slot 12 and the tip drops
  into the bin; the bar reads "no tip". Expected failure: the robot declines
  `moveToAddressableAreaForDropTip`, whose error text will name the area it
  wanted; `fixedTrash` is the OT-2's on software 7.1 and later.

- [ ] **The lights button switches the rails and shows what the robot said.**
  Click the bulb in the status bar: the rails go off, the icon goes hollow.
  Click again: on, and amber. Then switch them from the Opentrons app and
  Connect again: the bulb shows the robot's state, not the last click.

- [ ] **The tip calibration switches the lights on.**
  Leave the rails off after a picking run and start the pipette offset
  calibration from the notebook: the log says "rail lights were off:
  switching them on" before the upper camera is read, and they stay on
  afterwards. Expected failure: a `TipCalibrationError` about the lights,
  which means /robot/lights could not be read or the toggle did nothing.

- [ ] **Return to rack puts the tip back where it came from.**
  Pick up from C1, jog somewhere on Manual control, come back, Return: the
  tip goes into C1, not A1. Drop in place with no tip on reads as a refusal
  in the card, with the robot's own words, and nothing moves.

## Commit 13 — pipette offset calibration

- [ ] **A profile taught from the notebook is picked up as it is.**
  With `tip_calib` in positions.json from the notebook, step 1 shows the
  stored position and "Go to stored position" drives there at the module
  height. Expected failure: the gantry arriving somewhere else, which would
  mean the page and the notebook disagree on the name or the z.

- [ ] **The whole run, with the touch-up, matches the notebook's numbers.**
  Pick up a tip, open both cameras at the profile's default modes, run with
  the profile's offset as the start. The log reads as the notebook's output
  does — crosshair found, residual in px and mm, "after correction the tip
  is N um from the crosshair" — then the touch-up block appears with the
  lower camera live and the step at 0.05 mm. Nudge, Accept: the result
  screen's offset is where the gantry ended up, `calibration.json` carries
  it with `method: auto+manual` and the homography, and the status line on
  the Profile page shows the new offset.

- [ ] **Abort during the touch-up saves nothing.**
  Start, wait for the touch-up, Abort. The log says so, `calibration.json`
  is unchanged (compare its mtime), and the gantry stays where it was.

- [ ] **No tip, no run.**
  Drop the tip and open step 3: Start is greyed and the checks name the
  reason. Pick one up on the Labware page and the check clears without
  leaving the tab.

- [ ] **The lower camera at the wrong mode is refused before anything moves.**
  Open the lower camera at 2000×1500 (the picking clip's mode) and Start:
  `TipCalibrationError` about the calibrated mode, and the gantry has not
  moved. This is the check DESIGN section 3 says cost a whole run in silence.

- [ ] **The Z axis is retracted when the calibration completes.**
  After Accept, the log ends with "retracting leftZ" and the tip is at the
  top of its travel, not over the disc. After Abort it is not.

## Commit 14 — the feed: zoom, crosshair, focus

- [ ] **The wheel zooms about the cursor on every feed, at full frame rate.**
  On the lower camera at 4000×3000, zoom to 16× on a crosshair: the fps in
  the caption does not drop, because only the visible cut-out is resampled.
  Double-click resets. Expected failure: the frame rate halving at high
  zoom, which would mean the whole frame is being enlarged.

- [ ] **The lower camera's focus moves the lens live.**
  On the lower feed the focus slider shows the profile's value (920 on the
  bench). Drag it: the picture goes in and out of focus as it moves, and the
  number is what the device reads back. Then on the Profile page, "Save
  controls" writes it to cameras.json, and the next open starts from it.
  Expected failure: "asked N, got M" beside the slider, which means the
  driver ignored the set - the same check `apply_controls` makes at open.

- [ ] **The crosshair is off on the lower camera and on on the upper.**
  Open both and switch between them on Manual control: the tick follows
  the camera. Tick it on the lower feed, switch away and back: the choice
  holds.

- [ ] **The window fits the bench screen, maximised and with the taskbar.**
  Maximise on the 1920×1080 display: no `QWindowsWindow::setGeometry`
  warning in the console, and the bottom of every side panel is reachable,
  by the bar or the wheel, on Manual control, both calibration tabs and
  Routine. The panels' minimum height used to add up to 1144 lines, more
  than the screen has under a taskbar, and Qt refused the geometry. Over a
  scrolled panel PgUp still moves Z and the wheel never turns the step
  combo.

- [ ] **A camera's feed opens from the status bar on any page.**
  On the Profile page, with nothing open, click the lower camera's button
  in the status bar: the camera opens (the icon fills) and its feed appears
  in a window of its own, crosshair off, focus slider at the bottom. Close
  the window: the camera stays open. Close the camera on the Profile page:
  the window goes away. The window keeps its size and place between shows.

## Commit 15 — deck modules

- [ ] **The profile's modules reach the run without a cell being run.**
  `profiles/lab_main/deck.json` carries slots 5, 8, 9 at +64.2 mm, the
  notebook's `add_slot_offsets`. Connect, load the destination plate into
  slot 5 from the Labware page, then `GET /runs/<id>`: the labware carries
  an `offsetId` and the run's `labwareOffsets` has a vector of z 64.2 for
  slot 5. Move to A1 top: the tip stops above the plate on the platform,
  not 64 mm into it. Expected failure: no offsetId, which would mean the
  wrapper's table was empty at load - the session registers it in `_ready`,
  so look for a connect that did not go through it.

- [ ] **A plate loaded from the notebook without the offset is flagged.**
  From the notebook, `load_labware` a plate into slot 5 with no offsets set,
  then Connect here and carry on with the run. The deck shows slot 5 in
  amber with the module band, the Deck modules card names it, the status
  bar reads "DECK: slot 5 without module offset" on every page. "Load again
  with the offset" clears all three, and the run shows the new labware with
  its offset.

## Commit 16 — the marker on the feed

- [ ] **The marker is outlined the moment the camera sees it.**
  On step 1 with the upper camera open, slide the marker into the frame:
  a green outline, a yellow edge along its top, a white dot on its first
  corner, and "id N · as printed". Turn it a quarter turn: the caption
  follows and the dot moves with the marker's corner. Expected failure: an
  outline that lags the picture by more than about a third of a second,
  which would mean the watch is reading the camera rather than the frame
  the view is showing.

- [ ] **A marker face down is named as such.**
  Put the marker down the wrong way up, or hold a sheet with the marker
  printed on the far side. Nothing is drawn, and the card says a mirrored
  marker is in no dictionary. This is the check the whole thing exists for:
  before it, this looked exactly like bad lighting.

- [ ] **A marker from another dictionary is found and offered.**
  Hold up a 4X4 marker with the sweep set to DICT_6X6_250: it is outlined,
  the card says which dictionary it is really in, and one click sets step 2
  to it. Expected failure: nothing drawn at all, which would mean the other
  dictionaries are not being tried.

- [ ] **Nothing on that page costs the sweep anything.**
  Start the sweep from step 3 and watch the frame rate in the caption: the
  watch stops when step 1 is not the page on screen, so the sweep has the
  camera to itself.

## Commit 17 — the sweep's overlay, the check tab, a maximised window

- [ ] **The sweep draws the marker it is tracking, pose by pose.**
  Start the sweep and watch: at each pose the outline lands on the marker
  and the caption reads "tracked". These are the corners being fitted, not
  a second detection, so a pose where the outline does not appear is a pose
  the tracker missed - and the log says the same thing a moment later.
  Expected failure: an outline that stays where the marker was two poses
  ago, which would mean `on_frame` is being called with stale corners.

- [ ] **The check tab drives the tip to a crosshair, and the tip is there.**
  With both calibrations saved and a tip on, open Manual control (the
  Check tab's home since commit 23), Detect crosshairs: every
  crosshair on the disc is circled green, the map's reference pixel is a
  red cross, anything outside the fitted area is grey. Switch on "Click to
  move" and click one. The tip arrives over it at z 67 - look at it, that
  is the whole point of the tab. Expected failure: the tip lands beside the
  crosshair by a consistent amount in one direction, which is the pipette
  offset being stale rather than the map being wrong.

- [ ] **The map's consistency figure is in the tens of micrometres.**
  Click four or five crosshairs across the field. The mean should be near
  the sweep's own held-out error (about 24 µm at degree 3); a figure in
  millimetres means the map and the camera no longer agree - focus, zoom or
  mode - and the sweep needs running again.

- [ ] **Nothing moves until it is armed.**
  With "Click to move" off, clicking a crosshair prints the target and the
  gantry does not move. Leave the tab and come back: it is off again.

- [ ] **The window opens maximised on the bench display.**
  `micropick-gui` fills the screen without being dragged, and the title bar
  and taskbar are still there - the notebook and the Opentrons app have to
  be reachable while a run is going.

## Commit 18 — positions that are worth having, panels that fold

- [ ] **A saved position survives everything and can be driven to.**
  On Manual control, jog somewhere, "Save this position…", name it. The row
  shows the name and its three coordinates. Restart the application and it
  is still there; select it and Go to, and the gantry returns. Expected
  failure: an empty list after a restart, which was the old behaviour -
  that list was the controller's own, in memory, and went with the
  connection.

- [ ] **Rename and delete do what they say.**
  Rename one: the row changes and stays selected. Delete one: it asks
  first, naming the position and its coordinates. `positions.json` in the
  profile matches the list after both.

- [ ] **`tip_calib` appears in the list and driving to it works.**
  The pose the pipette calibration taught is a position like any other.
  Driving to it from here should put the disc back under the camera - the
  same place "Go there" on the pipette tab goes.

- [ ] **No Move or Positions section.**
  No jog panel, on any page or sheet, has a folded Move or Positions
  section. Undo step is on the pad, beside Keys: a step, then Undo step,
  and the gantry is back where it was.

## Commit 19 — planning a plate with the mouse

- [ ] **The plate list is what the deck holds, and nothing else.**
  Load a tip rack and a plate from the Labware page, then open Routine: the
  list offers the plate by slot and not the tip rack, and the mini deck
  shows both where they are. Click the plate's slot on the deck and it is
  the one in the list. Expected failure: a plate offered that is not on the
  deck, which is what the old list of every definition in `labware/` did.

- [ ] **All four ways of selecting work on the real plate.**
  Click a well; Ctrl-click three more; drag a box over a block; click a
  column number and then a row letter. Shift takes away by any of them. The
  selection is the white ring, and the count in the box does not change as
  the selection does.

- [ ] **A count goes into every selected well at once.**
  Select a column, set 3: every well in it reads 3. Select two of them, set
  0: they leave the plan and read their names again. The line under the
  buttons says how many are selected, what they hold, and what the plan
  adds up to.

- [ ] **A 384 and a 1536 are both plannable.**
  On the 384 the numbers still fit; on the 1536 the wells are dots with no
  numbers, and the row and column headers are the only way to select a line
  - which is what they are for. Both create a routine whose summary counts
  what was planned.

- [ ] **Routine comes before Picking in the tabs.**
  And the plan made here is the one the Picking page's run delivers into.

## Commit 20 — the dish, measured before the run

- [ ] **The camera is open when the page appears.**
  Open Picking, Manual control or either calibration tab with no
  camera open: the upper camera opens itself and the picture is there. Unplug
  it and try again: the page says why once and does not try again until the
  Profile page's button is pressed - watch for a page that becomes slow to
  open, which would mean it is retrying.

- [ ] **"Go to the dish" goes to the dish.**
  Jog until the dish fills the frame, Teach here, drive somewhere else, then
  Go to the dish: the same view comes back. `observe` appears in the jog
  panel's positions like any other pose.

- [ ] **The analysis counts what a run would pick.**
  With the real weights loaded, Detect cuboids: the overlay is the picking
  window's own - red for every detection, yellow for pickable, green for
  isolated - and the histogram shows the population against the size
  window. Compare the "N of M inside" figure with what the first batch of a
  run actually picks up; they should be the same order of number. Expected
  failure: an empty histogram with detections on the picture, which means
  the pixel map and the camera mode disagree and the sizes are in no unit.

- [ ] **Moving the size window moves the count.**
  Picking settings…, change `cuboid_size_threshold`, Save: the band on the
  plot moves and the count under it changes without re-analysing. Saved into
  `picking.json`, which is what a run reads.

- [ ] **The settings form has everything and refuses nothing silently.**
  Filter for a field you know is in the schema; it is there. Put a window in
  the wrong order and Save: pydantic's own message appears and the dialog
  stays open.

## Commit 21 — the run from the Picking page, tabs along the top, profiles

- [ ] **Start asks once, then the robot moves.**
  With everything in place, Start picking: a dialog names the routine and
  asks about the dish, the plate, the lids and the settings, with Cancel as
  the default. Start: the gantry retracts, goes to `observe` and the run
  begins without Resume. Cancel: nothing moves.

- [ ] **Resume is only for a run that asked for it.**
  During a normal run Resume and Space do nothing. Empty the dish until the
  run stops in `needs_operator`: Resume lights up, and pressing it measures
  the dish again before choosing anything.

- [ ] **The run's status is on the picture, beside the resolution.**
  State, target, PAUSED when paused and the keys, in a translucent box under
  the resolution caption, the same size at any zoom. Nothing is written into
  the frame. During the run the decision frame is held (the caption says
  "held") and the overlays sit on it; while waiting for the operator it is
  live.

- [ ] **Analysis is drawn over the live feed.**
  Detect cuboids, then nudge the dish by hand: the contours stay where
  they were and the cuboids move out from under them. Detect again and they
  line up. Switching camera removes them.

- [ ] **The routine is named above Start.**
  Make or change a routine on the Routine page: the Picking page shows its
  summary - name, delivered/planned, next well, plate and slot - before the
  run buttons, and it updates as the run delivers.

- [ ] **Tabs along the top, two groups.**
  Profile, Labware, Calibration, Routine, Picking on the left in that order;
  Manual control and Log at the right edge. The chosen tab is filled. Space
  or the arrows never change the page.

- [ ] **Where the gantry is shows on the picture.**
  On Manual control, the calibration tabs and Picking the jog panel
  has no Position card; the coordinates are in a box on the picture, and a
  refused or clamped step is a toast, never a second line there. With
  no camera the box is still shown over the placeholder.

- [ ] **Profiles are made and removed on the Profile page.**
  New profile…, a name, Start from the loaded one: the copy loads, with the
  same cameras, calibration and positions and no history. Start from
  "empty": a profile with defaults. A name that exists or contains a slash
  is refused while it is typed. Delete… on a profile that is not loaded asks
  first, with Cancel as the default, and then removes the directory; on the
  loaded one the button is off and says why.

- [ ] **The Profile page fits in two columns.**
  Installation and Robot side by side, Cameras and Models under them.

## Commit 22 — tabs that open into the page, a 4:3 picture, a quieter crosshair

- [ ] **The chosen tab opens into its page, in both themes.**
  Flip the OS between light and dark with the window open: the strip is
  the raised surface, the chosen tab is the page's own colour and there is
  no line between them. Pages have no title of their own any more; the tab
  is the title.

- [ ] **The picture is as wide as its shape, and the panel gets the rest.**
  Maximise the window on the bench display: the upper camera's picture is
  4:3 with no black bands, and the panel beside it is wider than before.
  Switch Manual control to the lower camera (square crop): the picture
  narrows and the panel widens. In a small window the panel keeps its
  designed width and the picture is letterboxed instead. Watch for wrapped
  text cut off in the panel after a resize - the column measures its labels
  again at each new width.

- [ ] **The crosshair is thin, white and see-through.**
  On a bright dish and on a dark one it is visible and does not hide what
  it is aimed at.

## Commit 23 — Manual control moves the robot from the picture

- [ ] **The top of the Z travel is measured, and used.**
  Tip on, lower the tip a few millimetres with PgDn, switch on Click to
  move and click the picture: the log shows "retracting leftZ" before the
  gantry travels, and the tip is at the top. Click again: no retract this
  time. Change the tip (or reconnect): the next click retracts again.

- [ ] **The camera goes where you clicked.**
  Click a feature near the middle of the upper camera's picture: it ends up
  under the crosshair. Near the frame's edge a toast says "Approximate:
  outside the calibrated area.", and the feature lands
  near the crosshair rather than on it. Expected failure: a consistent miss
  in one direction everywhere, which is the map's reference pixel not being
  the crosshair.

- [ ] **Unreachable targets are refused.**
  Drive near a corner of the deck and click beyond it: a red toast "Not
  reachable: X 402.1 is past the limit 380.", and nothing moves.

- [ ] **The check from the old Check tab, here.**
  Detect crosshairs on the disc, click one: the tip is lowered over it at
  Crosshair Z (67). The Calibration page has two tabs now.

- [ ] **The tip goes to a cuboid at Cuboid Z.**
  Detect cuboids on a dish, click one: its box turns white and the tip
  comes down over it at Cuboid Z, which starts at the profile's pickup
  height. Change Cuboid Z and check `picking.json`: `pickup_offset` moved,
  `dish_bottom` did not.

- [ ] **Nothing moves with Click to move off, or on a zoom reset.**
  Leave the tab and come back: the box is off, and a click only prints the
  target. Double-click a zoomed picture with it on: the zoom resets and the
  gantry stays where it is.

- [ ] **Middle-drag pans a zoomed picture and stops at its edges.**
  Zoom in with the wheel, hold the middle button and drag: the picture
  follows the hand and stops when its edge reaches the widget's. Every
  camera view has it, feed windows included.

- [ ] **Aspirate and dispense in place, from the page and from A and D.**
  Tip on, tip in liquid: set Volume and Flow rate, press A - the log and
  a toast say "Aspirated 10 µl at 50 µl/s, here." and the
  liquid rises in the tip; D puts it back. With no tip a red toast "Put a
  tip on first." and nothing is sent. On any other page A and D do
  nothing. Expected failure: the robot declines the aspirate (a dispense of
  more than is in the tip, or no prior pick-up) - the refusal is a red
  toast rather than nothing happening.

- [ ] **The keys are on the picture.**
  Bottom-right of the picture: the jog keys, A and D, and the mouse. H hides
  the box and shows it again, with or without a robot connected.

## Commit 24 — straight moves, a well to put a cuboid in, pickups in two clicks

- [ ] **Click-moves go in one straight line, 1 mm under the top.**
  Click far across the picture with a long tip fitted: the first click
  retracts, then the gantry travels diagonally in one move at 1 mm under
  the retracted Z (the position box shows it). Further clicks go without a
  retract. After a tip move has lowered the tip, the next click retracts
  again. Expected failure: a red toast "asked for ..., ended at ...",
  which would mean the robot refuses moveToCoordinates at the travel height
  too and TRAVEL_BELOW_TOP_MM needs to be larger.

- [ ] **Go to well reaches the well at the chosen level.**
  Load a plate on the Labware page; it appears in the Well card. Choose a
  well (typing "b3" works), Level top, Offset Z 5: the tip stops 5 mm over
  the well. Then bottom and a small offset. The path from the dish goes up
  and over the labware, not through it (the robot's own path; this move is
  deliberately not force_direct).

- [ ] **A pickup and a deposit are two clicks.**
  Tip on, "Aspirate at a cuboid" and "Dispense at a well" on, Click to move
  on: click a cuboid - the tip comes down and aspirates at once; Go to
  well - it goes there and dispenses. With no tip both are refused before
  anything moves.


## Commit 25 — the shake pose taught on the Picking page

- [ ] **Teach shake here, then Go to shake.**
  On Picking, with no `shake` in the profile: the "Shaking the dish" card
  says so, Go to shake is grey and Start lists the missing position. Jog the
  tip into the medium clear of the cuboids, Teach shake here: the card shows
  the coordinates, the button reads "Re-teach shake here" and `shake`
  appears once in the jog panel's list. Drive away, Go to shake: the tip
  goes up, across at the travel height and straight down to the same spot.
  Re-teach elsewhere: still one `shake`, with the new coordinates. During a
  run both buttons are grey.

- [ ] **The run shakes where it was taught.**
  Start a run on a crowded dish: when nothing is isolated the tip goes to
  the taught `shake`, not to a fixed point, and stirs ±10 mm in X there.

- [ ] **A pose taught at the top is reached 1 mm under it.**
  With `tip_calib` taught straight after a retract (Z at the top), Start the
  pipette calibration: the log says "driving to tip_calib", the tip retracts,
  goes across and ends 1 mm under the stored Z, and the calibration goes on
  - no "Destination out of bounds in the Z-axis". The same for Go to on the
  calibration page, Go to the dish, and Go to in the jog panel's list. Start
  a picking run with `observe` taught at the top: it reaches `observe`.

## Commit 26 — a camera taken away is opened again

- [ ] **Another app takes the upper camera, and it comes back.**
  With the upper camera live in the GUI, open the camera preview in
  Windows Settings (Bluetooth & devices → Cameras) or the Camera app. The
  console prints the MSMF `OnReadSample ... -1072873822` warnings; within
  a second the picture shows "camera lost - reconnecting" under the
  caption, with the last frame behind it, and the log says the device
  stopped delivering frames. Close the preview: within a few seconds the
  box goes away, the feed is live again at the same resolution and the
  log says "reconnected after N attempts" - no restart of the GUI.

- [ ] **A tuned focus survives it.**
  On the lower camera, move the focus slider, then take the camera away
  and give it back as above: the lens comes back at the tuned focus, not
  the profile's.

## Commit 27 — Robot & Deck

- [ ] **The tab and its cards.**
  The tab that was Labware reads "Robot & Deck". On it, top to bottom:
  Deck modules, Definition (its list a quarter shorter than before), Slot,
  Tip. No Re-read under Slot, no Return to rack under Tip.

- [ ] **A well typed into the Tip chooser.**
  Select a slot holding a tip rack: the wells run A1 … A12, B1 … H12. Type
  `c7` and press Enter: C7 is chosen. Type `z1` and press Enter: it goes
  back to C7. Pick up tip takes the chosen well.

## Commit 28 — Liquid handling: cuboid washing

Replaced by commit 29: the washing page and `workflows/wash` are gone.
Kept for the record of what washing needs when it returns as a program.

- [ ] **Go above the well lands over the right well.**
  With a new setting (nothing saved yet), Go above the well puts the tip
  5 mm over the middle of the chosen well as the plate's description has it.
  Well off, or over the neighbour: the plate is loaded in the wrong slot, or
  without its module offset (the status bar says which).

- [ ] **Save center and top here, then Go to the saved center.**
  Jog the tip to the middle of the well, level with the rim, and save. The
  saved numbers are small - fractions of a millimetre in X and Y, a few
  millimetres at most in Z. Go to the saved center of *another* well: the tip
  is on its middle, level with its rim. A Z tens of millimetres off means the
  pose was read before the move finished, or a module offset changed.

- [ ] **Show where the tip will draw.**
  The tip goes down beside the cuboid, "Tip height above the bottom" over the
  bottom, and touches nothing. Touching the bottom: the depth is too large.
  Over the cuboid: the shift is too small, or its sign is the wrong way.

- [ ] **The first well and the pause.**
  Start washing on a real plate: the tip is emptied into the waste first
  (blow out, a small shake), then the first well is drawn slowly and the tip
  rises in small steps. The run pauses; the cuboid is still in the well.
  Continue washes the rest, emptying the tip whenever the next draw would
  overflow it and once at the end.

- [ ] **Stop, put back, continue.**
  Stop during a draw: the tip rises and "The tip holds N µl" is shown. Put
  the liquid back returns it to that well, slowly, at the same spot; the well
  shows as not washed again. Continue empties the tip, pauses after that well
  again, and does not draw from any well washed before the stop.

- [ ] **Nothing else moves the robot while it washes.**
  During a run the jog keys and buttons, Go above the well and the other
  setup buttons are greyed out. P pauses and continues, Esc stops.

- [ ] **The first draw after the tip is emptied is accepted.**
  The robot refuses an aspirate in place straight after a blow out; the
  shake is a well-based aspirate and dispense, which is what resets the
  plunger. If the first draw of a run fails with "previous blow out", the
  shake did not happen.

## Commit 29 — Liquid handling as blocks

- [ ] **The tab is on the right.**
  The tabs on the right edge read Liquid handling, Manual control, Log.

- [ ] **The plate map is the deck's.**
  With a plate and a reservoir loaded on Robot & Deck, both are in the
  Plate list. A row selected and New group: the row turns the group's
  colour. Selecting part of it and New group again moves those wells to the
  new group.

- [ ] **A well of the group, a well of the reservoir.**
  Group of A1-A3: Aspirate 20 µl @ this well, bottom +1 mm; Dispense
  (everything in the tip) @ a well, the reservoir A1, top +5 mm. Try this
  step on the aspirate: the tip goes into A1 of the plate (or the selected
  well), 1 mm over the bottom, and draws. Try the dispense: it goes to the
  reservoir and empties there.

- [ ] **A saved point.**
  On Manual pick, save a memo point as "waste" (see Points there). It is
  offered under At a point at once; the calibration position, the shake
  position and the position over the dish never are. Tick "points" on the
  picture: a green ring with the name sits where the point is, and follows
  the deck as the gantry is jogged.

- [ ] **The run.**
  Start asks first, then does A1's whole chain before A2's. The map rings
  the well being worked on and fills the finished ones. P pauses and
  continues; Esc stops after the current command with the tip raised and
  "The tip holds N µl" shown. Continue carries on from the next step - the
  aspirate is not repeated - going back into the well first if that step is
  "where the tip is".

- [ ] **Blow out, then aspirate.**
  A chain with Blow out @ this well followed, in the next well, by an
  aspirate is accepted by the robot (the shake after the blow out prepares
  the plunger). The same with the blow out at a saved point: if the next
  aspirate fails with a message about a blow out, `prepareToAspirate` is not
  accepted by this robot software and the blow out has to be in a well.

- [ ] **Nothing else moves the robot while it runs.**
  The jog keys and buttons and every editing card are greyed out during a
  run.

- [ ] **The program comes back.**
  Close and start the application: the groups and steps are as they were.
  Save as, New, Open brings the saved one back.


## Commit 30 — washing as a liquid handling program

- [ ] **Where reads as it means.**
  The Where list offers each well of the group, one fixed well, a saved
  point, same place as the step before.

- [ ] **A plate's well center, measured.**
  On the Wells card select one well with a cuboid in it (D5), Go to well
  top: the tip stops over D5 where the definition has its top. Jog it onto
  the real center, level with the rim, and Set well center: the line under
  the map says "measured on D5" with the offset, and deck.json has it.
  Go to center with another well selected lands on that well's real center
  and rim. Well depth appears under the line, filled from the definition;
  set the real one. A step on this plate now says "x, y from the measured
  center". Forget drops it; the line says it is not measured.

- [ ] **The measured bottom.**
  A step @ each well, Level "bottom (measured)", Z +2.5 mm: Try this step
  puts the tip 2.5 mm over the bottom the depth says, at the real center.
  Level "bottom" with the same Z goes where the definition's bottom is,
  still at the real center. With no center measured, Start lists "the
  measured bottom of slot N is not known".

- [ ] **Auto refill is for a source only.**
  An aspirate at each well of the group has no Auto refill box; switching
  Where to one fixed well shows it.

- [ ] **A wash.**
  Group of the wells with cuboids, Pause after the first well on, tip 200 µl:
  Aspirate 100 µl @ each well, bottom (measured) +2.5 mm, X -2 mm, 10 µl/s,
  Lift slowly; Dispense, Auto empty @ one fixed well
  (waste), top +5 mm, 200 µl/s. Start: the first well is drawn beside the
  cuboid, the tip rises 2 mm slowly in one move, and the run pauses to look at
  it - the cuboid is still in its well. Continue: the waste is visited after
  every second well and after the last, with a blow out each time; no
  retract between wells.

## Manual control: the well center

- [ ] **Go to well uses the measured center.**
  With a well center measured on Liquid handling, Manual control's Well
  card says "Well center measured on …". Go to well, Level top, Offset Z
  0, picking well offsets 0: the tip lands on the real center, where
  Liquid handling's Go to center puts it, and the jog panel says "from the
  measured center". Forget well center: Go to well goes to the
  definition's center again.

- [ ] **Set well center from Manual control.** (Gone, #63: Manual
  pick's Well center step.)
  Choose the plate and one well, Go to well top: the tip stops where the
  definition has its top, and the card says to jog onto the real center.
  Jog there, level with the rim, Set well center: the line shows the new
  offset, and Liquid handling's Wells card shows the same measurement at
  once. Choosing another plate greys Set well center.

- [ ] **American spelling.**
  Center, color, analyze on every page (Picking's "Detect cuboids",
  Liquid handling's "Set well center", Camera calibration's "Center it
  under the crosshair").

---

## Redesign, phase 1 — theme and components

Look at them with `python -m micropick.gui.gallery --mock` on the bench
machine (1920x1080, Windows).

- [ ] **The text is Inter and the numbers JetBrains Mono.**
  Both are bundled in `gui/theme/fonts` and registered at start. Expected
  failure if wrong: Segoe UI everywhere and Consolas in the X / Y / Z line
  of the tip gauge - the files were not found (a build that left
  `theme/fonts` out, see `packaging`).

- [ ] **The window is dark whatever Windows is set to.**
  Switch Windows to light mode and start again. Expected failure if wrong:
  light cards or white inputs - something still follows the OS theme.

- [ ] **A Select shows its drawn chevron, clear of the border.**
  The arrow is an SVG file the stylesheet points at by its full path.
  Expected failure if wrong: no arrow at all - the path had a space or a
  backslash Qt did not read (an install under `Program Files`).

- [ ] **The jog pad's keycaps name the keys of this keyboard.**
  Turn Keys on: ← ↑ → ↓, PgUp, PgDn, and the step keys under the pad.
  Rebind Z up in Settings, open the gallery again: the new key is shown.

- [ ] **Sizes read well from a meter away at 1920x1080.**
  Buttons are 36 px, compact ones 28 px (REDESIGN §12.6). Expected failure
  if wrong: buttons too small to hit on the first try, or labels cut.

---

## Redesign, phase 2 — the shell: Home, tasks as steps

Start the program normally (no `--mock`) on the bench machine, with the
robot on.

- [ ] **The bench's profile is loaded without being asked for.**
  The first start after this change takes the profile written to most
  recently; later starts the one loaded last (`profiles/.last-profile`).
  Expected failure if wrong: Home says "Complete the setup" with all four
  items on a bench that was set up - the wrong profile, or `default` was
  made beside the real one.

- [ ] **Home carries on with the robot's session by itself.**
  (Since phase 3: no Connect.) With the robot on and never powered off,
  start the program: "Robot connected" within a few seconds, and the deck
  and tip are what they were.

- [ ] **Auto pick opens on Deck, done once something is on it.**
  With an empty deck Deck has no ✓ and Next is gray; load a tip box and
  Deck gets its ✓ at once (#39). Start a new robot session: the deck is
  empty again and Deck is no longer ✓.

- [ ] **A dimmed step takes no keys.**
  Open Tip before the camera is calibrated: the page is dimmed under
  "Calibrate the camera first." Press the arrow keys and PgUp: the robot
  does not move. Expected failure if wrong: the jog keys still drive the
  robot under the banner.

- [ ] **The banner's button goes to the thing to do, and the crumb back.**
  "Go to Camera calibration" opens it as Home › Auto pick › Camera
  calibration; a click on Auto pick comes back to Tip.

- [ ] **Auto pick, then Manual pick, opens on Work.**
  With the deck checked, the tip calibrated, the source dish chosen and
  the dish bottom taken (Save dish bottom or Use it), Home › Manual pick
  shows Deck, Tip, Source and Dish bottom ✓ and the Work page.

- [ ] **Esc still stops, Park parks, Light switches the lights.**
  Esc during a jog stops it; a second Esc within 10 s holds the robot.
  STOP shows in the top bar during a picking run only. Park moves the
  head to the back right without homing; the Tip step stays done. Settings
  › Advanced › Home asks, homes, and the Tip step is not done after it.
  Light follows the rail lights.

- [ ] **The log is under Settings.**
  ⚙ Settings, Log at the bottom left: Home › Settings › Log.

## Redesign, phase 3 — auto-connect and the connection monitor

Start the program normally (no `--mock`) on the bench machine.

- [ ] **Nothing moves at start.**
  Robot on, with a robot session left on it: start the program with a
  hand near (not in) the deck. Home says "Robot connected" within a few
  seconds and the gantry does not move. Expected failure if wrong: the
  robot homes at start.

- [ ] **Robot off: Home looks for it.**
  Switch the robot off. Within ten seconds Home says "Connecting to the
  robot…", "This can take a few minutes after it is switched on.", and the setup
  and task cards are dimmed under "Waiting for the robot.". No Connect,
  Disconnect or Start new robot session button anywhere.

- [ ] **Robot on again: it asks to home, and only then moves.**
  Switch it on; once its light stops blinking Home says "Robot needs to
  home", "The robot was restarted. Labware and the tip are unknown." The
  gantry does not move. Home and connect: the dialog says "Keep hands out of
  the robot. The gantry will move."; Cancel - nothing moves. Home and
  connect again, Home: "Homing…" while the robot homes, then "Robot connected". In Auto
  pick, the deck is empty, Deck is no longer ✓ and the tip is unknown until read again.

- [ ] **A cable blip loses nothing.**
  Unplug the robot's cable for ten seconds and plug it back in: "Robot not
  connected", then "Robot connected" again, and Deck is still ✓.

- [ ] **A new address while looking.**
  With the robot off, ⚙ Settings: change the address, Save. The log says
  "the robot is looked for at <address>"; set it back, Save, switch the
  robot on, and it is found.

## Redesign — Robot setup page and Reset setup (#8)

Start the program normally (no `--mock`) on the bench machine, on the
bench's profile.

- [ ] **Home's Setup card.**
  With everything set, Home says "Setup is done" and a click anywhere on
  the card opens `Home › Robot setup`. With something missing it says
  "Complete the setup" and "N steps left: …", and Complete setup opens
  Robot setup, where the same items have a yellow ring.

- [ ] **The rows read as the bench is.**
  Upper and lower camera name the attached cameras; Camera calibration
  says "Done N days ago"; Platforms names each kind with its slots
  ("Picking platform in 5 and 8 · Calibration module in 9"). Each position
  reads `≈ slot <n> · X … Y … Z …`, the slot the head is roughly over
  at that position (the robot's X and Y, not the camera's). No "observe", "shake" or
  "tip_calib" anywhere.

- [ ] **A row opens where it is set.**
  The cameras open their sheet over Robot setup (see #9 below); Platforms opens the deck
  page, Camera calibration its page, the positions the Picking page and
  the tip calibration, each as `Home › Robot setup › <item>`. The
  "Robot setup" crumb comes back to the rows.

- [ ] **Keep setup changes nothing.**
  Reset setup: "Reset the setup?" with Keep setup highlighted. Keep setup:
  every row as it was, and no new zip beside the profile folder.

- [ ] **Reset archives, then clears.**
  Reset setup, Reset: a toast "Setup reset.", every row a yellow ring and
  "Not set.". Beside the profile folder (`Documents\micropick\profiles`)
  is `<profile>-<date>_<time>.zip` holding the folder as it was. The deck
  page's labware, the plate plan, the picking settings (Settings) and the
  dish bottom are as before. With the platforms cleared, jogging no
  longer stops above a module's top until Platforms is set again. Set
  the cameras back from their rows to carry
  on, or restore the zip by hand.


## Redesign — Upper and lower camera sheets (#9)

Start the program normally (no `--mock`) on the bench machine, with both
cameras attached, on the bench's profile.

- [ ] **A camera row opens its sheet.**
  Robot setup, Upper camera: a sheet "Upper camera" over the rows, the
  breadcrumb `Home › Robot setup › Upper camera`. Left, the upper
  camera's live picture; right, "Which camera is the upper camera?" with
  the attached cameras, the upper one chosen.

- [ ] **The picture follows the choice.**
  Choose the lower camera's device: within a few seconds the picture is
  the view from under the dish, and Done is gray until it is. Choose the
  first one back: the deck again. Nothing is saved yet: the Lower camera
  row under the sheet still names its device.

- [ ] **Cancel and × save nothing.**
  Choose the other device, then Cancel: the rows as before. Again with ×.
  Open Picking: the dish camera is the same one as before.

- [ ] **Done swaps.**
  Choose the other device, Done: a toast "Upper camera: <device>. Lower
  camera: <device>.", the two rows swapped. Picking now shows what the
  lower camera saw. Swap them back the same way.

- [ ] **During a run.**
  Start a picking run and open the Upper camera row from Robot setup:
  the picture shows, Done says "Robot busy: …" and changes nothing.

- [ ] **Settings no longer chooses cameras.**
  Settings › General has no Cameras card; the profile's column starts
  with Models.

## Redesign — Camera calibration sheet (#10)

Start the program normally (no `--mock`) on the bench machine, upper
camera chosen, the robot connected, the calibration marker at hand.

- [ ] **The row opens its sheet.**
  Robot setup, Camera calibration: a sheet "Camera calibration", the
  breadcrumb `Home › Robot setup › Camera calibration`. Left, the upper
  camera's live picture; right, five steps, the first open, the others
  gray.

- [ ] **Place and slot.**
  Put the marker on the picking platform, It is in place. The slots are
  the platforms' (all slots on a setup without platforms). Choose the
  marker's slot, Move the camera there: the camera goes over the slot,
  the marker in or near the picture.

- [ ] **Center.**
  The picture says NO MARKER IN VIEW until the marker is in it, then
  MARKER FOUND. Without a saved pixel map a click on the picture moves
  nothing and says to use the arrows; with one, a click on the marker
  brings it to the middle. It is centered is gray until the marker is
  near the middle.

- [ ] **The sweep is a run.**
  Start calibration: a progress bar, "Don't touch the robot.", the jog
  panel gone, STOP and the pill "Calibrating the camera" in the top bar.
  Close the sheet with ×: the sweep goes on; the pill brings the sheet
  back. Esc (or STOP): the sweep stops at the next pose, "Calibration
  stopped. Center the marker again.", step 3 open again, nothing saved.

- [ ] **The result.**
  A full sweep: green "Camera calibrated and saved.", the Camera
  calibration row says it was done just now. With the marker off center
  on purpose: yellow "Not accurate enough: the marker was off center."
  with Redo calibration
  (back to step 3) and Save anyway (saves, then green).

- [ ] **Parameters.**
  Step 4 names the marker side, dictionary, grid and degree; change the
  grid in Settings and the line follows.

- [ ] **From the Tip step.**
  Auto pick on a profile without a pixel map, the Tip step's banner "Calibrate the camera
  first." › Go to Camera calibration: the sheet over the Tip step,
  `Home › Auto pick › Camera calibration`; the arrow keys move only the
  sheet's jog panel.

---

## Redesign — JogPad and quieter camera chrome (#11)

Start the program normally (no `--mock`), robot on, a profile with both
cameras.

- [ ] **The pad is over the picture.**
  Manual control, Liquid handling, the camera calibration sheet's centering
  step, the tip calibration: Controls at the picture's bottom-right,
  XY arrows, Up / Down, Step ‹ 0.5 1 2 › mm; on Picking too.
  Expected failure if wrong: two pads on one picture, or a pad left over
  another page.

- [ ] **Its title folds it.**
  ▾ Controls at the pad's top left, Undo step and Keys at the right: a
  click on the title folds the pad to its header in the corner, Undo step
  and Keys still there, again opens it. Over a bright picture the plate is
  evenly translucent, the area between the arrows and Up / Down too.

- [ ] **The fold is the screen's.**
  Fold Controls on Manual pick's Run, go Home and come back: still folded,
  and Liquid's and the Tip step's are as they were. Picking starts folded,
  the others open, each time the program starts.

- [ ] **Keys folded.**
  Keys on a folded Controls: the list shows under the header, the buttons
  it names folded away. Keys off: it goes.

- [ ] **A press moves one step.**
  At step 1 each arrow moves 1 mm, Up / Down 1 mm in Z; ‹ › and + / - walk
  0.01 … 10 mm, 5 drawn yellow and 10 red. Over the tip calibration's
  lower camera the arrows move the tip as on the picture, each button's
  tooltip naming the robot axis; during the touch-up there is no Up / Down.

- [ ] **Gray while the robot is busy.**
  Start a liquid run: the pad's arrows are gray, the tooltip "Robot busy:
  liquid run." During a long Go to the tooltip names that move. Lock: gray
  too.

- [ ] **Keys.**
  Keys (or H): ← ↑ → ↓ PgUp PgDn on the buttons, the step keys, U, H and
  the page's keys (A, D on Manual control) under them; no Space (#62),
  and Space saves nothing. Rebind Z up in
  Settings: the keycap follows. Click into a number field: the pad says
  the keys are the field's; Enter gives them back.

- [ ] **Only what is used is on the picture.**
  Top-left: X … Y … Z … in a fixed font and nothing else from the camera
  - no resolution, crop, zoom or fps. Bottom-left: one row, "Wheel zoom
  Middle-drag pan Double-click fit" (Manual control: its clicks first),
  cut short rather than running under the pad. Picking's size histogram
  sits above that row.

---

## Redesign — Platform and position sheets (#12)

Start the program normally (no `--mock`), robot on, a profile with the
upper camera chosen, the dish and its holder and the crosshair disc at
hand.

- [ ] **Platforms.**
  Robot setup, Platforms: a sheet with the deck as 3×4 big slots (10 11 12
  at the back), slot 12 gray. Click slot 5, "What stands in this slot?":
  Picking platform; slot 8 the same; slot 9 Calibration module. A slot
  with a platform has a dashed border. Save changes (gray before the first
  choice): "Saved: slot 5 · Picking platform, slot 8 · Picking platform,
  slot 9 · Calibration module" under it, Save changes gray again; Done
  closes without a question, the row "Picking platform in 5 and 8 ·
  Calibration module in 9" with a ✓. Reopen (no Cancel), set slot 5 to
  Nothing, ×: "Save the changes?" - Discard: slot 5 still has its
  platform. Again with Done and Save: slot 5 has nothing.
  Expected failure if wrong: a plate in slot 8 drawn in the hazard color
  on the Deck step after Save changes.

- [ ] **Platform types.**
  Slot 5's Height to 63.9 mm, Save changes: the Deck step's plate in slot
  5 is loaded at that height. New type "Tall platform", 80 mm, Add type:
  it is in the list; put it in slot 4, Save changes. Delete type on slot
  9's Calibration module, Save changes: slot 9 keeps it, the list no
  longer offers it. A name already there (any case): "There is a type of
  that name."

- [ ] **Order.** Positions read Calibration position, Shake position,
  Position over the dish, on the page, in Home's "N steps left" and in a
  step's banner.

- [ ] **Calibration position.**
  Four numbered cards; only "It is there" is active. It is there: the
  mini deck lights up. Click the calibration module's slot: the camera
  goes over it, the slot is marked, "It is centered" lights up. Center
  the crosshair (a click on the picture with a pixel map, else the
  arrows), It is centered, Save position: "Saved: slot 9 · X … Y … Z …",
  the sheet stays open. The Tip page's disc position (Go to saved
  position) goes to the same pose: it is `tip_calib`.

- [ ] **Position over the dish.**
  "Place the source dish" (It is there), the mini deck with nothing
  chosen, "Center the dish rim around the crosshair" (It is centered),
  Save position. Done: the row "≈ slot 8 · X … Y … Z …" with a ✓.

- [ ] **Shake position.**
  "Lower the tip into the hole", the yellow "The camera can't see this.
  Watch the robot itself.", the pad over the picture. Jog the tip into
  the holder's hole, It is in the hole, Save position: the row has a ✓.

- [ ] **Only Save position stores.**
  Open a saved position, move the robot, Done or ×: the row's value is
  unchanged, nothing asked. No Cancel button.

- [ ] **Saved position on top.**
  A saved position shows "Saved: slot …" and Go to saved position. From a
  fresh home it goes back there, never asking the robot for a height
  above its top, and marks the steps before Save done.

- [ ] **Busy.**
  While a liquid run has the robot, Save position, the mini deck and Go
  to saved position are gray, the mini deck's tooltip naming the run.

## Redesign — Deck step and Deck confirmation (#13)

Start the program normally (no `--mock`), robot on, a profile with the
platforms set (Picking platform in 5 and 8, Calibration module in 9), a
tip box and a 384 plate at hand.

- [ ] **The deck.**
  Home, Auto pick: the Deck step shows twelve tiles, 10 11 12 at the back.
  Slots 5, 8 and 9 carry their platform's chip and a dashed border; slot
  12 is a faint TRASH on stripes and a click on it does nothing. A tip box
  shows a teal tip icon, "96 tips · 200 µl"; a plate a violet icon and
  "384 wells · 112 µl · flat".

- [ ] **What goes to a slot.**
  Click an empty slot: "What goes to slot 7?", a search, Frequent · All ·
  Plates · Tips (Frequent chosen). Frequent lists what was loaded lately,
  then the custom labware. Tips lists only tip boxes. On All, type "384":
  every 384 plate, each with its well volume; on Tips the same search
  finds nothing. Click one: a toast "… is in slot 7.", the tile shows it,
  the panel reads "In slot 7".

- [ ] **In a slot.**
  Remove (or the × at the tile's top right): "Slot 7 is empty.", the tile
  reads Empty. There is no Put something else.

- [ ] **Drag.**
  Drag the plate from slot 7 onto empty slot 4: it moves, the robot
  session reports it in 4 (Manual control's deck agrees). Onto a filled
  slot: nothing happens. Dragged onto slot 5 (a platform), Manual
  control's Go to well top stops above the plate, not inside the
  platform.

- [ ] **No confirmation (#39).**
  Deck's ✓ comes with the first labware and stays through loads, removes
  and drags; it goes only when the deck is empty or a labware sits on a
  platform without its height. Next only moves on.

- [ ] **Change platforms in Robot setup.**
  Opens the Platforms sheet over the step, the breadcrumb "Home › Auto
  pick › Platforms"; Done there updates the chips on the tiles.

## Redesign — Tip step: put a tip on, calibrate the tip (#14)

- [ ] **No tip box.**
  With no tip rack on the deck, the Tip step says "There is no tip box on
  the deck. Add it on the Deck step." Back to Deck opens the Deck step.

- [ ] **Take a tip.**
  A rack in slot 10: the card reads "No tip on" with an outlined tip, and
  "<rack name> · slot 10" as plain text under it (a Select with a second
  rack). The rack's first free tip is ringed and the button reads "Pick up
  A1". Click A3 on the rack: "Pick up A3", the button keeps its width (also
  for H12). Pick it up: the tip comes off A3, a toast "Tip A3 is on.", the
  title "Tip A3 is on" with a filled tip, the rack folds away and the
  Calibrate card moves up, Drop in trash and Drop in place.

- [ ] **Show rack.**
  With a tip on, Show rack opens the rack and Hide rack folds it. A click on
  the rack chooses nothing. Dropped and picked up again, it starts folded.

- [ ] **Drop in trash, Drop in place.**
  Drop in trash: the tip drops into the bin, a toast "Tip dropped.
  Calibration needs a new one.", A3 is gray and A1 is offered. Pick up A1, hold a beaker under it, Drop in place: the tip falls
  where it is, the gantry does not move, A1 stays gray.

- [ ] **Calibrate the tip.**
  Without a tip the card is dimmed with "Put a tip on first." and nothing in
  it takes a click. With one: Go to the disc - the pill reads "Calibrating
  the tip", STOP shows, the chip In progress, the camera goes over the
  calibration position and waits ("Waiting for you"). Yes, calibrate: a bar
  that only turns for about 30 s (no numbers on screen; the routine's lines
  are in the Log), the lower camera's picture. Then the run ends by itself:
  the pill goes, "Tip calibrated." in the toast and the footer, the chip
  Done, Tip gets its ✓, the tip stays over the crosshair. Two clicks in all.
  No numbers on the path, no red mark unless something failed.

- [ ] **Fine-tune by hand.**
  After a calibration Adjust by hand · optional is the path's current item,
  Controls still folded, the step on the status plate, the keys moving the
  tip. Adjust by hand: Controls open (XY only, 0.05 mm), Save gray, Cancel. The
  left arrow (and the left key) moves the tip left on the picture, not
  up. One step: Save lights; Save: "Fine-tune saved.", Adjust by hand again, no
  pill. Adjust, a step, Cancel: nothing saved, the tip stays. Adjust, a step,
  then Next or Home: "Save the changes?" over what is shown next; Discard keeps
  the saved offset. Adjust, a step, Park: the step is gone, nothing is asked.
  Coming back: Adjust by hand gray, "The tip is not over the crosshair.", until
  Redo. Manual adjustment off in Settings: the path ends on Tip found. The shake
  position's sheet keeps its pad open.

- [ ] **Arrows over the lower camera.**
  With the manual adjustment off in Settings, after a calibration the
  picture is the lower camera's: the page's left arrow (and the left key)
  moves the tip left on the picture, not up. Move the tip away: the upper
  camera, its arrows as on every page.

- [ ] **Redo.**
  After a result: Redo on Tip found starts the run again, the camera to the
  disc, the path on Disc in the picture, no link there.

- [ ] **Stop while it waits.**
  Go to the disc, then Esc (or STOP) at the disc: the run ends, "Stopped.
  Nothing was saved." in the toast, the pill goes, the path back on Camera
  over the disc, or at its end after a Redo.

- [ ] **Done follows the tip.**
  Drop the tip and take another: Tip loses its ✓ until calibrated again.
  In Liquid, the Tip step has no calibration and is ✓ once any tip is on.

## Redesign — Plan step and templates (#15)

- [ ] **The plate.**
  Auto pick › Plan with a 384 plate in slot 5: the dropdown reads "Slot 5
  Corning 384 …" and the grid is 16×24. With no plate on the deck: "There
  is no plate on the deck. Add it on the Deck step." and Back to Deck.

- [ ] **Choosing wells.**
  Click wells, a row letter, a column number, drag a box: they turn
  purple and show their count ("1" too), the last chosen ringed. Undo,
  Redo, Select all, Clear; Ctrl+Z, Ctrl+Y, Ctrl+A do the same. New plan
  with no wells: "Choose the wells on the plate."

- [ ] **A count per well (#43).**
  Click A1, type 5; click A2, type 3: A1 shows 5, A2 shows 3. Click A1:
  ringed, the field reads 5. Ctrl+click A2: the field is empty, "mixed";
  type 4: both show 4. Click a ringed well: it stays, selected. Ctrl+click
  a selected well: out of the selection, still planned. Remove (or Delete,
  Backspace after a click on the grid): the selected wells leave the plan,
  Undo brings them back; Remove is gray with nothing selected.

- [ ] **Use this plan, Edit plan (#45).**
  Column by column, Use this plan: a toast "Plan ready" and no green
  line under the button; Plan gets its ✓ and Next is enabled. The grid,
  the plate, the field, the order and the templates are gray; the button
  reads Edit plan. Edit plan: all editable again, Plan loses its ✓ until
  Use this plan. During a picking run Edit plan is gray.

- [ ] **Save as template, Save changes.**
  Save as template: a name field in place, Save. A name already used is
  refused under it. Change a well: Save changes to template appears and
  writes over that template; the folder `Outputs/templates/plate-plans`
  holds one file for it. No dialog asks "are you sure".

- [ ] **A template (#44).**
  A template: every saved one is listed, each "<name>" over "24 wells ·
  saved Oct 20". A click: "Which plate does it go on?" and the mini deck,
  only the slots holding that plate type clickable - also with one such
  plate. A click on a slot puts the plan on that plate. Open from file…
  takes one from anywhere (a colleague's). A template for a plate type
  not on the deck: "Put a <type> on the deck." with Go to Deck, nothing
  loaded.

- [ ] **Old presets.**
  Files from `Outputs/plate_plan_presets` (Save plan as preset… before)
  are in the list under their file name, and moved to
  `Outputs/templates/plate-plans`.

- [ ] **No run file.**
  Nothing on the Plan step names or opens the run's progress file.

## Redesign — Dish bottom step (#16)

- [ ] **No camera.**
  Auto pick › Dish bottom (and Manual pick › Dish bottom, the same
  screen): no picture; "Tip height", Z in big numbers, mm, then three
  cards side by side - Coarse, Finer, Finest - each with Up, its step
  carousel (1, 0.5, 0.1 mm) and Down, gray until it is the one lowering.
  Readable from two meters.

- [ ] **Last time.**
  With a dish bottom measured before: "Last time: Z 66.40 mm" over
  "3 days ago" (today, yesterday) and Use it. Use it: a toast "Using the
  last dish bottom.", the green "Dish bottom: Z 66.40 mm", the step gets
  its ✓. Home and connect (a new robot session): the ✓ is gone, the value
  stays. On a profile that never measured it, no Last time card.

- [ ] **Measure.**
  Move the tip (in Coarse): the tip goes over the middle of the dish,
  about 10 mm over the last dish bottom. Down 1 mm at a time until it
  touches; It touches: the tip goes up 1 mm and Finer lowers at 0.5 mm;
  It touches again: up 0.5 mm, Finest at 0.1 mm; down until the dish
  bottom bends; Save dish bottom: a toast "Dish bottom saved.", the tip
  goes up, the green line shows the new Z with Redo, the step gets its
  ✓. A value more than 3 mm from the last asks first; Cancel saves
  nothing. Redo: back to Move the tip, each card at its first step, the
  value and the ✓ kept until a new one is saved. Only the current card's
  button is blue.

- [ ] **The Z after the Tip step (#56).**
  With the tip low over the crosshair on Tip, go to Dish bottom (Next,
  or answer the fine-tune's Save / Discard): the tip rises, and once it
  stops the Z readout shows where it stopped (about 118 mm), not the Z
  from before the raise.

- [ ] **Where the dish is.**
  On the right "Over the dish · slot 9" - the slot the camera is over,
  not the tip's (Robot setup's row says the same). Change: Robot setup's
  position sheet over the task. Save it in another slot: the shake
  position is cleared, Picking's Shake the dish reads Set shake position
  and opens its sheet.

- [ ] **Keys.**
  While lowering, PgUp / PgDn move the tip up and down and + / - change
  the step, as rebound in Settings' Keys; the arrow keys do nothing.
  Before Move the tip, and after saving, PgUp/PgDn do nothing. Keys (or
  H) puts PgUp / PgDn on every card's Up and Down and - / + on its step
  arrows, as keycaps like the jog pad's, and lists them under the cards
  (#58). Rebind Z down in Settings: the keycap and the list follow.

- [ ] **Busy.**
  While a run or Park has the robot, Move the tip, Up and Down are gray,
  and so are their keys.

- [ ] **Picking.**
  The Picking page has no dish bottom card any more; its steps are 1-5.

## Redesign — Picking step (#17)

- [ ] **Layout.**
  Auto pick › Picking at 1920×1080: the dish picture on the left with
  "N cuboids found" after Detect cuboids; on the right, without a
  scroll bar, Detect cuboids and Shake the dish side by side, then
  Cuboid sizes (open) and Plate · slot N (folded). Opening one folds the
  other. No camera name, no step numbers, no checklist on the page.

- [ ] **Cuboid sizes.**
  The summary is the size window ("250–500 µm"). Drag a white line: the
  count under the plot follows, the window is saved (picking.json
  archived, old -> new in the log) and the summary changes. The line under
  it is "0.5 mm above the bottom · 15 µl" (the profile's). More settings
  opens the picking settings; All measures the cuboids' figures.

- [ ] **Footer between runs.**
  With a step before it not done: NOT READY and the banner's sentence,
  Start picking gray. All done: READY and "N wells to fill." (after an
  analysis, "N cuboids found."), Back and Start picking (blue). Start asks
  the usual confirmation and nothing else.

- [ ] **Footer during a run.**
  PICKING (blue) and "Well A3 of 24 · taking a cuboid", "… · checking the
  tip", "… · putting the cuboid in"; Pause and Stop run (red) on the
  right. The Plate card opens by itself; filled wells turn violet, the
  current well is ringed, the summary counts up ("3 of 24 wells").

- [ ] **Pause and Camera to dish center.**
  Running, the button over the picture reads "Pause to move the camera"
  and is gray. Pause: PAUSED (yellow), the button becomes Resume (yellow),
  the picture button "Camera to dish center"; with an empty tip it moves
  the camera over the dish (Resume gray while it moves). Resume carries on
  with the run, which looks at the dish again from the picking position.

- [ ] **Waiting for you.**
  An empty dish: after the configured number of shakes - count them on the
  robot - WAITING FOR YOU (yellow) and "No cuboids found after 3 shakes.
  Add cuboids, or finish by hand." The number is the shakes seen. On
  Windows the taskbar button flashes once. Resume: the run goes on.

- [ ] **The end.**
  A filled plan: DONE (green), "The plan is filled.", Back and
  Start picking. Stop run mid-run: READY, "Stopped. The tip may still hold
  cuboids: put them back into the dish."

## Redesign — Manual Work: the picture and points (#18)

- [ ] **Over the picture.**
  Manual pick › Work at 1920×1080: top left the tip gauge ("IN THE TIP",
  0 µl, an empty tip drawn) with X, Y, Z under it; nothing over the top
  right (no "A click moves the [Camera | Tip]", no Camera / Tip back to
  center, #59); bottom left one row "Left click move · Right click
  mark a point · Wheel zoom · Middle-drag pan". No camera name, no Click
  to move, no Mark points.

- [ ] **A left click moves the camera.**
  One click and the clicked spot comes under the crosshair, once (a
  double click moves nothing); the tip stays at the travel height. A
  click near the deck's edge that would leave the envelope: a red toast
  "Not reachable: …." and nothing moves. While another page's job has
  the robot: a click says "Robot busy." and nothing moves.

- [ ] **Points.**
  One card, Points, that does not fold: one list, ☆ for a memo point and ★
  for a saved one, Point at crosshair, Tip to crosshair and New points get Z
  under it. A right click marks M1 where the tip would go down, draws it in
  cyan with a ring round it (red with "!" when below a platform's top),
  chooses it in the list, says "M1 added." and the cards do not move. Turn
  the Points chip off: the points leave the picture, a right click adds M2
  to the list only; on again: both back. Delete on a chosen row takes that
  point from the list and the picture; the next right click takes its
  number. Nothing moves when marking.

- [ ] **The star (#112).**
  ☆ asks a name, the point's own in the field; "M1 kept for later." and it
  is ★. ★ asks "Remove M1 from saved points?", Keep the primary button;
  Remove makes it ☆ with its name and Z, and one Undo brings ★ back. On
  Liquid's Plan ★ cannot be clicked.

- [ ] **A double click never moves the robot (#112).**
  Tip on. Choose the top row, then double-click a cell of the row under it,
  fast and slowly: the box opens or nothing happens, never Tip to or Camera
  to. Esc in the box puts the value back and stops nothing.

- [ ] **Undo and Redo of the points (#96).**
  Undo and Redo are on Points' title row, gray with nothing to undo or
  redo. Do each, and Undo and Redo it: add a point, Delete it, type its Z,
  Z from tip, ☆ on it, ★ on it, rename a saved point, Delete a saved point.
  Each comes back in the list and on the picture, for both kinds. Make
  four moves: Undo goes back three, then is gray. A new move after an Undo
  grays Redo. Delete a saved point, Undo, close the program and start it
  again: it is listed, with its Z. Ctrl+Z does nothing here. Memo point
  made on Liquid's Plan: Undo on Manual pick takes it off.

- [ ] **Saved points (#61, #62).**
  With the three Robot setup positions set: none of them is in the points
  lists, and none is drawn on the picture (no "observe"). Save M1, name it
  "trough": the row leaves Memo for Saved, reading trough
  with "Saved", a cyan ring with a filled disc on the picture. Save another as "trough" or
  "shake": refused in a red toast, the memo point stays.
  Double-click trough's name, type "bench", Enter: renamed. × on bench
  asks "Delete point bench?"; Keep keeps it. Restart: bench is listed
  again, the memo points are gone.

- [ ] **Camera to, Tip to, Point at crosshair (#66).**
  Point at crosshair: a memo point under the crosshair, Z "travel"; ☆ on
  it: its Z is the travel height then.
  Click a row: Camera to, Tip to, Z from tip and Delete unfold under it;
  click another: they go there; click it again: they fold. Camera to: the point comes under the crosshair, the
  tip stays at the travel height. Without a tip Tip to is gray ("Put a
  tip on first."); with one, Tip to takes the tip down to the point's Z.
  A double click on a row edits it and moves nothing.

- [ ] **Tip gauge and past picks.**
  Tip on, Aspirate 15 µl: the gauge reads 15 µl and fills; Dispense All:
  0 µl.
  Drop the tip: 0 µl. The Past picks chip (filled while on): a violet ring at
  each spot aspirated from, the last bright, the older faint; off hides them.

- [ ] **Detect cuboids and Look at.**
  Detect cuboids, on the picture, draws the contours and "N cuboids found ·
  M in size" under the chips; a click on a cuboid only moves the camera there, the tip
  stays up. Look at: the dropdown shows the whole deck with what is on it;
  a slot chosen sends the camera over its center (check slot 8 by eye).

## Redesign — Manual Work: Aspirate and Dispense (#19)

- [ ] **Aspirate's settings line.**
  Manual pick › Run, From In a well, a plate not measured: the Aspirate
  card reads "0.5 mm above the drawing's bottom · 10 µl · 50 µl/s" (the
  profile's pickup offset, volume and flow rate). Confirm that plate on
  Well center (Save, or Use it): "0.5 mm above the measured bottom · …",
  and choosing another plate follows it; By hand at a Z: "Z 66.6 mm · …". Change unfolds
  the height, Volume and Speed, and the line follows them. No
  step numbers on the cards, no moved-cuboids counter, no Finish.

- [ ] **By hand: under the crosshair (#60).**
  From: By hand, a tip on: Aspirate is enabled at once, no click needed,
  and no hint to click a spot. With the crosshair over a slot with no
  bottom confirmed in this robot session Change shows Height Z | Above
  the bottom with Above gray and Z chosen; over the source dish's slot
  after Dish bottom (or Use it) Above is enabled and chosen.
  Put the crosshair on liquid, Aspirate: the tip goes over the crosshair's
  spot at the travel height, down to the height, aspirates, and the
  camera comes back to where it was - the same spot under the crosshair;
  the gauge is the only answer. Snap to found cuboids on: Analyze the
  dish, click a cuboid off its center: the camera puts the crosshair on
  its center, nothing is aspirated. A click beside every cuboid moves to
  the point. Aspirate as soon as I click: one click aims, aspirates and
  comes back over the spot.

- [ ] **By hand from a 24 well plate (#90).**
  A 24 well plate on the deck, confirmed on Well center (Save, or Use it),
  a tip on, a little liquid in a well. Manual pick › Run, By hand, the
  crosshair in a well, off its center: the line reads "0.5 mm above the
  bottom of slot 5" (the plate's slot), and it follows when the camera
  goes to the dish's slot, and turns to a Z, Above gray, over an empty
  slot. Back in the well, jog the tip down to the real bottom and compare
  its Z with the measured one (rim less depth): the depth is the drawing's
  and the rim was set by eye. Raise it again and Aspirate: 0.5 mm must
  clear the bottom. Too low or too high, note by how
  much: 0.5 mm is the default for every source (#86). Crosshair on the
  plastic between two wells: Aspirate says "The spot is not in a well."
  and nothing moves; with Aspirate as soon as I click, a click there too.
  Close to a wall, inside: check the tip clears it, as the refusal leaves
  0.5 mm for the tip's radius (`TIP_RADIUS_MM`, a figure not measured).
  Height Z on the same spot is not refused: the height is yours, so use a
  Z over the rim.

- [ ] **In a well, At a point.**
  In a well: Plate and Well; Aspirate goes to the well's bottom + the
  height (watch it, it must not hit the bottom): the measured bottom
  (rim less depth) on a plate confirmed in this robot session, else the
  drawing's. Check the 0.5 mm on a confirmed plate: the rim is set by eye
  and the depth is the drawing's. At a point: the points
  list (drawn on the picture only now), × deletes; Aspirate goes to the
  chosen point at its Z. Aspirate is gray until a well or a point is
  chosen.

- [ ] **Dispense.**
  Empty tip: Dispense folded, its button gray. After an Aspirate it opens
  by itself, on Part with the picking volume. Part 6 µl of 15: the gauge reads 9 µl;
  All: 0 µl and the button grays. In a well: with a plate plan made, the
  mini map shows Filled / Planned; a click on a well chooses it and the
  button reads "Dispense into A7"; the tip goes into that well at
  the deposit height, above the same bottom as Aspirate's. At a point: "Dispense 6 µl".

- [ ] **Aspirate and Dispense fold (#114).**
  Manual pick's Run, tip empty: Aspirate open and primary, Dispense folded, outlined
  and gray, "The tip is empty. Aspirate first." in yellow and whole. A press on the
  gray button folds nothing; either card opens at any time, one at a time, the open
  one's border in its color. Aspirate by the button, then by a click with Aspirate
  as soon as I click: Dispense opens and is primary. Press Aspirate with its card
  folded: it works. A Dispense leaving some keeps the card, one that empties the
  tip opens Aspirate. In place's buttons and keys switch no card. Change a setting:
  the summary follows, the whole in its tooltip. Type in Volume and Aspirate: the
  arrows move the robot afterwards, no other field has the keyboard.

- [ ] **Dispense has its own speed (#97).**
  Dispense open: under Part | All the line reads "50 µl/s" (the profile's
  flow rate), and Change unfolds Speed. Set it to 10 and leave Aspirate's at
  50: the line says 10 µl/s and Aspirate's still 50. Aspirate, then Dispense
  here: the toasts say "at 50 µl/s" and "at 10 µl/s", and the liquid goes out
  visibly slower. A and D do the same. Close the program and start it again:
  Dispense is back on the profile's flow rate.

- [ ] **Well center.**
  Work has no Well center card (#63: it is Manual pick's step before).
  A plate measured there: Aspirate and Dispense in a well go to the
  measured center, confirmed or not; the height follows the confirmation.

## Redesign — Well center step and skipped plates (#21)

- [ ] **The list.**
  Two plates on the deck, Liquid › Well center: both "Not measured yet"
  (gray ring), the first selected. The upper camera on the left, the jog
  pad over it. With none on the deck: "No plate on the deck."

- [ ] **Measure a plate.**
  Choose a well, Move the tip: the tip goes to its top center as the
  robot has it (watch it: no crash into the rim). It is centered and It
  is level take turns with the arrows; Well depth reads "10.67 mm, from
  the plate's drawing" (for a Corning 96). Save: "Well center saved.",
  the row reads "Measured today" with a green ✓, the next plate is
  selected. Liquid's steps on that plate now go to the measured center.

- [ ] **Skip.**
  Skip this plate, use the drawing: "Not measured: using the drawing", a
  yellow ✓; the step is ✓ once every plate is measured or skipped. Home
  the robot (a new robot session): the skipped plate reads "Not measured
  yet" again; a measured one stays measured.

- [ ] **A run's plate.**
  During a liquid run into a plate, its Save is gray, the tooltip naming
  the run.

- [ ] **Keys (#58).**
  The pad's Keys (or H): ← ↑ → ↓, PgUp, PgDn on its buttons, - / + on
  the step arrows, U and H listed under it, as bound in Settings.
  The position and camera calibration sheets' pads have Keys too.

- [ ] **The movement keys (#57).**
  On Well center the arrows and PgUp / PgDn move the tip one step of the
  pad's, as rebound in Settings' Keys (no restart). Then on Dish bottom
  (while lowering), Manual pick's Work, Picking, the Tip step's fine-tune
  and Robot setup's position and camera calibration sheets: each moves
  the robot by one step, never two. On Home and Liquid's Plan and Run
  they move nothing.

## Redesign — Finish by hand (#20)

On the bench's profile, with a plan of a few wells and a dish with fewer
cuboids than the plan needs.

- [ ] **Wells a run left empty are named.**
  Let the run end short (or Stop run): under the plate card "N wells were
  not filled" and the wells, with Finish by hand.

- [ ] **Finish by hand.**
  Finish by hand: `Home › Manual pick`, Work, Dispense open on In a well,
  the first empty well chosen on the plan's plate, the other empty ones
  shown. The tip, the dish bottom and the plan are the ones the run had:
  no step of Manual pick asks for them again.


## Redesign — cleanup and Focus in the camera sheets (#24)

On the bench's profile, both cameras attached.

- [ ] **Focus opens the camera's own window.**
  Robot setup, Lower camera: Focus opens a window with the lower camera's
  picture and the focus row (slider, number, default, save to profile).
  Move the slider: the picture sharpens and blurs live. Save to profile:
  "saved to the profile: focus …". Close the program and start it again:
  the lower camera opens at that focus.

- [ ] **Focus only for a chosen camera.**
  In the Upper camera sheet, choose a device the profile does not have
  (after Reset setup, say): Focus is gray. Cancel closes the focus window
  with the sheet.

  (Both checks above are replaced by #31's below: no Focus button, no
  focus window.)

## Robot setup walk-through — camera sheet opening and focus (#31)

- [ ] **Opening camera.**
  Robot setup, Lower camera: until the first picture, a turning bar and
  "Opening camera…" in the middle of the picture, never "no camera".
  Choose the other device: the same while it opens.

- [ ] **Focus under the picture.**
  On the lower camera's picture, the Focus row (slider, number, Default
  (960)) is under it as soon as the picture shows; none on the upper
  camera. Move the slider: the picture sharpens and blurs live. ×, then
  open the sheet again: the focus is the saved one. Move it again, Done;
  close the program and start it again: the lower camera opens at that
  focus.

- [ ] **Focus for a device only previewed.**
  After Reset setup, Lower camera: the focus row shows on the lower
  device's picture before anything is saved; Done saves the camera and
  that focus.

## Auto pick order, the Deck tiles and the picker (#39, #40, #41, #42)

Start the program normally, robot on, a profile with the platforms set
(Picking platform in 5 and 8, Calibration module in 9), a tip box and a
plate at hand.

- [ ] **The order.**
  Auto pick's steps read Deck, Tip, Dish bottom, Plan, Picking. With a
  calibrated tip on and the dish bottom found, Auto pick opens on Plan.

- [ ] **Plan needs a plate.**
  With only a tip box on the deck, Plan is dimmed under "Put a plate on
  the deck first."; Go to Deck opens Deck. Load a plate: Plan is open,
  even with the dish bottom not found. Picking still names the dish
  bottom.

- [ ] **The tiles.**
  The deck is about as wide as it is deep at any window size (never
  stretched sideways), centered in its column. A long custom labware name
  wraps inside its card, or ends in "…", never past the card. Each filled
  tile has a × top right; Clear all under the deck empties every slot and
  goes gray. Drag a plate: only its framed name follows the pointer, not
  the slot and its platform chip.

- [ ] **The calibration module's slot.**
  Slot 9 (Calibration module) takes no click and no drop, like the trash.
  The platform chips ("Picking platform") read at the size of the labware
  facts.

- [ ] **The Platforms sheet.**
  Change platforms in Robot setup: the sheet's deck has the same tiles and
  proportions as the Deck step, a dashed border and chip on each platform,
  the trash striped. Choosing, Save changes and Save / Discard work as
  before.

## Park, Home and the shell's look (#37, #38)

- [ ] **Park does not home.**
  With a calibrated tip on, the tip low over the dish: Park raises the
  tip, then the head goes to the back right in one move, no question
  asked. The Tip step stays done. Park again: one move, no retract.

- [ ] **Home is in Settings › Advanced.**
  Home, beside Start new robot session, asks "Home the robot?" with the
  tip calibration to do again; Cancel moves nothing. Yes homes; the Tip
  step is no longer done. Gray, with the holder named, during a run.

- [ ] **Home looks settled.**
  The three task tiles sit on the page, no dark card behind them. The
  cards and tiles are centered both ways at any window size. Titles on
  Home are one size.

- [ ] **The breadcrumb.**
  The gap between the house and "Home" is the same on Home, in a task and
  in Robot setup.

- [ ] **Robot setup's Done; the Log.**
  Done at Robot setup's bottom right goes to Home. Settings › Log has Save
  report for help… and no Clear.

## Liquid in tip (Picking)

- [ ] **Where it is.**
  Auto pick has no Tip liquid step. On Picking, under Cuboid sizes and
  Plate, the Liquid in tip card is folded to its title and "Tip empty".
  Click the title: it opens.

- [ ] **Dish center.**
  "20 µl · 1.5 mm above the dish bottom · 50 µl/s", Aspirate from on
  Dish center, Aspirate 20 µl: the tip goes over the middle of the dish,
  1.5 mm over the dish bottom, draws 20 µl and goes up. "The tip holds
  20 µl.", the title row "20 µl in the tip". Press again: "The tip holds
  40 µl.". Dispense 40 µl: the tip goes there again, gives all of it
  back and goes up; "Tip empty", Dispense gray.

- [ ] **A well.**
  A well: the mini deck lists the plates on the deck; choose one, click a
  well on its map ("Well B3, slot 5."): Aspirate 20 µl goes into that
  well's bottom (shifted by its measured well center, if any), draws and
  goes up. The line drops the height. Back to Dish center and to the
  step later: Dish center again.

- [ ] **Change.**
  Change opens Volume and Height with Cancel and Save. Type 33, leave
  the box: nothing is saved yet. Cancel: the line is as before. Change,
  30, Enter (or Save): in the line, on the button and in More settings
  under Main. Restart: the values are kept.

- [ ] **During a run.**
  While a run picks, Aspirate and Dispense are gray, "Pause to use the
  tip.". Pause: they work, Pause / Resume is gray until the tip is up
  again; Resume: the run goes on.

- [ ] **The run keeps it.**
  Fill 20 µl, then run Picking: each deposit and return gives back only
  the cuboids' volume; at the end the tip still holds about 20 µl.

## Well center step in Manual pick (#63)

- [ ] **The step.**
  Manual pick's steps: Deck, Tip, Source, Well center, Dish bottom, Run.
  With the source dish chosen and Dish bottom done the task opens on Run,
  not on Well center. Well center
  lists the plates on the deck, with no Skip this plate; "A plate not
  measured uses its drawing." Next is enabled with nothing measured.

- [ ] **Done.**
  Measure every plate: the step's chip is checked. A plate skipped in
  Liquid in this robot session does not count in Manual pick: its chip
  stays open until that plate is measured.

- [ ] **Liquid unchanged.**
  Liquid › Well center still has Skip this plate, and Next stays gray
  until every plate is measured or skipped.

## Manual pick's Source step (#88)

- [ ] **Nothing is chosen.**
  A new start, a 24 well plate on the deck: Manual pick opens on Source
  (after Deck and Tip), a tile for the source dish and one for the plate,
  each with its slot, none outlined, Next gray. Run says "Choose the
  source first." with Go to Source.

- [ ] **From a plate.**
  Click the plate's tile: Dish bottom leaves the step bar. Well center
  opens on that plate, "You pick from slot 5: measure it, or Use it.", Next
  gray beside "Measure the plate in slot 5." A second plate on the deck
  stays optional. Run says "Measure <plate> in slot 5 first." until Save or
  Use it; no step asks for the position over the dish.

- [ ] **From the source dish.**
  Click Source dish: Dish bottom is back, with its Over the dish need. Well
  center is optional, and Run says "Find the dish bottom first."

- [ ] **The choice goes.**
  Plate chosen, drag it to another slot on Deck, or take it off: Source
  loses its ✓, no tile is outlined and Dish bottom is back. Likewise after
  Home and connect (a new robot session).

- [ ] **Auto pick is not touched.**
  Confirming the plate in Manual pick leaves Auto pick's Dish bottom not
  done.

- [ ] **Finish by hand.**
  A run left short, Finish by hand: Work is not dimmed, Source ✓ on the
  source dish.

## A measurement and its module; Use it (#87, #92)

- [ ] **The module changes.**
  Measure a plate in a slot, then in Robot setup's platforms put another
  module height in that slot (or take the module away). In Manual pick,
  Auto pick (the plan's plate) and Liquid its row reads "Not measured
  yet", its depth is the drawing's, and a deposit, a Manual well and a
  Liquid step go to the drawing's center. Put the old height back: the
  row reads "Measured" again.

- [ ] **Last time and Use it.**
  Manual pick › Well center, a plate measured on an earlier day, in a new
  robot session: "Last time: Z 66.40 mm" and when, with Use it. Take the
  tip to the plate's real bottom by hand: Z there agrees with the line
  (the Z is built from the drawing, the module and the measurement).
  Use it: it grays. Save on a new measurement grays it too. A new robot
  session: Use it is back. A plate never measured has no such card, and
  neither Liquid nor Auto pick shows one.

## Well center step in Auto pick (#64)

- [ ] **The step.**
  Auto pick's steps: Deck, Tip, Dish bottom, Plan, Well center, Picking.
  With the plan in use the task opens on Picking, not on Well center.

- [ ] **No plan.**
  Edit plan on Plan, then Well center: dimmed under "Make a plan first."
  with Go to Plan, which goes there. Next is enabled.

- [ ] **The plan's plate only.**
  Two plates on the deck, a plan into slot 5: Well center lists only
  slot 5's, with no Skip this plate. Measure it: the chip is checked.
  A plan into the other plate: the list follows it.

- [ ] **The deposit.**
  With slot 5's well center measured, run Picking into a small well: the
  tip goes down at the measured center.

## The last step of every task is Run (#65)

- [ ] **The step bar.**
  Auto pick: Deck, Tip, Dish bottom, Plan, Well center, Run. Manual pick:
  Deck, Tip, Source, Well center, Dish bottom, Run (no Dish bottom with
  a plate chosen). Liquid: Deck, Tip, Well center, Plan, Run. No step is called Picking or Work.

- [ ] **The run keeps its words.**
  Auto's Run is the Picking page: Start picking, and the footer and the
  pill say PICKING / "Picking" while it runs, as before.

## Reordering plan steps (#71)

On Liquid's Plan step. The tests use Qt's own drag loop; Windows' native one
is only seen here.

- [ ] **A drag.**
  Press ⠿ on the third card and move: the card follows the pointer, a dashed
  place stays where it was, a line shows between the cards. Release above the
  first: it is first, the numbers read 1, 2, 3, the colors stayed with their
  steps. Ctrl+Z puts it back.

- [ ] **Esc cancels the drag and stops nothing.**
  During a run the list is locked, so try this with no run. Drag a card, press
  Esc: it stays where it was, no "stopping" is said anywhere. Then press Esc
  with no drag: Stop answers as it does on every page.

- [ ] **A long plan.**
  With more steps than fit, hold a card near the bottom edge: the list scrolls.
  Release outside the list: nothing moved.

## Liquid Plan and Run (#67)

These replace the checks of the page of groups (#22, #23). Set up: a 6-well
plate and a 96-well plate on the deck, both measured or skipped on Well center,
a waste to use as the trash, a tip on. A failure in the wells themselves points
at the compile step (`core.liquid_plan`); one on screen only, at the pages.

- [ ] **A plan by mouse.**
  With the mouse only, on the Plan step: 6-well A1 → 96 A1–A12, 50 µl, Pause
  after the first well; 6-well A2 → 96 B1–B12, Mix after putting it in on; 96
  D1–D12 → Trash. Each card reads as a sentence ("Move 50 µl", Slot · A1 →
  Slot · A1–A12 (12)). Drag step 3 to the top: the numbers read 1, 2, 3, each
  step keeps its color, here and on the plate. Drag it back.

- [ ] **The trash is never a dead end.**
  Behind the Trash row, Remove the trash: the row says "Not set yet. Needed
  before running.", Next is gray and the footer says "Set the trash to run this
  plan." Set it from the Trash row, a well of the waste, Done: Next is enabled.
  With a new plan afterwards the trash is already there (Use for future plans).

- [ ] **One step at a time.**
  Run the plan, One step at a time, Start step 1 and confirm. The tip is blown
  out over the trash before the first well. After the first well the strip says
  "First well done. Check it, then continue." and the window flashes in the
  taskbar; Continue. After the step: "Step 1 is done. 12 of 12 wells." with
  Finish here, Repeat step 1 and Go to step 2. Repeat does the step again from
  its first well; Go to step 2 goes on. The counter ("7 / 12"), the bar and the
  wells drawn keep up with the robot, the well ringed being the one it is at,
  and the pill says "Liquid · step 2 of 3". After the last step: "All 3 steps
  done." with Run again, Back to plan and Home, and Run gets its ✓ in the step
  bar.

- [ ] **Plates in their real proportions.**
  The 6-well and the 96-well plate are boxes of the same size, on Plan and side
  by side on Run; only the wells differ.

- [ ] **A wash, drawn beside the cuboid.**
  On a plate measured on Well center, not skipped. A tip of 200 µl and the trash
  a well of the waste, Top, +5 mm. Step 1: Move liquid from the wells with
  cuboids to the Trash, 100 µl, Take speed 10, Put speed 200, Take it at Bottom
  +2.5 mm, Aside X −2 mm, Lift slowly on; the drawing says "Bottom measured
  in Well center". Step 2: Move
  liquid from the reservoir to the same wells, 100 µl, Pause after the first
  well on. Start: each well is drawn beside the cuboid, 2 mm to the side of the
  measured center and 2.5 mm over the bottom, and the tip rises 2 mm slowly in
  one move, then straight up out of the well. The waste is visited after every second well and after the last, with
  a blow out each time, no retract between wells and no second trip at the end.
  Step 2 pauses after its first well: the cuboid is still in it. A draw over the
  cuboid means the aside did not reach the engine; a trip to the waste after
  every well, that auto empty did not.

- [ ] **A plate not measured.**
  With a plate neither measured nor skipped, its tile says "Not measured"
  (yellow) and choosing it shows the warning. Measure now opens the Well center
  step; Use anyway hides it for that plate, and Amount says "Bottom from the
  plate drawing".

- [ ] **How on the left.**
  A Mix's How: Times, Volume and Speed in one row over the height card, in a
  column in the middle under the title, and only rows and buttons in the open
  card. A Blow out in a well: Speed over the height card; at the trash or a
  point: Speed and "A point and the trash are used at their own height." A Move
  the tip in a well: only the height card; at a point it has no How. Type an
  offset: the drawing, the sentence and the note follow, and one Ctrl+Z takes
  the digits back. During a run the fields are gray.

- [ ] **Wait and Wait for me on the left.**
  A Wait: the large time over Minutes and Seconds, in a column in the middle
  under the title, the whole line "Pause and Stop still work." under them, and
  only rows and buttons in the open card. Type minutes: the time, the row and
  the card's title follow, and one Ctrl+Z takes the digits back. A Wait for me:
  the message field over the preview, which follows it as it is typed. During a
  run the fields are gray.

- [ ] **Blow out: By hand, and A well first.**
  Add a Blow out: it opens on A well | A point | By hand | Trash with the plate
  shown and nothing chosen, "Choose the place." under it, Try once and the
  footer's Next gray. At 1280×800 the four buttons touch no title. By hand:
  bring the tip to a spot with Controls, Use this spot; How shows Speed and "A
  point and the trash are used at their own height.", and a run blows out
  there. A template saved with a Blow out into the trash opens on Trash.

- [ ] **The place choice in the middle.**
  In Take from, Put into, Where and the trash chooser, A well | A point and its
  variations are on the title's row, in the middle of the left area, and the
  plate starts as high as under a Mix's Wells, which has no choice. At 1280×800
  the choice clears "Where is the liquid taken from?", still on that row. Each
  choice changes the place.

- [ ] **No field takes the focus by itself.**
  Click a field of Amount, then go through the sections by Next and by their
  rows: no field is ringed, and a digit or an arrow pressed changes nothing in
  the plan, in any section. Click Volume and type several digits: the field
  keeps the focus while the card's row and the sentence follow.

- [ ] **A point and By hand.**
  Take from A point: the live upper camera, the points under it. A right click
  adds a memo point; used in a step it reads as its name and is still there
  after the program restarts, opened from a template. The star asks a name and
  keeps it as a saved point; Robot setup's positions are never listed. By hand:
  bring the tip to a spot with Controls, Use this spot ("Spot saved for this
  step."); the run goes back there without waiting for anyone.

- [ ] **Try once.**
  An open step, Try once: "Step 1: trying on the first well.", the robot does
  its first well only, no blow-out first, and the tip stays where the step put
  it; the toast says what the tip holds. With no trash set, a step going into
  the trash is refused with "Set the trash to try this step." and nothing moves.

- [ ] **Pause, Stop run, Continue.**
  During a step, Pause: the robot stops after the current command, PAUSED, the
  button reads Continue in yellow, and the strip says which well it goes on
  from. Stop run (or Esc) in step 2: the tip is raised, READY and "Stopped. n of
  N wells done.", with Continue step 2 from well 8 (the next well), Start the
  step over and Start from step 1. Continue carries on from that well with no
  blow-out; Start from step 1 blows out first. During a run the Plan step says
  the plan cannot change, and its handles are hidden.

- [ ] **Templates.**
  Save as template "Wash", New plan, then A template: "Wash" is listed with its
  steps and wells, and a click brings them back; a change shows Save changes to
  "Wash". The file is in `Outputs/templates/liquid-plans/`. A program saved
  there before plans is listed gray, "Made with an older version", and does not
  open.

## Messages: the toast (#94)

Everything a page says now goes to one place. A failure here is on screen
only: `widgets.toast`, or a page still telling `JogPanel.tell` a refusal.

- [ ] **Nothing under X Y Z.**
  Manual pick's Run, Picking, the Tip step's picture, a Robot setup sheet:
  jog into a limit, click off the deck, press Save with the robot busy.
  Under the coordinates there is never a message, only the arrows of the
  axes (and on Manual pick's Run the height line, #95, which says no more
  than a slot's heights). Well center and Dish bottom have no line under
  their tip height
  either. "N cuboids found" and MARKER FOUND stand on the picture's top
  edge, left of its buttons, and stay.

- [ ] **Bottom left, on every screen, moving nothing.**
  Say something on Home (Park), a task's step, Robot setup, a sheet over
  the page and during a run: the toast is at the window's bottom left, over
  the Footer, over the run's status while it shows. No card moves. A long
  sentence wraps upward over the page and stays clear of Back and Next.
  Back and Next take a click through it.

- [ ] **Green 3 s, red 6 s.**
  Save something: green for 3 s. Click off the deck: red for 6 s. Click
  off the deck, then at once Save: the green replaces the red, for its own
  3 s. The StatusPill does not change for either.

- [ ] **A refusal is a sentence.**
  Platform in slot 8, a memo point there with Z 50, Tip to: "Not reachable:
  Z 50 is below the platform in slot 8.", nothing moves, and the point's Z
  is red in the card and on the picture, with the same reason as its
  tooltip. Dispense with nothing in the tip: "Nothing is in the tip."

## Manual pick: Tip to crosshair, the step on the picture (#99)

A failure in the first check is the tip landing off the spot: the pixel
map or the measured tip offset, the same as By hand's aim. The others are
on screen only.

- [ ] **Tip to crosshair.**
  A calibrated tip on, the crosshair on a mark on the deck, Tip to
  crosshair: the tip stops over that mark at the travel height, and stays
  there; the camera does not come back. Without a tip it is gray ("Put a
  tip on first."), without a pixel map too ("Calibrate the camera first."),
  and while a job holds the robot. With the tip low in a well it rises
  first. A spot off the deck: "Not reachable: ...", nothing moves.

- [ ] **One plate and one style on every picture (#109).**
  Manual pick's Run, Picking, the Tip step's picture, Camera calibration, a
  position sheet, Liquid's Plan: X Y Z one per line and "STEP · 1 mm" in the
  plate at the top left, Crosshair and Points chips and the picture button at
  the top right, the mouse row at the bottom left, no checkbox and no axes.
  Bigger step on the pad, then the step keys: it follows at once, yellow at
  5, red at 10. Fold the pad: it stays, and the keys still change it. Liquid's
  Plan with Controls closed: the step stays. A click on the plate moves
  nothing. Well center: the same step in a chip under the tip's X Y Z, no
  picture. Dish bottom: unchanged.

- [ ] **Part first.**
  Open Manual pick's Run: Dispense reads Part | All, Part chosen, its
  field the picking volume. Aspirate, then D: that volume is given back,
  not all of the tip.

- [ ] **Source's heading.**
  Manual pick's Source step reads "Where do you pick from?".

## Manual pick: the plate's heights on the picture (#95)

A wrong number here is the one to look for: read the line, then touch. The
top is the wells' rim, on the platform if there is one; a bottom that does
not match the plate on the deck is its labware definition or the slot's
platform height, or a Well center measured badly.

- [ ] **The line follows the camera.**
  Manual pick's Run, a plate on a platform in slot 8, another in slot 5,
  slot 2 empty. Look at slot 8: under the plate "Slot 8 · top 85.10 · bottom
  67.77 · drawing", the numbers the plate's. Look at slot 5: its own. Jog
  across a slot's edge: the slot changes with the crosshair, not with the
  tip. It stands while the robot moves. Look at slot 2: no line, and
  nothing else moves.

- [ ] **Measured or drawing.**
  Measure the plate in slot 8 on Well center, back to Run: the line ends
  "measured", and the Z of the tip at the rim is its top. Start a new robot
  session, the plate loaded again: "drawing", the drawing's numbers, until
  Use it on Well center; Aspirate In a well goes by the same.

- [ ] **The source dish.**
  Look at the source dish's slot with no plate there, Dish bottom not done:
  no line. Save the dish bottom (or Use it), back to Run: "Slot 9 · bottom
  66.42 · measured", no top.

- [ ] **Only here.**
  Picking, the Tip step's picture, a Robot setup sheet, Liquid's Plan: no
  line under X Y Z.

## Manual pick: a Z checked against the plate's bottom (#101)

Keep the tip high for these: a target past the limit must not move at all.
The limits are 1 mm under a measured bottom and 2 mm under a drawing's, and
the bottom is the one the height line gives. A Z refused that should go, or
the other way round, is first of all a height line to read.

- [ ] **A Z past the limit moves nothing.**
  Manual pick's Run, a plate in slot 5 not measured, a tip on, the tip up.
  The height line over slot 5 says "bottom 3.55 · drawing" (your plate's
  number). A memo point in the plate, Z 2.5 under that bottom: Tip to does
  not move, and bottom left in red "Not reachable: Z 1.05 is below the
  drawing's bottom in slot 5.", for 6 s. Aspirate At a point there and
  Dispense At a point there (something in the tip): the same, the gauge as
  it was. By hand at the same Z, the crosshair over the plate: the same,
  and with Aspirate as soon as I click the camera stays where it was.

- [ ] **A light press stays possible.**
  Empty dry well only. Measure the plate on Well center, so the line says
  "measured". A point 0.5 mm over the bottom: Tip to goes. Then points
  nearer, down to 0.5 mm under the bottom: each goes. 1.5 mm under: refused,
  "measured bottom". A new robot session, the plate loaded again and not
  confirmed: the line says "drawing", and the limit is the drawing's.

- [ ] **The source dish.**
  Dish bottom saved, Z 66.42: a point in the dish's slot at 66 goes, at
  65 it is refused. Dish bottom not done in this robot session and no
  plate there: nothing is checked.

- [ ] **No bottom, no check.**
  An empty slot: a point at a low Z over it is not refused for a bottom (a
  platform's top still is). A point at the travel height over a plate goes.

- [ ] **Jog and Dish bottom are not stopped.**
  Over the plate, the pad's Down past the limit: it steps as it always did.
  Dish bottom's Coarse Down goes on under the bottom saved last.

## Manual pick: equal columns (#100)

On a full HD window, the page in two columns.

- [ ] **The line between the columns stands still.**
  Manual pick's Run. Aspirate From In a well, At a point, By hand; fold and
  open Memo and Saved; open Change until a scroll bar shows; Dispense In a
  well until the button reads "Dispense into A7". The two columns stay
  the same width and no card goes to the other column. A plate's long name
  is cut in its Select.

- [ ] **Nothing cut on a full HD window.**
  Every button's text is whole, Point at crosshair and Tip to crosshair
  side by side, and a chosen row's four actions. Narrow the window: the columns stay equal,
  then the cards go to one column.

## Manual pick: how the tip leaves (#102)

- [ ] **Retract automatically, on each source.**
  A tip on, the camera over a plate. Aspirate By hand: the tip goes down,
  aspirates, rises and the camera comes back. In a well and At a point: it
  rises where it is, and stays there, the camera not coming back. Dispense
  In a well, At a point: the same, it rises where it is.
- [ ] **Don't retract.**
  Change in Aspirate, Retract: Don't retract. The line says "don't retract".
  By hand: the tip stays down in the liquid, the camera does not come back.
  Dispense's choice is its own and is still Retract automatically.
- [ ] **Retract after delay.**
  Change, Retract after delay, Delay 5 s (gray until then). The line says
  "retract after 5 s". Aspirate: the tip stays down for 5 s, Aspirate and
  Dispense gray and the jog pad grayed, then it rises.
- [ ] **Esc in the delay.**
  Delay 60 s, Aspirate, and Esc while the tip is down: the toast says
  "Stopped: the tip stays down.", the tip has not moved and the buttons
  come back at once.

## Manual pick: Retract slowly (#103)

- [ ] **The OT-2 moves evenly at 0.5 mm/s.**
  Change in Aspirate, Retract slowly on; the line says "retract slowly". The
  tip in water, Aspirate: it rises 2 mm in about 4 s, evenly, with no jerk and
  no pause, then goes up as usual. If it does not move evenly, Retract slowly
  has to be made of small steps instead (#93), in Liquid too.
- [ ] **Gray with Don't retract.**
  Retract: Don't retract. Retract slowly grays and the line says "don't
  retract". Retract after delay: the delay comes first, then the slow rise.
- [ ] **Close under the travel height.**
  A point 1 mm under the travel height, Aspirate there with Retract slowly
  on: it only goes up, no slow move.

## Manual pick: In place, and the A and D keys (#111)

- [ ] **In place neither moves nor lifts.**
  A tip on, jogged down into liquid. Aspirate in place: nothing moves, the tip
  stays down whatever the cards say, "Aspirated 10 µl here." Dispense in place
  the same, "Dispensed 10 µl here.", and gray with the tip empty. Above what
  the tip holds: "Only 5 µl in the tip." in red, nothing moved.
- [ ] **The keys follow Settings.**
  Opening the page, the arrows and A work at once, no click needed. Settings,
  Keys: bind aspirate to Q and dispense to W, Save. The buttons carry Q and W,
  Q and W do it, A and D no longer. In the Amount field the keys type or do
  nothing, the robot stays.


- [ ] **Manual pick's Run: the frame (#113).**
  At 1920×1080: the picture, then a work column and a Points column of 400 px,
  Points the full height. Look at and Detect cuboids in a row over the chips,
  Past picks among them. Footer: Back and Finish, nothing on its left; Finish
  goes Home. Narrow the window under 1600 px: one column of 420 px, Points a
  folded card after Dispense, the picture still 4:3 with black bars.

- [ ] **Nothing shifts.**
  Manual pick's Run: click through From and Where, with Change open and closed:
  the picture, the width of every card and the other column stay, the card
  clicked keeps its top and is only as tall as what it shows, no flicker. Part
  and All move nothing. Opening the page: nothing jumps as the camera opens. Liquid
  in tip's From: only what is under it moves. Tip step: Pick up and Drop in trash move the Calibrate card, with the rack folding, and nothing else. A
  liquid run: Pause and Continue, the plates keep their size. Home: the same
  with the robot switched off and on. Plan: A template and New plan, what is
  under the tiles stays. A refusal on Deck or Plan is a red toast at the bottom
  left, no line on the page.

## Manual pick: what the Aspirate and Dispense cards hold (#115)

The checks above that say By hand, Change, Retract automatically, Don't retract,
Retract after delay, Retract slowly and Analyze now read By clicking, the fields
in the card, Right away, Stay, After a wait, Lift slowly and Detect cuboids; what
the robot does is as they say.

- [ ] **The fit.**
  1920×1080, 100 % scaling, a plate on the deck. No scroll bar with Aspirate open
  on By clicking and its lifting row open on After a wait, nor with Dispense open
  on In a well. Opening a lifting row under a plate's map may bring one.
- [ ] **By clicking.**
  From reads By clicking, At a point, In a well. A click moves the camera and
  never lowers the tip; Aspirate takes from under the crosshair; Snap and
  Aspirate as soon as I click work as before.
- [ ] **The size window.**
  "250–500 µm" on Snap's row, the profile's window; after dragging it on Picking,
  the new one. A click opens the histogram under it, with its sentence after
  Detect cuboids, "nothing measured yet" before; a click outside closes it. The
  picture's Sizes chip still works.
- [ ] **Heights.**
  By clicking: Z | Above the bottom, From tip only with Z, taking the tip's Z. In
  a well: Above the bottom, mm, and the summary or the field's tooltip says
  measured or drawing's. At a point: no height. Dispense: none, and a well is
  still reached at the deposit's height. A Z under the bottom is still refused.
- [ ] **Chips.**
  At a point lists every point as a chip, ★ saved, ☆ not. A click outlines it,
  again lets it go. More than three rows scroll and the card stands still; a
  point below a platform is red.
- [ ] **Plates.**
  In a well: the mini deck offers the plates only; a long name is cut. Aspirate
  starts on the Source step's plate. A click on a well chooses it, its name
  beside the mini deck. On Dispense, after a plan: the plan's plate, its wells in
  one color, no legend, any other well choosable, and no sign of what Picking
  filled. Finish by hand opens Dispense on In a well on the first well left short.
- [ ] **Lifting.**
  Folded, "After aspirating: lift right away", the same at the end of the summary.
  Open: Right away, After a wait, Stay. Wait, s only with After a wait; Lift slowly
  gone with Stay. Each card its own, kept across a change of step.
- [ ] **Fields.**
  Numbers right-aligned in the mono face, no arrows, the unit in the label; ↑ and
  ↓ step them while they have the keyboard, the wheel too, and the wheel scrolled
  past one changes nothing. The robot's keys work as the page opens.
- [ ] **Toasts.**
  "Aspirated 10 µl.", "Dispensed 10 µl into A3.", "Camera moved to slot 4.", "23
  cuboids found.". No "Tip moved here."

## Manual pick (#107)

MANUAL.md §8, the whole screen. Dish bottom and Well center have no picture, so
they are not in the chrome check (§10.19), and nothing counts filled wells on this
screen (§10.10).

- [ ] **The whole screen fits.**
  1920×1080, 100 % scaling, a plate on the deck: no scrolling with Aspirate open
  and its lifting row open, nor with Dispense open on In a well. A scroll bar here
  means a card grew past the column, and the action's fields leave the screen.
- [ ] **One look.**
  Side by side with `docs/redesign/prototype/ManualPick.dc.html`: every text is
  Inter or JetBrains Mono. The buttons have three heights, 44, 40 (Aspirate and
  Dispense in their headers) and 36; a choice's segments, a Switch and a card's
  fold header are not counted. A different font means Inter or JetBrains Mono did
  not load on this computer, or a widget has a font of its own.
- [ ] **A manual pick by clicking.**
  Aspirate as soon as I click on: a click on a cuboid aspirates it and the
  TipGauge rises, the Dispense card open. Dispense In a well: the tip goes to the
  well, the gauge falls, "Dispensed 10 µl into A3.", the Aspirate card open once
  the tip is empty.
- [ ] **A manual pick with A and D only.**
  The tip jogged down to a cuboid, A aspirates; jogged over a well and down, D
  dispenses. Neither moves the tip or switches a card, and Retract in the top bar
  raises it.
- [ ] **The camera chrome.**
  Picking beside Manual pick's Run: X Y Z and the step in one plate top left, the
  chips and the picture button top right, the mouse row bottom left, Controls
  bottom right, the same corners and chip style. A page's own buttons and the
  histogram stay where they were.

## Tip (#119, #120, #122, #124)

- [ ] **The picture and the column.**
  At 1920×1080 the picture is 4:3, undistorted (the disc is round), and the
  440 px column is centered in the space beside it. Camera calibration and
  the position sheets the same; Manual pick's two columns keep their width,
  the picture taking the rest; Picking's panel as before. A switch of cameras
  moves nothing.

- [ ] **The rack.**
  Round tips in the rack's real proportions, no teal: used ones dashed, the
  next one blue. A click on another tip makes it next, and Pick up takes it.
  A one-line legend under it.

- [ ] **Disabled buttons.**
  On every screen a gray button reads as its normal self, faded: the Footer's
  Next, Drop in trash during a run, a red one.
