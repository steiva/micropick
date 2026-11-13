# micropick

Lab robot (Opentrons OT-2 gantry, pipette, upper and lower cameras) that
picks cuboid microtissues from a dish into a plate. Design notes are in
`docs/DESIGN.md`; bench checks for the GUI in `docs/GUI_BENCH_CHECKS.md`.

## Running things

- Interpreter: conda env `lab` (`conda run -n lab python ...`); the bare
  `python` on PATH is the Windows Store stub.
- Tests: `conda run -n lab python -m pytest -q -n auto tests` (whole suite,
  in parallel through pytest-xdist; drop `-n auto` for one file). The mock
  robot moves at once in the tests (`tests/conftest.py`): a test that needs
  a move to take time passes `speed_mm_s`. A shown page or sheet (and a
  tip change) has its jog panel read the pose, and a busy panel refuses a
  job, never queues it: a test waits for the lease and the panels to be
  free before a click that moves the robot, and for what a job shows, not
  only for the lease (let go on the worker's thread, before the panel
  knows). It waits through the GUI conftest's fixtures, never a loop of
  its own: `until(condition, what=...)` (fails the test naming `what`,
  and runs no events once the condition holds), `robot_free(session or
  window, *panels)` (no lease held, no panel's `job_pending`) and
  `read_pose(panel)`. A panel's job is over when `job_pending` is False
  (`busy` is the same: a panel stays busy until its job's end reaches the
  page, GUI-06).
  A hung test is ended after 5 minutes, its stacks dumped after 2
  (`pyproject.toml`, pytest-timeout). A test that needs the program's theme takes `themed`
  (`tests/gui/conftest.py`), never `apply_theme` on the app: it stays for
  every later test, and a MainWindow then builds in 1.1 s, not 0.25 s.
  On `offscreen` a `QDrag` returns at once: a real drag is tested on
  `minimal`, in a process of its own (`tests/gui/step_list_drags.py`).
- GUI: `micropick-gui.bat` (a desktop shortcut points to it) or `conda run -n lab python -m micropick.gui` (`--mock` for no hardware).
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
bump it, add a CHANGELOG.md entry, commit, tag `v<version>`, push the tag:
`.github/workflows/release.yml` builds on Windows and publishes the
installer and zip as that tag's GitHub Release (the models' `.pt` come from
the release tagged `models`). `main` is the current program; `legacy` is
0.4.0, before the redesign. The window title, the log and a report show the version
and, in a build, its commit.

## Decisions to keep

### Picking: detection is live, a run holds its decision frame

- On the Picking page, *Detect cuboids* draws its contours over the
  **live** feed. If the dish moves afterwards, the contours visibly stop
  matching, which is the cue to detect again. There is no "back to live".
- During a **run**, the view follows `PickView` from `workflows/picking`:
  live while the session waits for the operator, otherwise the **frame the
  decision was made from is held**, unchanged, with the overlays measured on
  it. Do not make the run view always-live — the overlays belong to that one
  frame, and drawing them over a newer one shows contours where nothing was
  measured.
- Start picking asks for confirmation ("Start picking?", the dish and
  plate in place, lids off, settings checked, the robot moving on Start;
  Start the default) and then gives the session its go-ahead at once.
  The panel's one Pause / Resume button, in Start's place during a run,
  unpauses, or leaves `needs_operator`; nothing else gives a go-ahead.

### Camera view chrome

Over the picture is only what is used there (REDESIGN §3, MANUAL.md §2.2), in
one overlay style (`theme.overlay_box`, `overlay_button`, #109), what the feeds
have in common in the same places. The status plate at the top left: Manual's TipGauge
(`add_top_left_widget`), the robot's X, Y and Z one per line (fixed font), the
step (`CameraView.set_step_line`), dividers between; nothing under it but
Manual pick Run's height line (`set_height_line`), its row kept. At the top
right the chips (Crosshair, Points, Manual's Past picks, the Tip step's
Detections, Sizes where there is a histogram) and the picture button, left of them the page's buttons (`add_top_widget`, which
restyles them), then the page's lines (`set_status`: found counts, the marker
seen, what holds while it is true). One quiet mouse row bottom-left
(`CameraView.set_mouse_help`, "gesture what it does" per item). No native
checkbox and no axes on a picture. Never the camera name, slot (but in that
height line), resolution, crop, zoom, fps or "TIP EMPTY". All drawn by
`CameraView` in widget pixels, never into the frame and never as a separate
panel.

A message is never on the picture or a line of a page's own under X Y Z:
it is the toast (`widgets.toast`; `JogPanel.tell`, `MainWindow.say`,
`show_toast`), at the window's bottom left on every screen, over the
Footer and a run's status, moving nothing and taking no click. An answer
is green for 3 s; a refusal (`JogPanel.refuse`, `MainWindow.refuse`,
`refusal=True`) red for 6 s. A new one replaces the one showing. A refusal
is a sentence in the operator's words ("Not reachable: Z 50 is below the
platform in slot 8.", `motion.refusal`), no REFUSED, no pose. The
StatusPill carries no message.
A press on a widget floating over the picture (the jog pad's card, the
TipGauge, a page's button) is that widget's, even beside its buttons, and a
press on what is painted there (the plate, the page's lines, the mouse row,
the histogram: `CameraView._chrome`) is nobody's: never a click on the picture. A page whose click moves the robot names
it with `CameraView.set_click_to_move`; without a pixel map that line is
"Use the arrows to move the camera." and no click is sent.

A camera view and its side panel sit in a `widgets.feed_row.FeedRow`: the
picture is 4:3 (the upper camera's) whatever it shows, another shape
letterboxed in it with black bars, so a switch of cameras resizes
nothing, and the box gives way before the panel, still 4:3. The panel's content
keeps its designed width (one 440 px column; two 400 px columns on Manual pick)
and is centered in the rest, its cards never stretched (TIP.md §2.1). Picking
keeps the old rule, the panel taking the rest within 1-2x its designed width
(`side_grows`), until it is redesigned; Liquid's Plan and the camera sheet
have no `FeedRow` and stay as they are for now. Pages carry no title;
the TopBar's breadcrumb is the title.

Manual pick's Run (#113, MANUAL.md §1) is the picture, then
`widgets.work_columns.WorkColumns`: from a window of about 1600 px two columns of
400 px, the work (In place, Aspirate, Dispense, the Retract card pinned under them)
and Points at the full height, its list filling it; under that one column of 420
px, Points a card after Dispense, folded by default (`FoldCard.set_folds`). The widths are fixed, the picture takes
the rest (`FeedRow`, `width_in`), so a card's width never follows what it shows,
long text wraps or is cut, and no card is under 380 px. A row of buttons in these
cards has to fit a column, or goes one under the other. Look at and Detect cuboids
are a row over the chips (`CameraView.add_top_row_widget`), the Past picks chip
among them (`add_chip`) and the page's lines under them, "23 cuboids found · 9 in
size", on Manual only. Sizes are the picture's Sizes chip and, read-only, a popover
from the size window on Snap's row (`manual._SizesPopover`). The page has no status
of its own: what holds is such a line, an answer a toast.

### Nothing shifts

The frame never moves (#106): the camera view (`FeedRow`), the width of a card
and of a column (`WorkColumns`). Only what the operator clicks moves others, and
only down the column it is in: a Section, Change, a lifting row, Keys, a Save as
template's name row, and a choice showing other controls, which is as tall as what
it shows, no room kept for a choice not made (From, Where and the height fields on
Manual pick's Run, Liquid in tip's From). Show and hide those inside
`theme.factory.laid_out_at_once`, so nothing is painted at the old height (a
`FoldCard` does so itself). Two things a job's end moves: Aspirate and Dispense
folding and opening (#114) move what is under them, and so does the Tip card's rack
folding away when a tip goes on (#121).

A status line or a warning moves nothing: a message is the toast, and what comes
and goes by itself has its place kept, `theme.factory.reserve` for a line or a
field, and `choice_stack` for what the robot or a run changes, one page at a time in the
room of the tallest (the Tip card's rack or warning, its Pick up or drop
buttons, a liquid run's banners): a card must not leave from under the operator's hand. A test says it
with `stands_still` (`tests/gui/conftest.py`), and for a choice that the frame
and the card's own top stay (`tests/gui/test_nothing_shifts.py`).

### The shell: Home, tasks as steps (REDESIGN §4)

There are no tabs, no status bar, no floating next step and no Profile
page (removed in redesign phase 2; do not bring them back). The window is
`widgets.top_bar.TopBar` (breadcrumb `Home › <task>`, Light, Lock, Park, Retract, STOP
and the StatusPill while a run has the robot, Settings) over
one stack: Home (`pages.home`: the robot, the Robot setup, three task
tiles) and the existing pages, reused as the content of a **task**'s
**steps** (`gui.tasks`; `shell.STEP_PAGES` maps step to page). While a
step is shown, `StepBar` is over it and `Footer` (Back, Next; on Manual
pick's Run Back and Finish, which goes Home, no status line) under it.
A step's done mark and first unmet need come from `tasks.step_state` each
time, never from a visit; a step whose need is not met is dimmed
(`overlays.DimCover`) and disabled - so its keys do nothing - under one
banner naming that need with a button there. A task opens on its first
step not done. Next is enabled when the step is done or optional, and
only moves on: nothing is confirmed by it. A value is saved by a Save of
its own (Enter in its field); a page left with one typed and not saved
says so in `unsaved()` and is asked about over the page shown next, Save
/ Discard (`MainWindow._ask_unsaved`, `save_unsaved`, `discard_unsaved`),
as the platforms sheet asks. What the robot does and measures itself
(Calibrate, Save dish bottom, Use this plan) is saved at once and said. Deck is done while the robot
is connected, something is on the deck and there is no deck problem; a
move by drag goes through `Session.move_labware`, which loads again so a
platform's height attaches. Auto pick is Deck → Tip → Dish bottom → Plan
→ Well center → Run (no Tip liquid step: it is Picking's card), and a task opens
on its first step not done that is not optional. Manual pick is Deck →
Tip → Well center → Dish bottom → Run, the same steps whatever is chosen (#131,
`tasks.steps(task)`): Well center and Dish bottom are optional there
(`tasks.optional`), Dish bottom still dimmed under its banner without the position
over the dish, Next working, and Run needs only the Deck and the Tip. An optional
step's number has a dashed ring in the StepBar until it is done, the current step's
too, in the room the chip has (#129, `tasks.MARKED_OPTIONAL`, a list of its own:
Manual's Run can be passed and has no ring). Finish by hand opens Run. The last step of every
task is titled Run on screen (#65: Auto's `picking`, the Picking page;
Manual's `work`, `pages.manual`; Liquid's `run`); the run statuses and Start
picking keep their words. Plan needs a plate on the deck (a "plate" need, "Put a plate
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
and Home's "N steps left" read; Done at its bottom right goes to Home. A row, or a Robot setup item needed by a
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
the marker (It is in place; drawn as its outline alone, nothing saying which way
up it lies); its slot on the mini deck slot chooser
(`widgets.slot_chooser.SlotChooser`: all 12 slots, the `mini_deck.Tile`s of
every mini deck with what stands there (`labware_kinds.deck_tiles`), nothing
preselected, grayed while the robot is busy; a click moves the camera there at once,
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
"What stands in this slot?" (the profile's module types or Nothing; a
tile's × is Nothing for its slot), its height, New type and Delete type - all a draft until Save changes
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
does; once on, Change takes every tip box off for the choice
again), put a tip on and calibrate the tip (the Tip step's
`TipOnBlock` and `TipCalibrationBlock`, made once and kept, the run's
holder `SHAKE_HOLDER` in `shell.RUNS`, its way back `shell.RUN_ITEMS`),
the source dish in its holder, the holder's slot, a click on the hole
(the tip over it at the travel height through the pixel map and the
measured offset, `camera_moves.move_tip_to_pixel`), lower it with the pad
(It is in the hole), Save position; the tip stays on. The pose is the nozzle's, so it
is saved with that tip's calibration (`Calibration.shake_tip`) and every move there
takes `Profile.shake_pose`, shifted by the tip calibrated now (the same tip
measured again shifts nothing). Those first three
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
(Save report, no Clear; over the lock: only Advanced is the robot's). Advanced's
Start new robot session, Home and Disconnect robot session claim the
robot's lease; Home (`Session.home`) asks first, since the tip calibration
is then to do again (`Session.homed_at`: the Tip step not done); Disconnect lets the session go (`Session.release_run`) and the
monitor leaves it alone until Continue robot session (Home or Advanced)
or a new session.

A run is seen from every screen (`MainWindow._show_run`): the pill says
what it does (`shell.RUNS`, by the lease's holder; "Paused" and "Waiting
for you" from its page's `run_activity`, re-read on
`Session.run_activity_changed`), and a click on it goes back to the task
and step the run was started from. Home's tiles are dimmed under "The
robot is busy. Go back to <task> to stop or finish it." A new run names
its holder in `RUNS` and gives its page a `run_activity`.

Every jog panel's face is the `widgets.jog_pad.JogPad`, **Controls** on
screen (#110; XY arrows, Z Up / Down, the step carousel of
`jog.DEFAULT_STEPS`, the one list of step sizes): 340 px wide wherever it
is, it floats over the picture's bottom-right
(`CameraView.add_corner_widget`) while its panel is shown, folds to its
header there - the title is the fold toggle, its arrow before it, apart
from Undo step and Keys, which stay (`JogPad.set_folded`, the keys still
bound). The fold is the screen's for as long as the program runs: a page
is built once and keeps its pad, so no state of its own and no other
page's follows; Picking and the Tip step start folded, the others open (a sheet
hosting the Tip blocks keeps its own pad), and the Tip step's
two pads share one (`fold_changed`). It only says what was pressed - the panel moves as before (lease, envelope,
`run_job`), the pad grayed with the reason while the robot is not free.
While the pad is on screen, folded or not, its step is the status plate's
last line, "STEP · 1 mm" in `step_ink` (#99, #109: accent-hover, warn and red
as `step_color`), from `JogPad.step_changed`; a `jog_pad.StepChip` where there
is no picture (Well center). The step changes on the pad and by its keys.
Dish bottom's cards carry their own steps, so none there; on Liquid's Plan it
stays with the pad hidden behind Controls (`JogPanel.keep_step_shown`).
Undo step is on the pad. A jog panel has no Move or Positions section
(removed, #27; do not bring them back): saved points are drawn on the
picture's "points" layer (`show_position_on`), Robot setup's positions
never (#61), each set on its own sheet. There is no keys box: the pad's Keys toggle (H) puts
the keys as bound on its buttons, under the icon, and lists the step keys,
the panel's other keys and the page's own (`add_help`) under it, folded or
not. A key added to a page
belongs in that list too; what the mouse does goes in the picture's mouse
row. A text or number field with the keyboard keeps the keys it uses
(never taken from it: the robot would move while a digit is corrected);
the pad then says so (`jog_panel.TYPING_HELP`), and Enter in the field or
a click on the picture or the page gives them back. A panel's keys are
on while its pad is on screen, wherever the page put it (Well center
hides the panel and shows its pad in its own column), and off while the
pad is hidden, so no page out of sight takes a key (#57).

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
holds it from Start to its end, pauses and needs operator included; Picking
lends it to its jog panel while paused or waiting (`JogPanel.lend`).
While anything holds it, every jog panel and every button that would move
the robot is greyed out, the holder named in the tooltip. Home and connect
(a new robot session) is refused while it is held; closing the window during
a run asks and stops it, and the window closes (and parks Z) only once the
robot is free - else it stays open and says the run is still stopping.

Robot commands from a page with a jog panel go through
`JogPanel.run_job` (which claims the lease), never a worker of the page's
own, so they cannot overlap a key press. A pose read is a robot command:
never one on the GUI thread (the window and its picture wait on it, #36)
- a click takes `JogPanel.pose` - and a job ending in a `Motion` move
returns `jog.Reached` with the pose that move read back, so the panel
does not read it again. Every move across the deck goes through the session's
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

On Manual pick's Run step (`pages.manual`, MANUAL.md) a **single
left** click moves only the camera (#59), on a detected cuboid or not -
checked against the jog envelope (`Motion.refused`) and through
`JogPanel.run_job`; a click never lowers the tip. The Camera | Tip choice
is not shown (its code stays, `click_mode`, fixed to Camera, as Picking's
pickup clips switch); there is no Camera / Tip back to center (Picking
keeps Camera to dish center). A double click moves nothing. A **right** click (`CameraView.right_clicked`) marks a
**memo point** (`Session.memo_points`: tip XY and a Z or none, named with
the lowest free number M1, M2 ..., in memory until the program closes,
never in the profile), as does Point at crosshair
(the picture's center through the pixel map, as By clicking's aim,
no Z; gray without a pixel map, "Calibrate the camera first."). Under it
**Tip to crosshair** (#99) sends the tip to that spot at the travel height
(`Motion.to_spot`, through `JogPanel.run_job`) and leaves it there, no point
needed; gray without a tip or a pixel map or while the robot is busy,
checked against the jog envelope at the travel height. A point is
one kind - a place on the deck, no purpose stored (#66). One card, **Points**
(#96, #112), which never folds, is one list of rows of our own
(`widgets.point_list.PointList`, a fixed height, more rows scrolling; no
`QTableWidget`): the memo points then the **saved points**
(`Session.saved_points`: the profile's positions less
`tasks.SETUP_POSITIONS`, which are never listed or drawn as points, #61), a
thin line between the two kinds, a row being its star (`point_list.star_icon`:
filled in `warn`, an outline in gray, in the list and on the chips alike), name,
X, Y, Z ("travel" for no Z) and × (a memo point at once, a saved one asks
"Delete point <name>?"). ☆ asks a name, the point's own by default, and moves it into the profile
(`Session.save_memo_point`, #62, refused for a name in use or a setup
position's, `Session.point_name_refusal`; never gray: a point at the travel
height is stored at that height then, `Motion.travel_z` or else the jog
envelope's top); ★ asks "Remove <name> from saved points?", Keep primary, and
makes it a memo point with its name and Z (`Session.unkeep_position`, one
Undo step). `StarButton` and `PointButton` are Liquid's too, and only Manual
takes a star off. A double click edits X, Y, Z and a saved point's name
(`rename_saved_point`), in a box over the cell: Enter or leaving keeps it,
Esc puts it back and is no Stop. A click chooses a row (again: lets it go),
and the chosen row unfolds under the point **Camera to** (`Motion.camera_to`,
the point less the tip offset, its Z unused), **Tip to** (`Motion.to_spot`
to its Z, no higher than the travel height; gray while no tip is on, "Put
a tip on first.") and Z from tip, each checked against the jog envelope,
through `JogPanel.run_job`. A click chooses and unfolds at once; when that
folds a row above, the one clicked lifts and the pointer is on its actions, so
they take no click for two double click intervals, and a double click there is
the row's (the second press came down on Tip to). A click on the chosen row lets
it go after one interval: a double click keeps it. Under the list
Point at crosshair, Tip to crosshair and **New points get Z**, a Select of
Travel height, Z of the tip now and Last Z given (at start). **Undo** and **Redo** on the card's title row
(`theme.factory.tool_button`, as Liquid Plan's; no Ctrl+Z) go over the last 3
moves (`Session.undo_points`, `gui.point_history`: in memory, ended by loading
a profile). A move is any change of a point, wherever it was made, so Liquid's
right click is one too: added, deleted, X, Y or Z changed (Z from tip
included), renamed, starred or not; every change goes through `Session._points_moved`. A
move keeps every point as it was, and a saved point brought back is written to
the profile again, in its place. There is no Go to, no Click to move and no
Mark points; a double click only edits or renames, never moves. A point below
a deck module's top is flagged (`Session.memo_problem`, the jog
envelope's floors, `Motion.envelope`: the profile's modules over the deck
definition's slots, `core.deck`, in the robot's frame), red on the picture and in its Z. Both kinds drawn in cyan
on the "points" layer (`position_marks.memo_items`), a saved point with a
filled disc, the chosen one ringed; on Run the Points chip alone says whether
they are drawn, on at start (`CameraView.marks_default`) and never turned on by
a new point, which is chosen in the list. Both kinds are chips under "At a
point" too (`widgets.point_chips.PointChips`, a star and a name, three rows high,
more scrolling, only for choosing; the one flagged red as in the list). **In place** (#111, `ManualPage._in_place_card`, first of the
work column), always shown: Aspirate in place and Dispense in place, their
keys drawn as bound, A and D doing the same and nothing while a field has the
keyboard, with an amount and speed of their own (starting as Aspirate's fields,
then in the page's memory): where the tip is, no move and no lift whatever the
cards say. Dispense in place is gray while the tip is empty, and above what it
holds refused, "Only 5 µl in the tip.". A number on these cards is a
`theme.factory.number_field` under its `labeled` name and unit (#115, MANUAL.md
§2.7): no arrows, ↑/↓, the wheel only with the keyboard, which it takes by a click
only: the first the window finds would have it as the page opens. Aspirate (#115,
MANUAL.md §4.2) holds its settings in the open, no Change: From By clicking / At a
point / In a well, what that asks, Amount, Speed, the height (a point has its
own) and the lifting row, enabled once a source is chosen, the TipGauge its
answer. In a well is a `widgets.well_picker.WellPicker`: the mini deck, the
plate chosen on Well center first until one is clicked, the plate in real proportions, a
well chosen by a click, one always chosen while a plate is. By clicking (#60, By
hand before)
takes from under the camera's crosshair (the picture's center through
the pixel map, plus the measured tip offset); no spot is clicked: a
click moves the camera, with Snap to found cuboids onto a cuboid's
center (beside every cuboid, to the point). Its height is Z (with From tip, the
tip's Z now) | Above the bottom, the bottom that of the slot the spot is in
(#90, `ManualPage._slot_bottom`): a measured plate gives its measured bottom, the source dish's slot the dish bottom
when confirmed (`Session.is_confirmed("dish_bottom")`), anywhere else Above
is gray and Z is used; the default where there is one, the summary and the
field's tooltip naming it ("0.5 mm above the bottom of slot 5"). Over a plate in Above the bottom a
spot not in a well is refused, "The spot is not in a well.", nothing moved,
with Aspirate as soon as I click too (`well_center.in_a_well`: the
definition's outline at the measured center, less `TIP_RADIUS_MM`), and so
is a click aiming into a slot with no bottom; Z may be on a well's wall.
A target's Z (Tip to, At a point, By clicking's Z, Dispense's well height) more than 1 mm under the
bottom of its slot when measured, or 2 mm under a drawing's, is refused in a
red toast, nothing moved (#101, `manual.under_the_bottom`, its limits
constants; `ManualPage._heights_in`, what the height line reads, nothing
to check without one). It is on the move, not in `Session.memo_problem`,
and no part of the jog envelope: a jog step and Dish bottom's lowering go
down to a touch. Aspirate is enabled whenever a tip is on and the robot is
free: one job remembers the camera's pose, the tip to the spot at the
travel height (`Motion.tip_to`), down, aspirate, the camera back to that
pose; with Aspirate as soon as I click, a click aims and does it. In a well
the height is above the plate's measured bottom (rim less depth, reached as
the liquid engine reaches it, `Motion.to_measured_bottom`) and x and y are its
measured center once it is measured, else the drawing's. The
summary and the field's tooltip say which ("0.5 mm above the measured bottom").
Aspirate and Dispense each say how the tip leaves, on their own, in the page's
memory, on a row folded to its words, "After aspirating: lift right away"
(#102, #115, MANUAL.md §4.4, `manual._LiftRow`, `workflows.manual.retract`):
Right away (at the start), After a wait, Stay; Wait, s only with After a wait
(0.5-60 s, 5 at the start; "lift after 5 s"). The tip goes
straight up where it stands to the travel height (`Motion.rise`), and only
By clicking then brings the camera back; left down, the camera stays too. The
wait holds the lease, the buttons gray; Esc ends it, the tip down, "Stopped:
the tip stays down." in a toast. Lift slowly (#103, a Switch, off at the
start, hidden with Stay; "lift after 5 s, slowly"): after any wait
the tip goes straight up `motion.SLOW_RISE_MM` at `SLOW_RISE_MM_S` in one
force_direct move with `speed`, then up to the travel height; with less than
that way up it only goes there. Aspirate and Dispense are
`widgets.fold_card.ActionCard`s (#114, MANUAL.md §4.1, replacing #105): one open at
a time (`ManualPage.work_cards`, Aspirate at the start), either at any time, each
its number, title, a summary of the whole action cut to one line (the whole in
its tooltip) and its button in the header, working folded; open, its border is
its color. What the tip holds says only which button is primary: Aspirate while
it is empty, Dispense gray with "The tip is empty. Aspirate first." in warn-text,
then Dispense. A card opens by itself only where a job ends, never on
`in_tip_changed`: an Aspirate, by the button or a click, opens Dispense, a Dispense
that empties the tip opens Aspirate, and In place switches none. Finish by hand
opens Dispense. A card folding takes the keyboard off its fields, and the action
buttons take no focus: else it passes to the next field, and the robot's keys
with it. Dispense (MANUAL.md §4.3): In a well (a `WellPicker`, the plan's plate
first, any well choosable, the plan's wells in one color, no legend and nothing
of what a run filled, §10.10; from its well center, at a **well height**, #137:
Height Top | Middle | Bottom and a signed Offset, `core.liquid_plan.Height` said
by `height_words` under them and in the summary, "measured" with a measured
bottom; Bottom plus `picking.deposit_offset_z` at start, only read, then the
page's memory; a measured plate by its rim and depth, the numbers of
`well_center.plate_heights`, else the drawing; under the bottom refused as a
target's Z, above the travel height held there) / At a point, its Z said where
a well's height is, then, beside that sentence, **Tip to** (#138, "Tip to A7",
`ManualPage._to_target` as Dispense: the tip left there, nothing dispensed, no
lift, an empty tip too, gray with no tip on or the robot busy; with the tip
within `motion.SAME_SPOT_MM` of the target's X and Y, it and Dispense, a point's
Tip to and Tip to crosshair are one straight move, up or down, `Motion.to_spot`,
#139, and never Aspirate, whose plunger is made ready above the liquid, or a run),
then Part | All
(#99: Part chosen at start, its volume `picking.vol`), its own speed (#97, from
`picking.flow_rate` at start, in the page's memory) and the lifting row, its
button saying what it does ("Dispense into A7", "Dispense 10 µl"), gray and only
"Dispense" while the tip is empty. A job answers "Aspirated 10 µl.", "Dispensed
10 µl into A3." (at M1.), Look at "Camera moved to slot 4.", Detect cuboids "23
cuboids found.". What
is in the tip is the session's count (`Session.in_tip`,
`Session.aspirate`, `Session.dispense`: all or a part no larger than it;
another tip holds nothing). The last Z given is typed into a point or
Z from tip; no Z until one is given, so a first click never sends the tip
down. Over the
picture's top left is the `TipGauge` (`CameraView.add_top_left_widget`,
X, Y, Z under it), the tip's volume counted from the page's aspirates and
dispenses; the Past picks chip draws where it aspirated, the last bright
(`PositionMarks.set_picks`); Look at (a `MiniDeckDropdown`) sends the
camera over a slot. Run shows the upper camera only.

Under X, Y, Z on this page only, the one exception to nothing under them, is
the height line (#95, `ManualPage._heights_here`), for the slot the camera
is over (`camera_moves.camera_slot`: no pixel map or tip calibration needed),
following it: "Slot 8 · top 85.10 · bottom 67.77 · measured"
(`well_center.plate_heights`). Measured is a plate measured, what In a well and By clicking go by; any other
plate reads "drawing". A slot
with no plate has no line, its row kept (`set_height_line("")`) so what is
under it does not move. The source dish's slot says only "Slot 8 · bottom 66.00
· measured", once `dish_bottom` is confirmed; before a plate's drawing there,
as `_slot_bottom`. During a move the line stands; a message never goes there.

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

Park in the top bar (the head to the back right at the travel height,
`Motion.park`: a retract only if the tip is below it, then one move; never
a home, which would lose the tip calibration) is reachable from every
screen, without asking: it is refused while the robot's lease is
held, and goes through the shown page's `JogPanel.run_job` when that page
has one, else claims the lease for a worker of its own. Retract beside it
(#108, `MainWindow._retract`) takes the tip straight up to the travel height
(`Motion.rise`, staying where it is above it), by the same path and refusals,
"Retracting…" while it runs, nothing asked; its key (`retract` in `gui.keys`)
is R, window-wide, rebound in Settings or cleared there (`Action.clearable`), and a field with the keyboard
keeps it. Manual pick's Retract card (#136, `foot` of `WorkColumns`: under the
work column, outside its scroll, in both layouts) asks for this same Retract
(`Session.retract_requested`), gray when it is (`Session.not_free`), R drawn on
its button as bound. There is no Machine card: Home is in Settings, Advanced.
"The robot is busy" (`MainWindow._robot_busy`) is the robot's lease being
held, nothing else: a camera opening, a look for the marker or a picture
saved take no lease and busy nothing.

A popup a button opens is closed by that button (`controls.closed_by`, or
`setMenu`): `offscreen` replays no press, so only `windows` shows it reopening.

A section that folds (`theme.factory.Section`;
`flat` inside a card, Points' Memo and Saved)
has an arrow as tall as a capital letter (`fold_arrow`), and
folding lays its scroll column out at once (`_ScrollColumn.fit_now`), so
the cards around it do not flicker.

### Liquid is a plan of steps

Liquid's Plan step (`pages.liquid_plan`, LIQUID.md §3) builds a
`core.liquid_plan.LiquidPlan`: an ordered list of plan steps of six kinds, from
Add a step - Move liquid from → to, Mix, Blow out, Move the tip, Wait, Wait
for me - each a card read as a sentence, its id and color its own, its number
its place in the list. One card is open at a time, its rows the sections
(`sections`: Take from before Put into, then Amount and Extras); the left area
is what the section asks (the plate, the camera, or the fields of Amount,
Extras, How, Wait and Wait for me, `widgets.plan_forms`, in the middle of the
room under the title; the card has only rows and buttons), with no card open
the whole plan in each step's color. A Blow out made by Add a step has no place
yet (A well, nothing chosen; "Choose the place."), its stored default staying
Trash for the plans saved before. The place choice is on the title's row, centered in the left area,
moved right only to clear the title. After a change of section no field has the
focus (`LiquidPlanPage._leave_the_fields`): one clicked keeps it while typed in. A card moves by its handle
(`widgets.step_list`: never `QListWidget` with item widgets), Alt+↑ / ↓ or
its ⋯ menu; the widget only says `moveRequested`, the page applies
`move_step` as one undo entry and calls `set_steps`. A plate is drawn in its
real proportions (the SBS footprint, `PlateGrid`), the wells of other steps
dim. A step's place is wells, a saved point, a **spot** (coordinates kept in
the plan: By hand's Use this spot, or a memo point used there, one with no Z
at the travel height, so a plan never depends on what is in memory) or the
trash. The points are `Session.saved_points` and the memo points; a right
click adds a memo point and the star keeps it as a saved point, as on Manual
pick's Run. A jog panel still saves nothing (no Space, #62;
`jog_panel.UNBOUND`).

The plan is handed to the session as it is edited
(`Session.set_liquid_plan`): nothing confirms it. The step is done while
`Session.liquid_plan_problems` is empty, and until then Next is gray beside one
sentence (`next_reason`). The trash is set once per plan (`LiquidPlan.trash`, a
`Location`: a well of any labware or a saved point, so a memo point chosen
there is named and saved first; Use for future plans keeps it in the profile,
`Session.default_trash`). A run needs it; editing never does, nor does Try once
unless the step goes there. Try once (`run_plan(only=, first_pair=True)`, through
`JogPanel.run_job`) does the open step's first well and leaves the tip there: a
short job, not a run. Templates are as Auto Plan's
(`Outputs/templates/liquid-plans/`); a program from before plans is listed gray
("Made with an older version"), never opened or converted, and stays readable
by the engine and notebooks. The plan is saved as it is edited into
`outputs/liquid/current.json`; the next start keeps only its trash and what a
tip holds.

Each step but Wait and Wait for me compiles to a `core.liquid.Program`
(`compile_step`), and `workflows.liquid_plan.run_plan` runs them through the
engine (`workflows.liquid.run`), whose rules stay as they were: refill, auto
empty, the measured bottom, slow rise, pause / stop / resume. Its one addition
is `Group.empty_after`. A step with no wells rides on a stand-in plate that
never reaches the screen. Start blows the tip out over the trash before the
first step, automatically and only with nothing done (`PlanState.fresh`);
Continue does not. Washing is a two-step plan (measured bottom, an aside shift,
Lift slowly, auto empty, pause after the first well), not a page of its own.

Run (`pages.liquid`, LIQUID.md §6) runs that plan through a jog panel never
shown (`JogPanel.run_job`, lease `run=True`: STOP, the pill "Liquid · step 2 of
3"), held from Start to its end, the banner after a step included. No camera:
the step in hand from → to with its counter and bar, Taking from and Putting
into each a plate of the same size or a place's name. How to run is One step
at a time (after each step Finish here, Repeat step N, Go to step N+1;
`StepChoice`) or All steps in a row. The footer is as Picking's (`run_status`,
`footer_buttons`, `set_need`: READY / RUNNING / PAUSED / WAITING FOR YOU / DONE
/ NOT READY, the sentence in the plan's words, never the engine's
`LiquidState.status`): Back to plan and Start step 1 (Start for all in a row),
during a run Pause / Continue and Stop run, the plan locked; after a stop
Continue step 2 from well 8, Start the step over and Start from step 1. A run
to its end makes the step done (`confirm("liquid_run")`).

A plate's measured well
center (`WellCenter`) is one per slot and plate type, set on the Well
center step, one page in every task (`pages.well_center`, REDESIGN §6.7; #128;
`set_task` only tells it which plate to open on): the jog pad under the tip's
X Y Z, with its Keys, and beside them one intro line, a `MiniDeckDropdown` with every
plate on the deck lit and nothing else (`tasks.well_center_plates`), each tile
saying Measured or Not measured, one line for the selected plate ("Measured
today", "Not measured: using the drawing"), then Last time; last, so that nothing
stands under what folds, one card, no title, no chip, none with no plate, its
`PathSteps` path (#127): Tip to
a well (the plate's map, `WellPicker(plate_choice=False)`, the plate being the
selector's, Move the tip; done "Tip is over A1", the measurement's well, with Redo,
the one link and the way to another well: the map back, the measurement
forgotten, nothing moved), Center the tip (It is centered), Lower to the top of
the well (It is level), Well depth (from the drawing, Save, never done). A plate
opens on the first item, or on Center the tip while its measurement is in hand. The
column keeps the room the map leaves as it folds (`map_room`): the operator may
have scrolled down to Move the tip. A click on the selector chooses the plate
(`Session.chosen_plate`: for the robot session, gone when that plate is moved or
taken off, as its labware id tells; no need of any step). The page opens on it,
else in Auto pick on the plan's plate (`workflows.well_center.plan_plate`, the one
a deposit reads), else on the first on the deck; Save goes on to the next plate,
shown, not chosen. Manual pick's In a well opens on the chosen plate too, a click
there choosing another.

The step is optional in every task (`tasks.optional`: Next always enabled, the
task never opens on it; #130, #132), a plan no need of it, and done while there
are plates on the deck and every one is measured (`Session.well_center_status`),
measurements of earlier days included, so a new plate takes the mark away. A plate
not measured uses its drawing, a plate measured its measurement, in every task,
with nothing to confirm and nothing to skip (#133): no robot session has to see a
plate measured, and a plate moved since is left to the refusal under the bottom and
the height line. A liquid plan going to a measured bottom on a plate not measured
is that plan's problem to say. Manual pick's Run has no Well center card. A picking
deposit adds its x and y, Manual pick's wells and Liquid handling's steps all of it.
It is the plate's, wherever it
was measured, and every page reads it the same way:
`workflows.well_center` holds the measurement both pages take
(`Measurement`, `go_to_top`), the deposit's offset (`deposit_offset`) and
the plate plan's plate's center (`for_plan`). While a run goes into a plate
(its lease's `plates`), that plate's center is neither forgotten nor its
depth changed (`well_center.run_using`).
A measurement belongs to the height of the module in its slot when taken
(`WellCenter.module_height_mm`, 0 for none; a record from before has none
and is accepted): on another height `DeckConfig.well_center`, the one place
every reader asks, does not give it, so the plate reads "Not measured: using the
drawing" in every task and nothing uses it. A measured plate shows "Last time:
Z 66.40 mm · 3 days ago" (`well_center.measured_bottom`: the drawing's well top on
the module, the measured rim, less the depth), as information, in every task.

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
with cuboids". What a plate not measured uses, the labware definition, is its
**drawing** on screen. A changed run id is the robot restarted: labware, the tip and
what was confirmed are forgotten (`Session.robot_seen`). A session is shown by the name it was
given when created here (`config.robot_sessions`, default date, time and
profile), else by when the robot started it - never by its id. "run" on
screen means a picking or liquid run. The pipette
is set up under the hood and never mentioned ("pipette loaded" reads as "a
tip is on"). A `Routine` is a **plate plan**, "Plan" on screen (Auto
pick's Plan step, `pages.routine`: the plate from the MiniDeckDropdown,
the wells planned on the PlateGrid (`planning=True`: a click plans a
well and selects it, a click on a planned one selects it, Ctrl or Shift
adds to the selection or takes out of it, never out of the plan; only
Remove, Delete or Backspace takes the selected wells out of the plan),
the Cuboids field setting only the selected wells
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
poses are set with **Save position** (the shake position's too); a position
sheet's way back is **Go to saved position**, the Tip step's **Go to the
disc**, the Picking page's **Camera to dish center**.

### For biologists: stop, guard, say what to do

- **Stop** is Esc, window-wide, and the top bar's STOP while a run has
  the robot; no page binds Esc itself (two shortcuts on one key cancel out). First press:
  `Session.stop_requested`, every page stops at its next safe point, and a
  short job (a `JogPanel.run_job` that is not a run) ends after the command
  in progress, the tip where it is (`Session.stop_event`,
  `Session.stoppable`: the wrapper refuses that thread's next command,
  `JobStopped`, "Stopped: the tip stays where it is."). Second
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
So are the tip calibration's (`AppSettings.tip_cal_*`: the mode, Auto or By
hand, tip type, frames, verify, manual adjustment, the start offset dx, dy, the height the tip is
measured at, refused at the start below a deck module's top). The Tip step
(`pages.calibration_pipette`, TIP.md) is one column of two cards. The Tip card,
the same on the shake position's sheet, says the state at a glance: a drawn tip
(`TipIcon`, filled while one is on), the title ("Tip A2 is on", "No tip on", "Tip
state unknown."), under it the tip box and its slot, a Select with more than one
and plain text with one. With no tip on the rack (`PlateGrid(rack=True)`: the SBS
footprint in a dark box, round tips, each in the rack, used, a dashed ring,
`Session.used_tips`, or next, blue; a click makes a tip next; `RackLegend`
under it), Pick up A3 of a fixed width; with one on the rack folds away behind
Show rack / Hide rack, taking no choice while shown, and Drop in trash and Drop in
place (`dropTipInPlace`, nothing moved; no Put back: it did not work); no tip box,
a warning with Back to Deck.
Calibrate the tip is a `widgets.path_steps.PathSteps` path with a chip (Not done,
In progress, Done), dimmed with "Put a tip on first." until a tip is on, where it
stands read at each refresh from the tip, the run's phase and
`tip_calibration_status`, never from a visit: Camera over the disc (Go to the disc,
the camera to the calibration position), Disc in the picture (Yes, calibrate: the
disc confirmed, then at once the calibration), Find the tip (a bar that only
turns, the routine's lines only in the log; its result saved as it ends, the tip
left over the crosshair) - one run from Go to its end (lease `run=True`, STOP, the
pill). Two clicks once a tip is on. Stop at the disc ends it with nothing saved,
"Stopped. Nothing was saved.", the path back on Camera over the disc, or at its end
while an earlier calibration holds; during Find the tip the routine has no point
to stop at. What stops a start is said in the current item, a failure in a red
banner. The footer's sentence is the page's `footer_line` ("Tip calibrated."
green). Done, Find the tip reads Tip found with the link Redo, which goes back
through the disc (no Check button); then, with the manual adjustment on in
Settings, the path's last item, Adjust by hand · optional: **Adjust by hand**,
gray with "The tip is not over the crosshair." while it is not (leaving raises
the tip, Redo brings it back), opens Controls, the lower camera and the XY pad at
0.05 mm. Save gray until a step, a short job, not a run; Cancel saves nothing and
leaves the tip where it is; left with a step not saved, Save / Discard is asked.
By hand (`tip_cal_mode` "hand", #141; verify and the adjustment gray in Settings,
"Only for Auto."), for a tip the model does not find: Find the tip and Adjust by
hand are off the path. The run (`calibrate_pipette.start_by_hand`) finds the
crosshair with the upper camera, the tip aside reads it with the lower one (not
found: no homography, nothing else), and ends with the tip at the crosshair plus
the start offset; **Center the tip by hand** follows, outside the run: the lower
camera live, Controls open from 0.5 mm (`HAND_STEP_MM`), Save at once
(`save_by_hand`, `method` "manual"), Cancel saving nothing ("Stopped. Nothing was
saved."), the path back on the disc; left before either, Save / Discard is asked.
In either mode the picture stays live, the camera the routine reads with (the
upper one while it reads, the lower one from its first frame); each frame's boxes
(`viz.tip_views.view_items`, from the routine's `on_frame`) are drawn over it as
the model gives them and wiped when the gantry moves (`on_move`, a jog step), a
failed reading's kept until the next start. No frame is held, nothing else drawn.
The **Detections** chip (`CameraView.add_chip`), on at the start, takes them off.
A step counts while the tip is in line with the crosshair: raised straight up it
can still be saved, moved elsewhere it is dropped, its pose no offset. Controls
start folded on the step, the path's buttons moving the robot, and are gone
during a run. While the picture is the lower camera's, the page's own pad goes
through `TipTarget.axes` too (`JogPanel.set_view_axes`), so an arrow
moves the tip its way on that turned, mirrored picture. Leaving the
step raises a tip below the travel height (`Motion.raise_tip` through
the page's `JogPanel.run_job`), never while the robot's lease is held.
Liquid's Tip step shows no calibration (`set_task`). Its
cameras are the profile's upper and lower ones.
The Dish bottom step (`pages.dish_bottom`, REDESIGN §6.4) has no camera:
a big Z readout (read when shown and again whenever the robot is let
go: a raise begun as Tip was left ends after it is shown, #56) over
three cards side by side, Coarse, Finer, Finest
(`dish_bottom.STAGES`, 1, 0.5, 0.1 mm, no numbers), each with Up and
Down and its own step carousel (`jog_pad.StepCarousel`), active only
while it is the one lowering, through a jog panel that is never shown
(its keys on itself, so they never fire); the page's own keys from the
key actions (Z up / down, the lowering card's step sizes, H) while
lowering, no XY; Keys puts them on Up, Down and the step arrows as
keycaps (`jog_pad.KeycapButton`, the pad's look) and lists them, as
bound (#58). Move the tip (over the dish center) is
the first card's first button; It touches raises the tip by that card's
step and the next card lowers; the last card's is Save dish bottom (a
change over 3 mm asked first; the tip raised after); then "Dish bottom:
Z 66.42 mm" with Redo (back to Move the tip, the value kept until a new
one is saved). On the right "Last time: Z 66.40 mm · 3 days ago" with
Use it while it was ever measured, and "Over the dish · slot 9" with
Change (Robot setup's sheet over the task). A camera position's slot
(over the dish, calibration) is the camera's, the tip's pose plus the
measured offset, else Settings' start (`camera_moves.pose_slot`); the
shake position's is the tip's. Saved over the dish in another slot, the
shake position is forgotten (`Session.remember`): Picking's Shake the
dish is then Set shake position.
Done when measured (`Session.set_dish_bottom`) or Use it
(`Session.use_last_dish_bottom`) in this robot session, never by Next.
The Picking step (`pages.picking`, REDESIGN §6.5): the dish picture with
"N cuboids found" and Camera to dish center over its top right (during a
run only while paused or waiting, under the run's lease lent to the jog
panel, the tip empty; else "Pause to move the camera"); beside it, with
no panel scroll, Detect cuboids and Shake the dish ("Lights go off
while detecting.": the lights off for its frame, back as they were
after), then two cards side by side, neither folding
(`FoldCard(fold=False)`) - Cuboid sizes (the histogram whose window is
dragged and saved like the settings' Save, the pickup line, More
settings - a sheet over the page, `PickingSettingsSheet`, "Detect
again." after a change - All measures: `widgets.cuboid_stats`) and
Plate · slot N (mini `PlateGrid`: the planned wells, Filled / Planned
once something is filled, the current well ringed during a run) - under
them Liquid in tip (`widgets.tip_liquid.TipLiquidBox`, a card folded to
its title and "20 µl in the tip" / "Tip empty"): the picking settings'
`tip_liquid_vol` (20 µl) at their flow rate, from the dish center
(`dish_bottom.dish_center`, `tip_liquid_above_bottom`, 1.5 mm, over the
dish bottom) or a well chosen on the mini deck and its mini `PlateGrid`
(its bottom as the robot has it, x and y at the measured well center),
then the tip raised: a cushion the run never dispenses, since it gives
back only `vol` per cuboid. Dispense gives all of it back there. Change
opens the volume and height with Save (Enter) and Cancel, nothing saved
before. During a run only while paused or waiting with the tip empty of
a cuboid, under the run's lease lent to the jog panel (`RunHold`), as
Camera to dish center - then the jog panel (its pad over the picture, folded). No Save pickup clips
switch: its code stays, never shown and off. At a run's end a large card over
the middle of the page: Picking done (Picking stopped after a stop or a
failure), "N cuboids in M wells", Choose plan (primary: Plan with the
same plan on screen, not in use, `Session.plan_chosen`, so Use this plan
makes it again with nothing filled) and Close; with wells of the plan
left short, "N wells were not filled" and Finish by hand instead of
Choose plan (`Session.task_requested`: Manual pick's Run, Dispense on In
a well and the first of them). While the plan is done Start picking is
not offered. The positions are Robot setup's.
The footer is the run's status: the page's `run_status` and
`footer_buttons`, read by the shell for any step page that has them -
READY / PICKING / PAUSED / WAITING FOR YOU / DONE / NOT READY and one
sentence (`PickingSession.status` while it runs) and Back; Start picking
is centered in the side panel under the cards, Pause / Resume (yellow
while held) + Stop run in its place during a run. The shell passes the
step's need down (`set_need`): Start waits for it. While a run's page
says "waiting" the window flashes in the taskbar (`QApplication.alert`,
`MainWindow._show_run`). Settings
keeps Save and Revert at its bottom right, outside the scrolling columns.

A key line on the pad, like an item of the mouse row, is "keys   what it
does", split at its first three spaces.

The keys that move the robot (and Manual pick's aspirate, dispense) are
`gui.keys` actions with today's keys as defaults, rebound on the Settings
page's Keys card (`AppSettings.keys`, only the changed ones stored). The
jog panel and Manual pick's Run bind from it, rebind on save, and the pad
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
