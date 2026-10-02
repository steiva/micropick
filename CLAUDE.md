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
the tab along the top is the title.

The keys a page binds and what the mouse does are listed in a box at the
picture's bottom-right (`CameraView.set_help`, fed by `JogPanel.help_lines`
plus the page's `add_help`); H toggles it. A key added to a page belongs in
that list too.

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
("New robot session + home", "Continue with current robot session", status
bar "session <id> (continued)"); "run" on screen means a picking or liquid
run. The pipette
is set up under the hood and never mentioned ("pipette loaded" reads as "a
tip is on"). A `Routine` is a **plate plan** (the Plate plan tab). Stored
poses are set with **Set position** / **Set shake position**; the pipette
calibration's way back is **Go to last saved**, the Picking page's **Go to
picking position**.

### Settings belong to the computer, not the profile

The robot's address and the output folders (Outputs, Logs, Images) are
`config.app_settings`: `settings.json` beside `profiles/`, edited on the
Settings page behind the gear in the status bar (a page with no tab).
`paths` applies the folders as overrides, so writers keep asking `paths`.
The address reaches the robot wrapper as `OpentronsAPI(host=...)`;
`--robot-host` overrides it for one start. Nothing hard-codes an address.
