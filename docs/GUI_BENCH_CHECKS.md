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
  With both calibrations saved and a tip on, open Check, Detect: every
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

- [ ] **The check page can get back to the crosshairs.**
  Detect, click a crosshair with "Click to move" on, look at the tip over
  it. The crosshairs are now out of the camera's view, as they must be.
  Double click the stored pose in Positions: the gantry returns and Detect
  finds them again.

- [ ] **The panels fold where they should.**
  The pipette tab's step 1 shows Move and Positions folded; the camera
  tab's step 1 shows Move open and Positions folded; the check tab the
  other way round. Each opens on its triangle and stays open while the page
  does.

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
  Open Picking, Manual control, either calibration tab or Check with no
  camera open: the upper camera opens itself and the picture is there. Unplug
  it and try again: the page says why once and does not try again until the
  Profile page's button is pressed - watch for a page that becomes slow to
  open, which would mean it is retrying.

- [ ] **"Go to the dish" goes to the dish.**
  Jog until the dish fills the frame, Teach here, drive somewhere else, then
  Go to the dish: the same view comes back. `observe` appears in the jog
  panel's positions like any other pose.

- [ ] **The analysis counts what a run would pick.**
  With the real weights loaded, Analyse the dish: the overlay is the picking
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
  Analyse the dish, then nudge the dish by hand: the contours stay where
  they were and the cuboids move out from under them. Analyse again and they
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
  On Manual control, the calibration tabs, Check and Picking the jog panel
  has no Position card; the coordinates and the step are in a box under the
  resolution, and a refused or clamped step adds a second line there. With
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
