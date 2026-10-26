# micropick

Lab robot (Opentrons OT-2 gantry, pipette, upper and lower cameras) that
picks cuboid microtissues from a dish into a plate. Design notes are in
`docs/DESIGN.md`; bench checks for the GUI in `docs/GUI_BENCH_CHECKS.md`.

## Running things

- Interpreter: conda env `lab` (`conda run -n lab python ...`); the bare
  `python` on PATH is the Windows Store stub.
- Tests: `conda run -n lab python -m pytest -q` (whole suite, ~20 s).
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
  settings right) and then gives the session its go-ahead at once;
  Resume is only for leaving `needs_operator`.

### Camera view chrome

Text about the camera or the run (resolution, fps, zoom, robot status, gantry
position) is drawn by `CameraView` in widget pixels as semi-transparent
boxes over the picture, never into the frame and never as a separate panel.

A camera view and its side panel sit in a `widgets.feed_row.FeedRow`: the
picture gets the width its frame shape needs (4:3 for the upper camera),
the panel the rest, within 1-2x its designed width. Pages carry no title;
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
step not done. Next is enabled when the step is done or optional; Deck is
confirmed by its own Next. A Robot setup item needed by a step opens its
page as `Home › <task> › <item>` (`shell.SETUP_PAGES`) until Robot setup
has its own sheets. The last profile is loaded at start
(`Session.load_last_profile`, `default` made if none); profiles are never
chosen on screen. The Log is reached from Settings.

A run is seen from every screen (`MainWindow._show_run`): the pill says
what it does (`shell.RUNS`, by the lease's holder; "Paused" and "Waiting
for you" from its page's `run_activity`, re-read on
`Session.run_activity_changed`), and a click on it goes back to the task
and step the run was started from. Home's tiles are dimmed under "The
robot is busy. Go back to <task> to stop or finish it." A new run names
its holder in `RUNS` and gives its page a `run_activity`.

The keys a page binds and what the mouse does are listed in a box at the
picture's bottom-right (`CameraView.set_help`, fed by `JogPanel.help_lines`
plus the page's `add_help`); H toggles it. A key added to a page belongs in
that list too. A text or number field with the keyboard keeps the keys it
uses (never taken from it: the robot would move while a digit is
corrected); the box then says so (`jog_panel.TYPING_HELP`), and Enter in
the field or a click on the picture or the page gives them back.

A camera's focus is tuned only in its own window
(`widgets.feed_window.FeedWindow`; its status bar button went with the
status bar, and it comes back in Robot setup's camera sheets):
`CameraView.enable_focus_tools` - slider, typed number, default 960, save
to profile - is off on the pages.

### Manual control drives the robot from the picture

One job at a time moves the robot or changes its robot session, across
every page: it claims the robot's lease first (`Session.robot_lease`,
`hardware.robot_lease`) and its `Worker(lease=)` gives it back as it ends.
A claim while it is held is refused ("Robot busy."), never queued. Reads
(the pose, the tip record, the lights state) and the lights switch take
none. A run - picking, liquid, the camera sweep, the pipette calibration -
holds it from Start to its end, pauses and needs operator included; the
pipette calibration lends it to the touch-up's panel (`JogPanel.lend`).
While anything holds it, every jog panel and every button that would move
the robot is greyed out, the holder named in the tooltip. Home the robot
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
retract, it is out of bounds for the robot. Click-to-move is off on every
entry to the tab.

On Manual control a **double** click moves (`CameraView.double_clicked`,
`double_click_fits` off there: the wheel zooms back out); with Mark points
ticked (off by default) a click marks a **memo point** (`Session.memo_points`:
tip XY and a Z or none, named with the lowest free number M1, M2 ...,
in memory until the program closes, never in the profile). The tip goes
to one only by the card's Go to, never by a double click. A point below
a deck module's top is flagged (`Session.memo_problem`, the jog
envelope's floors, `Motion.envelope`: the profile's modules over the deck
definition's slots, `core.deck`, in the robot's frame), red on the picture and in its Z. Drawn in cyan
on the "points" layer of every page (`position_marks.memo_items`; that
layer is on by default on Manual control) and listed in the Memo points
card (`widgets.memo_points.MemoTable`). A new point takes New point Z, the
last Z given (typed into a point, or Z from tip); "travel height" (no Z)
until one is given, so a first click never sends the tip down.

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

A section that folds (`theme.factory.Section`, the labware page's Deck
modules) has an arrow as tall as a capital letter (`fold_arrow`), and
folding lays its scroll column out at once (`_ScrollColumn.fit_now`), so
the cards around it do not flicker.

### Liquid handling is a program of blocks

The Liquid handling tab (right side, beside Manual control) builds a
`core.liquid.Program`: groups of wells chosen on the plate map, each with
steps that each carry their own `Location` (this well, a fixed well, a saved
point, here); `workflows.liquid` runs it through `JogPanel.run_job`. Saved
points are `profile.positions`, not a list of the page's own. The "points"
box on the picture comes from `JogPanel.show_position_on`, so every page
with a jog panel has it. Washing is a program written with these blocks
(measured well center, auto empty, slow lift, pause after the first well),
not a page of its own. Start blows the tip out over the program's trash
(`Program.trash`, a well or a saved point) before the first well; Continue
does not. The plate map spans both card columns (`CardColumns(wide=...)`),
names the well under the pointer, and opens in its own window as a
`PlateMirror` - the same scene, never a copy. A plate's measured well
center (`WellCenter`) is one per slot and plate type, set on Liquid
handling or on Manual control's Well card (Go to well top, jog, Set well
center); Manual control's Go to well and a picking deposit add its x
and y, Liquid handling's steps all of it. It is the plate's, wherever it
was measured, and every page reads it the same way:
`workflows.well_center` holds the measurement both pages take
(`Measurement`, `go_to_top`), the deposit's offset (`deposit_offset`) and
the plate plan's plate's center (`for_plan`). While a run goes into a plate
(its lease's `plates`), that plate's center is neither forgotten nor its
depth changed (`well_center.run_using`).

### Words on screen

UI strings only; code names stay. The OT-2's HTTP run is a **robot session**
(carried on with by itself; "Home the robot" starts a new one).
There is no Connect: the connection monitor (`gui.robot_monitor`) looks
at the robot every few seconds from the start, carries on with a session
it can (that moves nothing), and leaves a robot with none on "Robot needs
to home" until the operator confirms Home the robot - a hand may be in
the robot. A changed run id is the robot restarted: labware, the tip and
what was confirmed are forgotten (`Session.robot_seen`). A session is shown by the name it was
given when created here (`config.robot_sessions`, default date, time and
profile), else by when the robot started it - never by its id. "run" on
screen means a picking or liquid run. The pipette
is set up under the hood and never mentioned ("pipette loaded" reads as "a
tip is on"). A `Routine` is a **plate plan** (the Plate plan tab). Stored
poses are set with **Set position** / **Set shake position**; the pipette
calibration's way back is **Go to saved position**, the Picking page's **Go to
picking position**.

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
  Picking page's step 1 lists what is left of it on
  other pages and the page's own set-up while not ready (detector, upper
  camera, clips camera - all opened by themselves); the Start card lists
  nothing, it names the steps that stop it. Anything to do or blocked
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
`config.app_settings`: `settings.json` beside `profiles/`, edited on the
Settings page behind the gear at the end of the top bar.
`paths` applies the folders as overrides, so writers keep asking `paths`.
The address reaches the robot wrapper as `OpentronsAPI(host=...)`;
`--robot-host` overrides it for one start. Nothing hard-codes an address.
The lower camera's mode and crop for pickup clips are settings too
(default 2000x1500, crop 0.5); the Picking page reopens the camera in that
mode, and the pipette calibration reopens it in the profile's own.
The camera calibration sweep's parameters (marker side, dictionary, grid,
degree) are settings too (`AppSettings.sweep_*`); the Camera calibration
page shows them and links to Settings.
So is where the models run (`AppSettings.inference_device`: Auto, CPU,
GPU). A `.pt` is run as its ONNX export beside it (`name.onnx`, made by
`python -m micropick.export_models`, checked against the `.pt`; the build
runs it) through ONNX Runtime - DirectML on Windows, so any GPU - and Auto
measures each device that works on the first load and takes the faster
(`core.vision.yolo`). `core.vision.onnx_yolo` gives ultralytics' own boxes:
change its letterbox or NMS only together with a parity check.
So are the tip calibration's (`AppSettings.tip_cal_*`: tip type, frames,
verify, manual adjustment). The Tip calibration page is numbered steps,
each a card with a mark: place the disc (ticked by the operator), set the
disc position, put a tip on, calibrate, nudge the tip (only while it
waits); its cameras are the profile's upper and lower ones.
Camera calibration is the same (`widgets.step_card.StepCard`): place the
marker (done when it is seen), center it (done within two of its sides of
the center), sweep, save the result; the upper camera, no chooser.
Picking is the same, leading to the run: complete the Operation checklist,
set the picking position, the shake position, the dish bottom (optional),
analyze the dish, start picking; the jog panel last. Steps 2-4 are marked
from the checklist's own lines. On Picking the size histogram is a card
over the picture's bottom-left corner (`widgets.size_panel`), not drawn
into the view: the size window is dragged on it and saved like the
settings' Save. Its expand button toggles the cuboids' figures, a window
of their own (`widgets.cuboid_stats`: counts, a histogram per picking
measure against its window, a table). Settings
keeps Save and Revert at its bottom right, outside the scrolling columns.

The keys box on a picture has a bold "Keyboard shortcuts" title and the
keys in blue (`camera_view.HELP_KEY`); a help line is "keys   what it
does", split at its first three spaces.

The keys that move the robot (and Manual control's aspirate, dispense) are
`gui.keys` actions with today's keys as defaults, rebound on the Settings
page's Keys card (`AppSettings.keys`, only the changed ones stored). The
jog panel and Manual control bind from it, rebind on save, and the key box
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
once: which attached camera is the upper and which the lower
(`Session.assign_camera_devices`, the specs' `device_name`; choosing the
other's device swaps them; a profile without a camera for a role gets one
from `Session.add_camera`, the bench's spec on the chosen device), the
machine learning models, the calibration summary.

## Agent skills

### Issue tracker

Issues live in GitHub Issues for steiva/micropick (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
