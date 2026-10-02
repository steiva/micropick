# Changelog

What changed in each version of micropick, newest first. The version is
in the window's title; a report (Log page, *Save report for help*) carries
it too.

Versions follow `MAJOR.MINOR.PATCH`: the first number changes when a
profile or a way of working has to change, the second for new features,
the third for fixes.

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
