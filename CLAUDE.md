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

- On the Picking page, *Analyse the dish* draws its contours over the
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
the tab along the top is the title. Tabs on the left are the steps of a
day's work in order (Profile, Robot & Deck, Tip calibration, Plate plan,
Picking); tabs on the right are tools used when needed (Camera
calibration, Liquid handling, Manual control, Log), titled in teal
(`shell.ASIDE_INK`).

The keys a page binds and what the mouse does are listed in a box at the
picture's bottom-right (`CameraView.set_help`, fed by `JogPanel.help_lines`
plus the page's `add_help`); H toggles it. A key added to a page belongs in
that list too.

A camera's focus is tuned only in its own window (the status bar camera
button, which toggles it): `CameraView.enable_focus_tools` - slider, typed
number, default 960, save to profile - is off on the pages.

### Manual control drives the robot from the picture

Robot commands from a page with a jog panel go through
`JogPanel.run_job`, never a worker of the page's own, so they cannot overlap
a key press. Moves across the deck are one force_direct
move_to_coordinates at 1 mm under the Z measured after a retract
(`workflows.manual.raise_tip`, `travel_z`): with a long tip the robot does
nothing for a moveToCoordinates at the retracted height itself. A retract
is only repeated when the tip is below that travel height. A stored pose
(`tip_calib`, `observe`, `shake`, the jog panel's Go to, and the picking
run's own moves to them) is reached no higher than that travel height
(`reachable_z`): taught after a retract, it is out of bounds for the robot. Click-to-move is off on every entry to the
tab.

Home robot position in the status bar is reachable from every page: it is
refused while any jog panel or any worker is busy, and goes through the
shown page's `JogPanel.run_job` when that page has one.

### Liquid handling is a program of blocks

The Liquid handling tab (right side, beside Manual control) builds a
`core.liquid.Program`: groups of wells chosen on the plate map, each with
steps that each carry their own `Location` (this well, a fixed well, a saved
point, here); `workflows.liquid` runs it through `JogPanel.run_job`. Saved
points are `profile.positions`, not a list of the page's own. The "points"
box on the picture comes from `JogPanel.show_position_on`, so every page
with a jog panel has it. Washing is a program written with these blocks
(measured well centre, auto empty, slow lift, pause after the first well),
not a page of its own.

### Words on screen

UI strings only; code names stay. The OT-2's HTTP run is a **robot session**
("Connect" carries on with the robot's session or starts one, "Start new
robot session", "Disconnect robot session", status bar "session <name>
(continued)"). A session is shown by the name it was
given when created here (`config.robot_sessions`, default date, time and
profile), else by when the robot started it - never by its id. "run" on
screen means a picking or liquid run. The pipette
is set up under the hood and never mentioned ("pipette loaded" reads as "a
tip is on"). A `Routine` is a **plate plan** (the Plate plan tab). Stored
poses are set with **Set position** / **Set shake position**; the pipette
calibration's way back is **Go to last saved**, the Picking page's **Go to
picking position**.

### For biologists: stop, guard, say what to do

- **Stop** is the status bar's red button and Esc, window-wide; no page
  binds Esc itself (two shortcuts on one key cancel out). First press:
  `Session.stop_requested`, every page stops at its next safe point. Second
  press within 10 s while work runs: `Session.halt_robot` stops the robot
  session (the server's only immediate stop) and a dialog says what next.
- A page that runs robot work listens to `stop_requested`.
- What has to be done before a picking run is the **Operation checklist**
  (`gui.readiness.STEPS`, shown with `widgets.checklist`): fixed steps in
  the order of the work, each an instruction with a capital ("Load a
  profile", never "no profile loaded") whose words never change - only
  its mark (done / to do / note / blocked, dimmed while a step it needs is
  not done). What is particular to now goes in the tooltip. A button goes
  to the page where it is done (`Session.page_requested`). The Profile page
  shows it in full; the Picking page shows its own steps (detector,
  camera, clips) under "Complete the Operation checklist". Anything to do
  or blocked, in either, blocks Start; optional steps never do.
- Picking settings have bounds (`picking_fields.BOUNDS`), Save lists the
  changes old -> new, and the previous picking.json is archived.
- Errors on screen are sentences (`workers.describe_error`); the log is
  also written to a file per day, and Save report zips it with the profile.

### Settings belong to the computer, not the profile

The robot's address and the output folders (Outputs, Logs, Images) are
`config.app_settings`: `settings.json` beside `profiles/`, edited on the
Settings page behind the gear in the status bar (a page with no tab).
`paths` applies the folders as overrides, so writers keep asking `paths`.
The address reaches the robot wrapper as `OpentronsAPI(host=...)`;
`--robot-host` overrides it for one start. Nothing hard-codes an address.
The lower camera's mode and crop for pickup clips are settings too
(default 2000x1500, crop 0.5); the Picking page reopens the camera in that
mode, and the pipette calibration reopens it in the profile's own.

The Settings page's second column is the loaded profile's, saved into it at
once: which attached camera is the upper and which the lower
(`Session.assign_camera_devices`, the specs' `device_name`; choosing the
other's device swaps them; a profile without a camera for a role gets one
from `Session.add_camera`, the bench's spec on the chosen device), the
machine learning models, the calibration summary. The Profile page is
Robot and Load profile, and under them the checklist before a run.
