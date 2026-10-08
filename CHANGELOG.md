# Changelog

What changed in each version of micropick, newest first. The version is
in the window's title; a report (Log page, *Save report for help*) carries
it too.

Versions follow `MAJOR.MINOR.PATCH`: the first number changes when a
profile or a way of working has to change, the second for new features,
the third for fixes.

## Unreleased

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
- Profile page: Robot and Load profile, side by side and centred. The
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
- Picking: deposits use the well centre measured on the Liquid handling
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
  centre, jog down until it touches, Set dish bottom here.
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
