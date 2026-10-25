# Changelog

What changed in each version of micropick, newest first. The version is
in the window's title; a report (Log page, *Save report for help*) carries
it too.

Versions follow `MAJOR.MINOR.PATCH`: the first number changes when a
profile or a way of working has to change, the second for new features,
the third for fixes.

## Unreleased

- The tip is no longer retracted before a move when it is already at the
  travel height: the jog panel's Go to, Tip calibration's Go to saved
  position, the Picking page's moves (picking position, shake position,
  dish center) and the picking run (a travel from the observation pose)
  now go straight there, as Manual control did. From below the travel
  height, the dish say, the tip still goes up first.
- One job at a time on the robot, across all pages. While a picking,
  liquid or calibration run goes on - paused or waiting for you included -
  nothing else moves the robot: the jog keys and moving buttons of every
  page, Robot & Deck's labware and tip buttons, Home and the tip button
  are greyed out, and their tooltip says what has the robot. Before, a
  jog on Manual control or a tip pick-up on Robot & Deck could go into a
  run's moves. Disconnect and Start new robot session wait for the robot
  too, and closing the window during a run asks first.
- A plate's measured well center can no longer be forgotten, nor its well
  depth changed, while a run goes into that plate: Forget well center and
  the depth are greyed out until the run ends, the run named in the
  tooltip. They no longer wait for other robot work, which they do not
  use.
- Profiles: deck.json now saves the measured well centers as
  `well_centers` and calibration.json the homography's held-out error as
  `center_error_px`. Profiles from 0.4.0 load as they are (the old
  "centre" keys are read) and are written with the new keys at the next
  save, so a profile saved by this version does not load in 0.4.0.
- Internal: Manual control and Liquid handling measure the well center
  with the same module (`workflows.well_center`), which the picking run's
  deposits use too.
- Internal: every move across the deck goes through one module
  (`workflows.motion`), which keeps the travel height and the top of Z
  for every page and run.

## 0.4.0 - 2026-10-19

Memo points and the well center on Manual control, a trash and a deck map
for liquid runs, a second Stop that keeps the robot session, and the
models on the GPU. Profiles from 0.3.0 load as they are.

- Manual control: Go to well goes to the plate's measured well center
  (it used the labware definition's before), and the Well card measures
  it - Go to well top, jog, Set well center - the same measurement as
  Liquid handling's.
- Manual control: memo points. With Mark points ticked, a click on the
  picture marks a point (M1, M2 ...) kept until the program closes and
  listed in the Memo points card, where its X, Y and Z are edited; the
  tip goes there with Go to. A point below the top of a deck module is
  shown in red with the reason. Moving from the picture is now a double
  click.
- A second press of Stop holds the robot instead of ending the robot
  session: the command in progress finishes, nothing else moves, and the
  labware and the tip are kept.
- Liquid handling: Start first blows the tip out over the program's trash
  (a well or a saved point); a Deck card shows where the run goes; the
  plate map is larger and opens in its own window. A new start keeps only
  the trash, not the last day's groups.
- Picking: the size histogram is a card over the picture; drag the size
  window on it to change and save it.
- The machine learning models run on the GPU when there is one (Settings >
  Inference: Auto, CPU, GPU), and faster on the CPU too.
- Faster runs: a full dish's contours are no longer repainted every
  100 ms, pickup clips are written while recorded (they held gigabytes of
  memory), and a paused run no longer makes the window crawl.
- A camera can be closed from its window; unticking Save pickup clips
  closes the lower camera.
- Fixes: a jog step right after Retract moves; a move from the retracted
  height is no longer refused for its Z; a number field that has the
  keyboard says so, and Enter gives the keys back to the robot; Detect
  cuboids on a frame with none; the plate map's row and column labels.
- American spelling throughout the interface (center, color, analyze).

## 0.3.0 - 2026-10-12

Picking as numbered steps with a cuboid statistics window, keys set in
Settings, and a Z jog limit that follows the tip. Profiles from 0.2.0 load
as they are; a tip calibration made before this version shows as to do
until it is made again.

- Calibrate the tip counts as done only while the tip it measured is on,
  in the robot session it was measured in: a new tip, no tip or a
  restarted robot means calibrating again.
- The Z jog limit is the height the tip reaches on Retract, never above
  (above it the pipette stops answering until homed): 200.6 mm with no
  tip, less the tip's length as the robot reports it (wide bore 200 µl:
  159.4 for jog steps). The position shown is read again when the tip
  changes and when a page is opened.
- Keys for moving the robot, and Manual control's aspirate and dispense,
  can be changed on the Settings page (Keys); the defaults are as before.
- Picking is numbered steps leading to Start: the Operation checklist
  left on other pages, the picking position, the shake position, the dish
  bottom, analysis, Start picking; Move and Positions at the end. The
  lower camera for pickup clips opens by itself while the box is ticked.
- The Sizes card is replaced by a window opened from the chart button on
  the size histogram over the picture: counts, rejections by reason, a
  histogram per measure (diameter, circularity, aspect ratio, solidity,
  radial CV, nearest neighbour, confidence, distance from the dish center)
  against its picking setting, and a table of medians and ranges.
- The next-step hint sits at the top center and can be dragged anywhere.
- Fold arrows are smaller, and opening a section no longer makes the
  other cards flicker.
- Shorter, plainer texts throughout the interface.

## 0.2.0 - 2026-10-08

A reworked interface: an Operation checklist that says what to do next,
both calibrations as numbered steps, the cameras and the models chosen in
Settings, and the tip handled from the status bar. Profiles from 0.1.0 load
as they are.

- A question mark in the status bar brings the next-step hint back after
  its cross closed it, on any page.
- Camera calibration is numbered steps too: place the calibration marker
  on the picking plane (ticks itself when the camera sees it), center it
  under the crosshair (ticks itself; says how far off it is), sweep, save
  the result. The camera chooser and the long marker description are gone
  (the description is the line's tooltip).
- A hint over the top right of the pages: the next step of the Operation
  checklist, with a link to where it is done. The cross hides it until the
  next step.
- Settings: Save and Revert are always in view, at the bottom right.
- Tip button: its panel no longer lights up under the mouse, a second click
  on the button closes it, and Pick up is no longer greyed while the page
  merely watches a camera.
- Tip calibration is a list of numbered steps, each with a mark: place
  the calibration disc on the module, set the disc position (Set position,
  Go to saved position, the jog panel right under it), put a tip on,
  calibrate, and - while the robot waits - nudge the tip onto the
  crosshair. The camera choosers are gone (the cameras are chosen in
  Settings); tip type, frames, verification and the manual adjustment
  moved to a "Tip calibration" card in Settings.
- The keys box on the camera pictures has a "Keyboard shortcuts" title,
  and the keys are in blue.
- Status bar: the tip is a button. It opens a small panel over it to drop
  the tip in place or in the trash, or pick up a new one from a rack's
  well (the next well of the rack is offered after each pick-up). Text in
  the status bar no longer lights up under the mouse; only buttons do.
- Camera calibration: the sweep parameters (marker side, dictionary, grid,
  degree) moved to Settings, where they are saved; the Sweep card says
  which ones it uses, with a link there.
- Profile page: Robot and Load profile, side by side and centered. The
  chooser shows each profile's folder after its name; a green check says
  which profile is loaded.
- Robot: Connect carries on with the robot's session, or starts a new one
  (after asking: the robot homes) when there is none to carry on with.
  Then two buttons only: "Disconnect robot session" and "Start new robot
  session".
- Profile page: the Operation checklist - the steps before a picking run,
  numbered, in the order of the work. The steps never change their words,
  only their marks; a step that waits for an earlier one is greyed, and
  hovering shows details. The Picking page keeps its own steps (detector,
  camera, clips) under "Complete the Operation checklist"; Start is blocked
  as before.
- Calibration is two tabs: Tip calibration among the steps, Camera
  calibration on the right with the tools used when needed (Liquid
  handling, Manual control, Log), whose titles are in the accent colour.
- The Settings gear is at the end of the tab row, larger; Stop is at the
  right-hand end of the status bar.
- Operation checklist: Connect the robot first, then Load a profile; the
  tip goes on before the calibrations, and the plate plan comes straight
  after them. Every step links to its page, done or not.
- Robot & Deck: the Slot card is first, with one "Load into slot N" for
  labware and deck modules alike, and what the slot holds listed a line
  each with its own Remove. "Labware definitions" and "Deck modules" are
  one card, modules under a rule and folded once the profile has them.
- Settings: a profile started empty can be given its cameras - choosing a
  camera for a role that has none adds it with the bench's settings.
- Settings: a second column for the loaded profile - which attached camera
  is the upper (overview) and which the lower (underview) one, with the
  attached cameras and their modes listed; the machine learning models; the
  calibration summary. All saved in the profile at once.
- Camera window (the camera buttons in the status bar, which now show and
  hide it): focus slider, a focus typed as a number, "default (960)" and
  "save to profile"; a "focus" box hides them. The pages no longer carry
  the focus slider.

- A Windows installer (`micropick-<version>-win64-setup.exe`): installs for
  the current user without an administrator, adds a Start menu and desktop
  shortcut and an uninstaller; a newer installer updates in place. Profiles,
  settings and logs stay in Documents\micropick.
- Picking: Shake the dish works while a run is paused or waits for you;
  the run does it itself, and with cuboids in the tip only once they are
  delivered.
- Picking (experimental, on by default): a cuboid a pickup missed is tried
  once more straight away, 0.3 mm over the dish bottom at 100 µl/s; missed
  again it is marked stuck (orange on the picture) and left alone until the
  dish is shaken. Settings: Run, "Retry a missed cuboid".
- Picking: deposits use the well center measured on the Liquid handling
  page for that plate (x and y, added to the well offset).
- Picking settings: the arrows step by what each number is (0.1 mm for
  heights and offsets, and so on).
- Picking: the dish bottom's two buttons are one above the other, so their
  names fit.

## 0.1.0 - 2026-10-02

The first versioned release, and the first Windows build.

**Picking**
- Analyse the dish over the live picture, with a size histogram and the
  size window it is judged by.
- A checklist before a run: every condition marked done, to do or worth a
  look, with a button to the page where it is done.
- Dish bottom (Z) calibration on the Picking page: tip over the dish
  center, jog down until it touches, Set dish bottom here.
- Shake the dish on demand.
- On a partial miss the caught cuboids go to the well first and the
  missed ones' volume back to the dish after.
- Optional pickup clips from the lower camera, in their own camera mode
  (Settings).

**Liquid handling**
- Programs of steps over groups of wells: aspirate, dispense, mix, blow
  out, moves, waits and pauses; refill and auto empty; a measured well
  bottom; slow lift.
- Leaving a well straight up; the plunger reset after a blow out done at
  the top of the well.

**Robot and deck**
- Robot sessions are named (date, time and profile) instead of shown by id;
  the robot's clock is set from the computer's at connect.
- A deck module's offset is attached at every load and checked.

**Safety**
- A red Stop on every page (and Esc): first press stops at the next safe
  point, a second stops the robot at once and says what to do next.
- Manual control cannot drive the tip into a deck module.
- Picking settings in plain words, with sensible ranges, the risky ones in
  amber, a list of changes to confirm, and the previous settings one click
  away.

**Help when something goes wrong**
- The log is kept in a file per day; *Save report for help* zips it with
  the profile and settings.
- Errors say what to do; a robot that does not answer says what to check.
- An activity line in the status bar while anything slow runs.
- `micropick.exe --self-test` checks an installation without a robot:
  the detector models, labware and video, result in the log file.
