# micropick

Lab robot (Opentrons OT-2 gantry, pipette, upper and lower cameras) that
picks cuboid microtissues from a dish into a plate. Design notes are in
`docs/DESIGN.md`; bench checks for the GUI in `docs/GUI_BENCH_CHECKS.md`.

## Running things

- Interpreter: conda env `lab` (`conda run -n lab python ...`); the bare
  `python` on PATH is the Windows Store stub.
- Tests: `conda run -n lab python -m pytest -q tests` (whole suite, ~3-4 min:
  most of it is a `MainWindow` built per GUI test, ~1.3 s each).
- GUI: `conda run -n lab python -m micropick.gui` (`--mock` for no hardware).
- `lab` has an unrelated package named `tests` installed, so
  `python -m tests.…` resolves to it, not to this repository's `tests/`.
- Windows build: `conda run -n lab python packaging/build.py` (PyInstaller,
  one folder, into `dist/`, zipped, then an Inno Setup installer
  `dist/micropick-<version>-win64-setup.exe` from `packaging/micropick.iss`;
  needs Inno Setup 6, else `--no-installer`). Refuses a dirty tree unless
  `--allow-dirty`. The installer's AppId is fixed: it is what makes a new
  installer update the installed program. A built app keeps its data in `Documents\micropick`
  (or `MICROPICK_ROOT`), not beside the exe (`paths.root`).

## Versions

One source: `src/micropick/_version.py` (pyproject reads it). A release:
bump it, add a CHANGELOG.md entry, commit, tag `v<version>`, build from
the tagged commit. The window title, the log and a report show the version
and, in a build, its commit.

## Decisions to keep

### Picking: analysis is live, a run holds its decision frame

- On the Picking page, *Analyze the dish* draws its contours over the
  **live** feed. If the dish moves afterwards, the contours visibly stop
  matching, which is the cue to analyse again. There is no "back to live".
- During a **run**, the view follows `PickView` from `workflows/picking`:
  live while the session waits for the operator, otherwise the **frame the
  decision was made from is held**, unchanged, with the overlays measured on
  it. Do not make the run view always-live — the overlays belong to that one
  frame, and drawing them over a newer one shows contours where nothing was
  measured.
- Start picking asks for confirmation (dish and plate in place, lids off,
  settings right) and then gives the session its go-ahead at once.
  The footer's one Pause / Resume button unpauses, or leaves
  `needs_operator`; nothing else gives a go-ahead.

### Camera view chrome

Over the picture is only what is used there (REDESIGN §3): the robot's X,
Y and Z (fixed font, and what a step ran into), the page's own lines
(`set_status`: found counts, a run's state), the page's buttons and one
quiet mouse row bottom-left (`CameraView.set_mouse_help`, "gesture   what
it does" per item). Never the camera name, slot, resolution, crop, zoom,
fps or "TIP EMPTY". All drawn by `CameraView` in widget pixels as
semi-transparent boxes, never into the frame and never as a separate panel.
A press on a widget floating over the picture (the jog pad's card, the
TipGauge, a page's button) is that widget's, even beside its buttons:
never a click on the picture. A page whose click moves the robot names
it with `CameraView.set_click_to_move`; without a pixel map that line is
"Use the arrows to move the camera." and no click is sent.

A camera view and its side panel sit in a `widgets.feed_row.FeedRow`: the
picture is 4:3 (the upper camera's) whatever it shows, another shape
letterboxed in it with black bars, so a switch of cameras resizes
nothing; the panel gets the rest, within 1-2x its designed width. Pages carry no title;
the TopBar's breadcrumb is the title.

### The shell: Home, tasks as steps (REDESIGN §4)

There are no tabs, no status bar, no floating next step and no Profile
page (removed in redesign phase 2; do not bring them back). The window is
`widgets.top_bar.TopBar` (breadcrumb `Home › <task>`, Light, Park - what
Home robot position was - STOP and the StatusPill while a run has the
robot, Settings) over
one stack: Home (`pages.home`: the robot, the Robot setup, three task
tiles) and the existing pages, reused as the content of a **task**'s
**steps** (`gui.tasks`; `shell.STEP_PAGES` maps step to page). While a
step is shown, `StepBar` is over it and `Footer` (Back, Next) under it.
A step's done mark and first unmet need come from `tasks.step_state` each
time, never from a visit; a step whose need is not met is dimmed
(`overlays.DimCover`) and disabled - so its keys do nothing - under one
banner naming that need with a button there. A task opens on its first
step not done. Next is enabled when the step is done or optional, and
only moves on: nothing is confirmed by it. Deck is done while the robot
is connected, something is on the deck and there is no deck problem; a
move by drag goes through `Session.move_labware`, which loads again so a
platform's height attaches. Auto pick is Deck → Tip → Dish bottom → Plan
→ Picking; Plan needs a plate on the deck (a "plate" need, "Put a plate
on the deck first." with Go to Deck), not the Deck step. The Deck step
is `pages.labware`: the tiles of `widgets.deck_tiles` in the deck's
proportions, a × per filled tile and Clear all (no Put something else),
a drag carrying only the labware's framed name; the calibration module's
slot takes no labware, like the trash (`Session.refuses_labware`); the
picker's tabs Frequent · All · Plates · Tips, a search inside the tab
shown; kind and facts from `gui.labware_kinds`. The Platforms sheet
shows the same tiles (`DeckTiles(show_labware=False)`). Robot setup (`pages.robot_setup`, from Home's
Setup card; the one page with a title, REDESIGN §5) is one page of rows - the cameras, camera calibration,
platforms, the three positions in the order they can be set - calibration,
shake, over the dish (each `≈ slot n · X Y Z`, never its stored
name) - whose set/not set is `tasks.SETUP`, the same that a step's needs
and Home's "N steps left" read. A row, or a Robot setup item needed by a
step, opens its page as `Home › Robot setup › <item>` or `Home › <task> ›
<item>` (`shell.SETUP_PAGES`), each in its own sheet. The
cameras have theirs (`pages.camera_sheet.CameraSheet`, over the screen,
the breadcrumb gaining the item): the live picture of the device chosen
in the Select, which follows the choice before anything is saved
(`Session.preview_camera`: the profile's camera on that device, opened
as the pages open it and left open; any other device opened on its own,
so none is opened twice). Done saves (`Session.choose_camera`: the other
role's device swapped in, a camera added to a profile without one), gray
while the robot is busy; Cancel and × save nothing. The camera
calibration has its sheet too (`pages.calibration_camera.
CameraCalibrationSheet`), the steps confirmed one after another: place
the marker (It is in place); its slot on the mini deck slot chooser
(`widgets.slot_chooser.SlotChooser`: all 12 slots, nothing preselected,
grayed while the robot is busy; a click moves the camera there at once,
through `JogPanel.run_job`: the slot's center less the measured tip
offset, else Settings' start dx, dy, and marks the slot once moved - the
one way a Robot setup sheet chooses a slot); center it with the arrows - a click
on the picture centers only through a pixel map there is, else it moves
nothing - MARKER FOUND / NO MARKER IN VIEW on the picture, It is
centered once within two of its sides of the middle; Start calibration,
the sweep a run (its lease `run=True`: STOP, the pill, which reopens the
sheet), "Don't touch the robot."; Stop goes back to centering. Good and
Acceptable are saved at once ("Camera calibrated and saved.", Done
closes the sheet), Redo waits ("Not accurate enough: the marker was off
center.", Redo calibration secondary, Save anyway quiet). × closes
it and a sweep carries on. The platforms have theirs
(`pages.platforms_sheet.PlatformsSheet`): the 3×4 deck, a slot clicked,
"What stands in this slot?" (the profile's module types or Nothing), its
height, New type and Delete type - all a draft until Save changes
(`Session.set_module_types`, `Session.set_deck_modules`), gray while
nothing differs from what is saved, then "Saved: slot 9 · Calibration
module"; Done and × only close (no Cancel), and with unsaved changes ask
Save / Discard first - so does going elsewhere (a crumb, Home, a task),
asked over what is shown next (`PlatformsSheet.discard`). The three positions share
one (`pages.position_sheet.PositionSheet`, `open_for(item)`): the upper
camera with the pad, a click moves the camera through the pixel map
(`gui.camera_moves`, also the camera calibration's), and the position's
numbered step cards (`position_sheet.STEPS`, built by kind through
`PositionSheet.builders`), each button gray until the step before is
confirmed: the crosshair disc / the source dish placed (It is there),
which slot (the mini deck `widgets.slot_chooser`, a click moves the
camera there), centered (It is centered), then Save position - the only
thing that stores the robot's pose under the stored name (`tip_calib`,
`shake`, `observe`; no new pose) and says "Saved: slot N · X Y Z" (the
slot clicked; else "≈ slot N", the nearest). Done
and × only close (no Cancel), storing nothing. A saved position shows
its line on top with Go to saved position (`Motion.tip_to`, no higher
than the travel height), which marks the steps before Save done and
the nearest slot on the mini deck. The
shake position carries its prerequisites as steps: a tip box (a type
from the drop-down, Settings' tip calibration type first; a mini deck
click loads it there through `Session.load_labware`, as the Deck step
does), put a tip on and calibrate the tip (the Tip step's
`TipOnBlock` and `TipCalibrationBlock`, made once and kept, the run's
holder `SHAKE_HOLDER` in `shell.RUNS`, its way back `shell.RUN_ITEMS`),
the source dish in its holder, the holder's slot, a click on the hole
(the tip over it at the travel height through the pixel map and the
measured offset, `camera_moves.move_tip_to_pixel`), lower it with the pad
(It is in the hole), Save position; the tip stays on. Those first three
are done by fact (`Built.done`): with a calibrated tip on the sheet opens
on the fourth. While a Robot setup sheet is open the page
under it is disabled, so its keys are the sheet's. Reset setup asks (Keep setup primary),
then `Session.reset_setup` zips the profile folder beside it, dated, and
clears the cameras, the pixel map, the deck modules and those three
positions; the deck, the picking settings, the tip calibration and saved
points stay. The last profile is loaded at start
(`Session.load_last_profile`, `default` made if none); profiles are never
chosen on screen. Settings is a sheet (`pages.settings.SettingsSheet`)
over whatever is shown, the breadcrumb gaining "Settings": General (Save,
Revert; closing it discards what was not saved), Advanced and the Log
(over the lock: only Advanced is the robot's). Advanced's
Start new robot session and Disconnect robot session claim the robot's
lease; Disconnect lets the session go (`Session.release_run`) and the
monitor leaves it alone until Continue robot session (Home or Advanced)
or a new session.

A run is seen from every screen (`MainWindow._show_run`): the pill says
what it does (`shell.RUNS`, by the lease's holder; "Paused" and "Waiting
for you" from its page's `run_activity`, re-read on
`Session.run_activity_changed`), and a click on it goes back to the task
and step the run was started from. Home's tiles are dimmed under "The
robot is busy. Go back to <task> to stop or finish it." A new run names
its holder in `RUNS` and gives its page a `run_activity`.

Every jog panel's face is the `widgets.jog_pad.JogPad` (XY arrows, Z Up /
Down, the step carousel of `jog.DEFAULT_STEPS`, the one list of step
sizes): it floats over the picture's bottom-right
(`CameraView.add_corner_widget`) while its panel is shown, folds to its
title row there (`JogPad.set_folded`; folded from the start on
Picking, the keys still bound), and only says what was pressed - the panel moves as before (lease, envelope,
`run_job`), the pad grayed with the reason while the robot is not free.
Undo step is on the pad. A jog panel has no Move or Positions section
(removed, #27; do not bring them back): saved positions are drawn on the
picture's "points" layer (`show_position_on`) and set on their own pages. There is no keys box: the pad's Keys toggle (H) puts
the keys as bound on its buttons and lists the step keys, the panel's
other keys and the page's own (`add_help`) under it. A key added to a page
belongs in that list too; what the mouse does goes in the picture's mouse
row. A text or number field with the keyboard keeps the keys it uses
(never taken from it: the robot would move while a digit is corrected);
the pad then says so (`jog_panel.TYPING_HELP`), and Enter in the field or
a click on the picture or the page gives them back.

A camera's focus is tuned only in Robot setup's camera sheet: while the
chosen camera opens, a spinner and "Opening camera…" over the picture
("No camera is attached." only when none is); from its first frame, for
a device that took a focus - the profile's or one only previewed - the
focus row under the picture (slider, typed number, Default (960)), tried
on the device live. Done saves it (`Session.save_camera_focus`); × saves
nothing and puts back the open camera's focus. No Focus button, no
window of its own, and `CameraView` has no focus tools.

### Manual pick drives the robot from the picture

One job at a time moves the robot or changes its robot session, across
every page: it claims the robot's lease first (`Session.robot_lease`,
`hardware.robot_lease`) and its `Worker(lease=)` gives it back as it ends.
A claim while it is held is refused ("Robot busy."), never queued. Reads
(the pose, the tip record, the lights state) and the lights switch take
none. A run - picking, liquid, the camera sweep, the pipette calibration -
holds it from Start to its end, pauses and needs operator included; the
pipette calibration lends it to the touch-up's panel (`JogPanel.lend`).
While anything holds it, every jog panel and every button that would move
the robot is greyed out, the holder named in the tooltip. Home and connect
(a new robot session) is refused while it is held; closing the window during
a run asks and stops it, and the window closes (and parks Z) only once the
robot is free - else it stays open and says the run is still stopping.

Robot commands from a page with a jog panel go through
`JogPanel.run_job` (which claims the lease), never a worker of the page's
own, so they cannot overlap a key press. Every move across the deck goes through the session's
`workflows.motion.Motion` (`Session.motion`): pages, the jog panel, the
pipette calibration, the liquid run and the picking run alike, never a
travel of their own. Moves across the deck are one force_direct
move_to_coordinates at 1 mm under the Z measured after a retract
(`Motion.raise_tip`, `travel_z`): with a long tip the robot does nothing
for a moveToCoordinates at the retracted height itself. The measured top is
one per robot connection, shared by every caller and dropped when the tip
or the robot changes. A retract is only made when the tip is below that
travel height (or the top is not measured yet), everywhere, the picking
run included. A stored pose (`tip_calib`, `observe`, `shake`, the jog
panel's Go to, and the picking run's own moves to them) is reached no
higher than that travel height (`Motion.reachable`): taught after a
retract, it is out of bounds for the robot.

On Manual pick's Work step (`pages.manual`, REDESIGN §6.6) a **single
left** click moves - the camera or the tip, as "A click moves the [Camera
| Tip]" over the picture's top right says, beside Camera / Tip back to
center (the dish center, `observe`) - checked against the jog envelope
(`Motion.refused`) and through `JogPanel.run_job`; a click on a detected
cuboid sends the tip down to it at the pickup height - except while the
Aspirate card takes its spot By hand: then a click chooses the spot (a
cuboid with Snap to found cuboids, else the point itself) and moves
nothing, a click beside every cuboid with Snap on still moves. A double click
moves nothing. A **right** click (`CameraView.right_clicked`) marks a
**memo point** (`Session.memo_points`: tip XY and a Z or none, named with
the lowest free number M1, M2 ..., in memory until the program closes,
never in the profile); each is deleted by its × (`Session.delete_memo_point`).
There is no Click to move and no Mark points. The tip goes to one only by
the card's Go to. A point below
a deck module's top is flagged (`Session.memo_problem`, the jog
envelope's floors, `Motion.envelope`: the profile's modules over the deck
definition's slots, `core.deck`, in the robot's frame), red on the picture and in its Z. Drawn in cyan
on the "points" layer (`position_marks.memo_items`); on Work only while
"At a point" is chosen in an open Aspirate or Dispense card
(`PositionMarks.show_memo`), and listed there
(`widgets.memo_points.MemoTable`). Aspirate (REDESIGN §6.6): the
settings as one line with Change (height above the dish bottom, or a
well's bottom; volume; speed), From In a well / At a point / By hand,
enabled once a source is chosen, the TipGauge its answer. Dispense opens
by itself when the tip holds something: All | Part, In a well (a mini map
of the plan's plate; a well's bottom plus the deposit offset, from its
well center) / At a point / Here, its button saying what it does ("Go
to A7 and dispense", "Dispense here"), gray while the tip is empty. What
is in the tip is the session's count (`Session.in_tip`,
`Session.aspirate`, `Session.dispense`: all or a part no larger than it;
another tip holds nothing). A new point takes New point Z, the
last Z given (typed into a point, or Z from tip); "travel height" (no Z)
until one is given, so a first click never sends the tip down. Over the
picture's top left is the `TipGauge` (`CameraView.add_top_left_widget`,
X, Y, Z under it), the tip's volume counted from the page's aspirates and
dispenses; Show past picks draws where it aspirated, the last bright
(`PositionMarks.set_picks`); Look at (a `MiniDeckDropdown`) sends the
camera over a slot. Work shows the upper camera only.

The robot reports Z at the tip's end with a tip on and at the nozzle
without one. There are two tops, kept apart in `workflows.motion`: the
measured one above, and the jog envelope's, computed. The jog envelope's
Z top is the retracted height, a hard ceiling (above it the pipette stops
answering until homed): the bare nozzle's 200.6 mm
(`motion.Z_NOZZLE_TOP_MM`, measured on the bench) less
the tip's length (`Tip.length`: the robot's own `pickUpTip` result; else
the rack's whole `tipLength`, which errs low; else 100 mm), less 0.5 mm
for jog steps. A step taken above that top (after a retract or a home)
is one straight move that also comes down to it
(`JogController._from_the_top`): a relative step there keeps a height the
robot refuses. Never an overlap computed here: the robot's differs from
the installed pipette definition's. A jog panel reads the pose again when
the tip changes and when it is shown.

Park in the top bar (all axes home, the head to the back right) is
reachable from every screen: it is refused while the robot's lease is
held, and goes through the shown page's `JogPanel.run_job` when that page
has one, else claims the lease for a worker of its own.
"The robot is busy" (`MainWindow._robot_busy`) is the robot's lease being
held, nothing else: a camera opening, a look for the marker or a picture
saved take no lease and busy nothing.

A section that folds (`theme.factory.Section`, Manual pick's Sizes)
has an arrow as tall as a capital letter (`fold_arrow`), and
folding lays its scroll column out at once (`_ScrollColumn.fit_now`), so
the cards around it do not flicker.

### Liquid handling is a program of blocks

Liquid's Plan step (`pages.liquid_plan`, REDESIGN §6.8) builds a
`core.liquid.Program`: groups of wells chosen on the `PlateGrid`, each with
steps that each carry their own `Location` (this well, a fixed well, a saved
point, here), the groups as fold cards dragged into run order, each step
one line with Edit (Try once, Delete step), Take liquid from (In a well /
At a point / By hand: where "+ Aspirate" takes from), What to run (all
groups or only one), templates as Auto Plan's (`Outputs/templates/
liquid-plans/`). Use this plan hands a copy and the groups to the session
(`Session.set_liquid_plan`); Run (`pages.liquid`, REDESIGN §6.9) runs
that, through a jog panel never shown (`JogPanel.run_job`, lease
`run=True`: STOP, the pill), `workflows.liquid.run(groups=...)`: the plate
with progress on the left, no camera; the footer as Picking's
(`run_status`, `footer_buttons`, `set_need`: READY / RUNNING / PAUSED /
WAITING FOR YOU / DONE / NOT READY, the sentence the run's own,
`LiquidState.status`), Back + Start (+ Continue after a stop), Pause /
Resume + Stop run. A run to its end makes the step done
(`confirm("liquid_run")`). Saved
points are `profile.positions`, not a list of the page's own, saved with
a jog panel's **Save point** (Space, `jog_panel.setup_name_refusal`:
never under Robot setup's `observe`, `shake`, `tip_calib`). The "points"
box on the picture comes from `JogPanel.show_position_on`, so every page
with a jog panel has it. Washing is a program written with these blocks
(measured well center, auto empty, slow lift, pause after the first well),
not a page of its own. Start blows the tip out over the program's trash
(`Program.trash`, a well or a saved point) before the first well; Continue
does not. The plate map spans both card columns (`CardColumns(wide=...)`),
names the well under the pointer, and opens in its own window as a
`PlateMirror` - the same scene, never a copy. A plate's measured well
center (`WellCenter`) is one per slot and plate type, set on Liquid's
Well center step (`pages.well_center`, REDESIGN §6.7: the plates on the
deck, "Measured today" / "Not measured: using the drawing" / "Not measured
yet"; Move the tip, It is centered, It is level, the well depth from the
drawing, Save; Skip this plate, use the drawing - `Session.skip_plate`,
for the robot session only) or on Manual Work's folded Well center card
(Go to well top, jog, Set well center); Liquid handling only reads it. The
step is done when every plate is measured or skipped
(`Session.well_center_status`). Manual Work's Aspirate and Dispense in a well and a
picking deposit add its x and y, Liquid handling's steps all of it. It is the plate's, wherever it
was measured, and every page reads it the same way:
`workflows.well_center` holds the measurement both pages take
(`Measurement`, `go_to_top`), the deposit's offset (`deposit_offset`) and
the plate plan's plate's center (`for_plan`). While a run goes into a plate
(its lease's `plates`), that plate's center is neither forgotten nor its
depth changed (`well_center.run_using`).

### Words on screen

UI strings only; code names stay. The OT-2's HTTP run is a **robot session**
(carried on with by itself; "Home and connect" starts a new one).
There is no Connect: the connection monitor (`gui.robot_monitor`) looks
at the robot every few seconds from the start, carries on with a session
it can (that moves nothing), and leaves a robot with none on "Robot needs
to home" until the operator confirms Home and connect - a hand may be in
the robot. While the robot does not answer, Home says "Connecting to the
robot…" ("This can take a few minutes after it is switched on.": it is
slow to boot, not lost), and "Homing…" while it homes. The dish the
cuboids are picked from is the **source dish** on screen, never "dish
with cuboids". A changed run id is the robot restarted: labware, the tip and
what was confirmed are forgotten (`Session.robot_seen`). A session is shown by the name it was
given when created here (`config.robot_sessions`, default date, time and
profile), else by when the robot started it - never by its id. "run" on
screen means a picking or liquid run. The pipette
is set up under the hood and never mentioned ("pipette loaded" reads as "a
tip is on"). A `Routine` is a **plate plan**, "Plan" on screen (Auto
pick's Plan step, `pages.routine`: the plate from the MiniDeckDropdown,
the wells planned on the PlateGrid (`planning=True`: a click plans a
well and selects it, a click on a planned one selects it, on a selected
one takes it out), the Cuboids field setting only the selected wells
("mixed" when they differ, every planned well drawn with its count),
Fill order as two tiles; done while the plan made by Use this plan is
the session's, which locks the page and turns the button into Edit plan:
that lets the session's plan go, so Plan is not done until Use this plan
again). A **template** is a named plan file (`config.templates`):
`Outputs/templates/plate-plans/` (the old `plate_plan_presets` moved there
when first listed), every one listed; a click asks on the mini deck slot
chooser which plate of its type it goes on, even when only one fits, and
with none says "Put a <type> on the deck." with Go to Deck. Save as
template makes a new file under a name not in use, Save changes to
template writes over the open one, no "are you sure". The run's own file (`Outputs/routines`) is never shown. Stored
poses are set with **Save position** (the shake position's too); the pipette
calibration's way back is **Go to saved position**, the Picking page's
**Camera to dish center**.

### For biologists: stop, guard, say what to do

- **Stop** is Esc, window-wide, and the top bar's STOP while a run has
  the robot; no page binds Esc itself (two shortcuts on one key cancel out). First press:
  `Session.stop_requested`, every page stops at its next safe point. Second
  press within 10 s while work runs: `Session.hold_robot` - the API
  wrapper refuses every command that moves (`RobotHeld`), the one in
  progress finishes, the robot session, labware and tip are kept; it is
  released once no job using the robot runs. Never stop the robot session
  for this: the server's only immediate stop ends the session, and with it
  the labware, the tip and the run's place.
- **Lock** in the top bar (`Session.set_locked`): until Unlock, the API
  wrapper refuses every command that moves (the same hold as the second
  Stop; letting go of one keeps the other) and every lease claim is
  refused, saying `RobotLease.refusal` ("Robot locked."). The lights still
  switch; Park is grayed. The page is under `overlays.LockOverlay` and
  disabled. Refused, and Lock grayed, while a run has the robot.
- A page that runs robot work listens to `stop_requested`.
- What has to be done before a picking run is the **Operation checklist**
  (`gui.readiness.STEPS`, shown with `widgets.checklist`): fixed steps in
  the order of the work, each an instruction with a capital ("Load a
  profile", never "no profile loaded") whose words never change - only
  its mark (done / to do / note / blocked, dimmed while a step it needs is
  not done). What is particular to now goes in the tooltip. A button goes
  to the page where it is done (`Session.page_requested`). It has no page
  of its own any more (the task steps and their banners lead); the
  Picking footer says NOT READY with the step's need, else the first of
  the list or of the page's own set-up (detector, upper camera, clips
  camera - all opened by themselves). Anything to do or blocked
  blocks Start; optional
  steps never do. "Calibrate the tip" is done only while the tip it
  measured is on, in the robot session it was measured in
  (`readiness.tip_calibration_status`).
- Picking settings have bounds (`picking_fields.BOUNDS`), Save lists the
  changes old -> new, and the previous picking.json is archived.
- Errors on screen are sentences (`workers.describe_error`); the log is
  also written to a file per day, and Save report zips it with the profile.

### Settings belong to the computer, not the profile

The robot's address and the output folders (Outputs, Logs, Images) are
`config.app_settings`: `settings.json` beside `profiles/`, edited in the
Settings sheet behind the gear at the end of the top bar.
`paths` applies the folders as overrides, so writers keep asking `paths`.
The address reaches the robot wrapper as `OpentronsAPI(host=...)`;
`--robot-host` overrides it for one start. Nothing hard-codes an address.
The lower camera's mode and crop for pickup clips are settings too
(default 2000x1500, crop 0.5); the Picking page reopens the camera in that
mode, and the pipette calibration reopens it in the profile's own.
The camera calibration sweep's parameters (marker side, dictionary, grid,
degree) are settings too (`AppSettings.sweep_*`); the Camera calibration
sheet does not repeat them.
So is where the models run (`AppSettings.inference_device`: Auto, CPU,
GPU). A `.pt` is run as its ONNX export beside it (`name.onnx`, made by
`python -m micropick.export_models`, checked against the `.pt`; the build
runs it) through ONNX Runtime - DirectML on Windows, so any GPU - and Auto
measures each device that works on the first load and takes the faster
(`core.vision.yolo`). `core.vision.onnx_yolo` gives ultralytics' own boxes:
change its letterbox or NMS only together with a parity check.
So are the tip calibration's (`AppSettings.tip_cal_*`: tip type, frames,
verify, manual adjustment, the start offset dx, dy). The Tip step
(`pages.calibration_pipette`, REDESIGN §6.2) is one column of two cards. Put a tip on:
a Select of the deck's tip boxes, the rack (wells taken here and not put
back gray, `Session.used_tips`), Take tip A3; then "Tip A3 is on." with
Drop in trash and Drop in place (`dropTipInPlace`, nothing moved; no
Put back: it did not work); no tip box, a warning with Back to Deck.
Calibrate the tip, its steps dimmed until a tip is on: Start tip
calibration (the camera to the calibration position), The disc is
there, Calibrate (a spinner; its result saved as it ends, the tip left
over the crosshair) - one run from Start to its end (lease `run=True`,
STOP, the pill), waiting for the operator at the disc, where Stop ends
it with nothing saved; then "Tip calibrated." with Redo calibration and
Check: tip to
crosshair, and under it the folded, optional Fine-tune by hand (the
lower camera and an XY pad while the tip is over the crosshair, else
Check first; Save gray until a step, a short job, not a run). Liquid's Tip step shows no calibration (`set_task`). Its
cameras are the profile's upper and lower ones.
The Dish bottom step (`pages.dish_bottom`, REDESIGN §6.4) has no camera:
a big Z readout, Up and Down with the step carousel between them
(`jog_pad.StepCarousel`), active only while lowering, through a jog panel
that is never shown (its keys on itself, so they never fire); the page's
own keys from the key actions (Z up / down, the step sizes, H) while
lowering, no XY, listed by Keys. "Last time: Z 66.40 mm · 3 days ago"
with Use it while it was ever measured; Tip over the dish center (Move
the tip), Lower until it touches, whose button is Save dish bottom (a
change over 3 mm asked first; the tip raised after); then "Dish bottom:
Z 66.42 mm" with Redo (back to Move the tip, the value kept until a new
one is saved).
Done when measured (`Session.set_dish_bottom`) or Use it
(`Session.use_last_dish_bottom`) in this robot session, never by Next.
The Picking step (`pages.picking`, REDESIGN §6.5): the dish picture with
"N cuboids found" and Camera to dish center over its top right (during a
run only while paused or waiting, under the run's lease lent to the jog
panel, the tip empty; else "Pause to move the camera"); beside it, with
no panel scroll, Analyze the dish and Shake the dish, then two FoldCards,
one open at a time - Cuboid sizes (the histogram whose window is dragged
and saved like the settings' Save, the pickup line, More settings, All
measures: `widgets.cuboid_stats`) before a run, Plate · slot N (mini
`PlateGrid`, Filled / Planned, current well ringed) during it - then Save
pickup clips and the jog panel (its pad over the picture). After a run that left wells of
the plan short, "N wells were not filled" with Finish by hand
(`Session.task_requested`: Manual pick's Work, Dispense on In a well and
the first of them). The positions are Robot setup's.
The footer is the run's status: the page's `run_status` and
`footer_buttons`, read by the shell for any step page that has them -
READY / PICKING / PAUSED / WAITING FOR YOU / DONE / NOT READY and one
sentence (`PickingSession.status` while it runs), Back + Start picking,
or Pause / Resume (yellow while held) + Stop run. The shell passes the
step's need down (`set_need`): Start waits for it. While a run's page
says "waiting" the window flashes in the taskbar (`QApplication.alert`,
`MainWindow._show_run`). Settings
keeps Save and Revert at its bottom right, outside the scrolling columns.

A key line on the pad, like an item of the mouse row, is "keys   what it
does", split at its first three spaces.

The keys that move the robot (and Manual pick's aspirate, dispense) are
`gui.keys` actions with today's keys as defaults, rebound on the Settings
page's Keys card (`AppSettings.keys`, only the changed ones stored). The
jog panel and Manual pick's Work bind from it, rebind on save, and the pad
names the keys as bound. A new robot key is an action there, never a
hard-coded QShortcut.

### Words on screen: short and neutral

An explanation on screen is one short sentence of what a control does or
what state something is in: no judgement ("worth it"), no reasoning, no
directions to another page when the button beside it goes there. Status
lines read "Robot busy.", "No profile loaded.", "Not set.". The reasoning
belongs in docstrings.

Words on screen are spelled American: center, color, gray, analyze,
neighbor, millimeter, labeled, canceled. Only the strings shown (and the
log's), except for "center": that is spelled American everywhere - code
names, docstrings, comments and saved keys (`WellCenter`, `well_centers`
in deck.json). A file written by 0.4.0 or earlier has "centre" keys
(`well_centres`, `centre_error_px`); they are still read
(`AliasChoices`) and written back spelled "center".

The Settings page's second column is the loaded profile's, saved into it at
once: the machine learning models, the calibration summary. Which
attached camera is the upper and which the lower is Robot setup's camera
sheets'.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for steiva/micropick (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
