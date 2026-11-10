# Tip screen — redesign specification

Status: agreed design, ready to build. Date: 2026-11-05.
Audience: Claude Code (implementation) and Ivan (review).

**This replaces §6.2 (Tip) of `REDESIGN.md`.**

Part of it is shared and applies beyond this screen (§2):

- how a camera screen is laid out;
- a numberless "path" component that replaces numbered step cards;
- how tip racks are drawn;
- color rules.

When you start, edit `REDESIGN.md` §6.2 to say "Replaced by `TIP.md`". Under
§3 (StepCard / CameraView layout), add "Changed by `TIP.md` §2".

`MANUAL.md` §2 (camera chrome, Controls overlay, Retract, step readout)
already applies here. Do not re-do it; reuse it.

---

## 0. How to use this document

**Sources of truth, in order:**

1. **This document.**
2. **The prototype** `docs/redesign/prototype/TipScreen.dc.html`. It is
   canvas HTML that does not run here.
   - It is drawn at 1920×1080, 100 % scaling.
   - The `scenario` tweak lists the states: No tip yet, Tip on, Is the disc
     there?, Finding the tip, Calibrated, Adjusting by hand.
   - Where the prototype and this document disagree, this document wins.
3. `MANUAL.md` §2, `REDESIGN.md`, `CLAUDE.md`.

**Why this rework exists.** Problems on the 0.4.0 Tip screen:

- **The cards are too wide.** The right panel is about 800 px wide. Every
  button is a thin bar the full width of the card, and the rack sits in a
  corner of empty space.
- **The colors are loud and mixed:**
  - a saturated teal grid of square "tips";
  - red ✕ marks for steps that simply are not done yet;
  - washed-out disabled bars that read as broken.
- **The numbered steps ("1. Start", "2. Disc…", "3. Calibrate") annoy
  users.** They show four boxes of equal weight when only one thing can be
  done at a time.
- **There are more steps than needed.** "The disc is there" and "Calibrate"
  are two clicks for one decision.

**Ground rules.**

- This is UI work. `calibration_pipette` logic, the tip calibration routine,
  `AppSettings.tip_cal_*`, `readiness.tip_calibration_status` and the
  tip-button actions (`widgets.tip_button`, `MainWindow._tip_command`) keep
  their behavior.
- Only the wiring of buttons to those calls changes (§4).

---

## 1. Layout (1920×1080)

```
┌ TopBar ─ ⌂ Home › Auto pick ..... [● Calibrating the tip] [■ Stop]  Light Lock Park Retract | ⚙ ┐
├ StepBar ─ ✓Deck ─ (2) Tip ─ 3 Dish bottom ─ 4 Plan ─ 5 Well center ─ 6 Run ───────────────────────┤
│ ┌──── camera picture, 4:3, full body height ────┐      ┌──── column 440 ────┐                    │
│ │ [XYZ · STEP]           [Crosshair][Points][📷] │      │ Tip A2 is on       │                    │
│ │                                               │      │ (rack, when shown) │                    │
│ │                                               │      │ Calibrate the tip  │                    │
│ │ [mouse help]            [Controls, when tuning]│     │  ● path …          │                    │
│ └───────────────────────────────────────────────┘      └────────────────────┘                    │
├ Footer ─ "Calibrate the tip to go on." ..................................... Back  [Next →] ──────┤
```

- **The picture is never stretched.**
  - Its box is 4:3 whatever it shows, so a switch of cameras resizes nothing
    (#50); another shape is letterboxed inside it. It takes the full body
    height where the width allows. At 1080 that is about 1141 × 856.
  - Its width follows from its height, as `FeedRow` does today.
  - Nothing is cropped to fill a box and nothing is scaled unevenly.
- **The panel is one column, 440 px wide, centered** horizontally in the
  width the picture leaves.
  - It does not grow to fill that width. Empty space on both sides is
    intended.
  - It scrolls vertically only if it must.
  - It is never narrower than 440 px: with less free width the picture gives
    way, 4:3 still.
- In the Liquid flow (no calibration, `REDESIGN.md` §4), the column holds
  only the Tip card.

---

## 2. Shared changes

### 2.1 Camera screen layout (all screens with a picture and a panel)

This updates the `FeedRow` rule in `CLAUDE.md`, "Camera view chrome": "the
picture gets the width its frame shape needs, the panel the rest, within 1–2×
its designed width". The new rule is:

- The picture's box is 4:3 at the full body height (minus padding) where the
  width allows, the same on every screen. It is never stretched or cropped
  to fit.
- The panel area is the rest of the width. Inside it, the content has its
  **designed width** and is **centered**:
  - **one column of 440 px** on Tip, Camera calibration and the position
    sheets (Dish bottom and Well center have no picture);
  - **two columns of 400 px** with 16 px between them on Manual pick
    (`MANUAL.md` §1). At 1920×1080 these leave the picture about 1048 × 786:
    the columns keep their width and the picture, 4:3, takes the rest.
- Cards never stretch to fill the free width.
- **Picking keeps the old rule** (the panel takes the rest, within 1–2× its
  designed width) until it is redesigned: its two cards side by side do not
  fit 440 px. **Liquid's Plan and the camera sheet are left as they are** for
  now.

### 2.2 Path: steps without numbers (new component)

`widgets/path_steps.py: PathSteps`. It replaces the numbered `StepCard` list
for a sequence that the user goes through once, in order.

- **A vertical line of nodes**, one per item, joined by a 2 px line. Order
  is shown by position, never by a number.
- **Three states for each item:**

| State | Node (22 px) | Title | Body |
|---|---|---|---|
| done | `ok-bg` fill, green ✓ (12 px), 1.5 px `#1F3A2B` border | the **result**, 15 px weight 500, `text` ("Camera is over the disc", "Tip found") | none, folded to one line; optional link on the right ("Go again", "Calibrate again") |
| current | `selected-bg` fill, 2 px `accent` ring, 8 px `accent` dot | the **task**, 15 px weight 600 ("Disc in the picture") | one sentence (14 px, `text-2`, line height 1.5), then one primary button (or a progress bar) |
| later | empty, 2 px `#3A4049` ring | the task, `muted` (`#6F7781`) | none |

- **The line color:** done segments are `#2E5A44`, the rest `#2A2E35`. The
  last item has no line below it.
- **Spacing:** 22 px below the current item, 14 px below the others.
- **Only the current item has a body.** A done item is never a big card.
- **No red marks.** "Not done yet" is the *later* state. Red is only for
  errors, as a banner with a sentence, and for Stop.
- **API:**
  - `PathSteps(items)`, where each item is
    `PathItem(key, task_title, done_title, body_widget, link_text=None)`;
  - `set_current(key)` marks the items before it done and those after it
    later;
  - `set_all_done()`;
  - a signal `link_clicked(key)`.

  The page owns the bodies, so the page logic stays in the page.
- **Where it is used now:** Tip only. Camera calibration, Dish bottom, Well
  center and Picking still use `StepCard` with numbers. Converting them is a
  follow-up (§7, Q1). Build the component so they can switch without changes
  to it.

### 2.3 Racks and plates are drawn the same way

This is the PlateGrid look from `LIQUID.md` R5, applied to tip racks:

- The **real SBS proportions** (127.76 × 85.48), drawn in a `#111317` box
  with a 1 px `#2E333B` border, radius 12, padding 5 %.
- **Circular wells**, never squares.
- **Tip rack colors:**
  - **in the rack:** `#4A515B` fill;
  - **used:** no fill, 1.5 px dashed `#4A515B` ring;
  - **next** (the one Pick up takes): `accent` fill with a 2 px `text` ring.
    Click another tip in the rack to make it next.
- A one-line legend under the rack (12 px `muted`): ● in the rack · ○ used ·
  ● next.
- The old teal fill is removed from a rack's wells. The `tips` color stays
  for what it marks elsewhere (deck tiles, Liquid's Take from).

### 2.4 Color rules (all screens; add to `REDESIGN.md` §1)

- **One accent** (`accent`, blue) marks what to do now: the current path
  item, the one primary button, the next tip.
- **Green** (`ok`) only means *done*.
- **Warn** (yellow) is for "waiting for you" and for problems the user can
  fix.
- **Red** only for Stop, danger buttons and error banners.
- **Disabled buttons:** they look like the normal button at opacity 0.4,
  never a separate grey fill (in the style sheet as fixed colors, which it
  has no opacity for). Prefer not showing a button that cannot be used yet
  (the path does that by design).

---

## 3. The Tip screen

### 3.1 Tip card

- **Header row:**
  - a drawn tip icon: filled `accent` when a tip is on, outline `muted` when
    none;
  - the title (17 px, weight 600): **"Tip A2 is on"** or **"No tip on"**;
  - under it, the tip box in 13 px `text-2`: "VWR 96 Tip Rack 200 µL XL ·
    slot 10". With more than one tip box on the deck, this line is a Select
    (chevron); with one, it is plain text;
  - on the right, only when a tip is on: the link **Show rack** / **Hide
    rack**.
- **The rack (§2.3):** shown when no tip is on (always), or when a tip is on
  and Show rack was clicked.
- **Buttons, under the rack:**
  - no tip on: **Pick up A3** (primary, right-aligned, 40 px). It has a fixed
    width, so "Pick up F12" does not change it;
  - tip on: **Drop in trash** and **Drop in place** (36 px, outlined,
    left-aligned). They are disabled (0.4) while the robot is busy, a run
    waiting at the disc included. Drop in place lets go where the pipette
    is: it is no Put back, which did not work.
- **No tip box on the deck:** the warn banner "There is no tip box on the
  deck. Add it on the Deck step." with **Back to Deck**. This is unchanged
  from `REDESIGN.md`.
- Toasts: "Tip A3 is on." and "Tip dropped. Calibration needs a new one."

### 3.2 Calibrate card (Auto and Manual flows)

- **Header:**
  - **Calibrate the tip** (17 px, weight 600);
  - on the right, a status chip, 26 px: **Not done** (`#22262C` on
    `text-2`), **In progress** (`selected-bg` on `accent-hover`), or **Done**
    (`ok-bg` on `ok`).
- **With no tip on:** the card is at opacity 0.55, with the line "Put a tip
  on first." and the path in its *later* state. Nothing in it is clickable.
- **The path (§2.2):**

| Item | Task title (current) | Done title | Body when current |
|---|---|---|---|
| go | Camera over the disc | Camera is over the disc | "Put the crosshair disc on the calibration module. The camera then goes to it." · **Go to the disc** |
| check | Disc in the picture | Disc is in the picture | "Is the disc with the crosshair in the picture? If not, put it on the calibration module now." · **Yes, calibrate** |
| find | Find the tip | Tip found | "The robot finds the tip under the camera by itself." · progress bar (6 px, `accent`) · "About 14 s left" (mono 13 px `muted`) |
| hand | Center the tip by hand | Tip centered | By hand only (#141): "Move the tip onto the central crosshair with Controls on the picture, then save." · **Save** · **Cancel** |
| fine | Adjust by hand · optional | (stays as is) | see below |

- **The one link:** *find*, once done, gets **Redo**: the old Redo
  calibration, the camera to the disc and the current item *check*. The
  routine finds the crosshair with the upper camera, so it cannot run *find*
  alone. While a run waits on *check* there is no link: nothing can have
  moved the camera, and Stop ends it.
- **The fine item, after a successful find,** is the current one, with the
  manual adjustment on in Settings. With it off there is no fine item and
  the path ends on *find* (`set_all_done`).
  - **Offered:** "Optional. If the tip is not on the central crosshair, move
    it there by hand." with **Adjust by hand**, gray with "The tip is not
    over the crosshair." while it is not, as after coming back to the step:
    leaving raises the tip, and Redo brings it back. There is no Check
    button (removed in 0.4.0).
  - **Adjusting:** "Move the tip onto the central crosshair with Controls on
    the picture, then save." with **Save** (primary, disabled until the tip
    has moved) and **Cancel**.
- **By hand** (Settings' tip calibration Mode, #141), for a tip the model does
  not find: *find* and *fine* are off the path and *hand* is on it. The run
  finds the crosshair with the upper camera, puts the tip aside and reads the
  crosshair with the lower one (a disc not found there costs only the
  homography), then leaves the tip at the crosshair plus the start offset and
  ends. *hand* is then current: Controls open over the live lower camera,
  XY only, from 0.5 mm; **Save** at once (the offset where the tip is, method
  "manual"); **Cancel** saves nothing ("Stopped. Nothing was saved."), the
  path back on *go*. Left before either, Save / Discard is asked. Done, *hand*
  gets **Redo**, as *find* does.

### 3.3 The picture

- The chrome follows `MANUAL.md` §2.2:
  - the status plate (X/Y/Z, STEP);
  - the layer toggles *Crosshair* and *Points*, and the camera button;
  - the mouse help ("Wheel zoom · Middle-drag pan · Double-click fit").
- **Controls** (`MANUAL.md` §2.3) are **folded** on this screen, the keys
  bound as ever: the robot is moved by the path's buttons, and the jog stays
  for when it is wanted. **Adjust by hand** opens them, XY only, at
  0.05 mm. They are hidden while a run has the robot, as today.
- **The detections (#141):** the picture stays live, the camera the run
  reads with: the upper one while it reads, the lower one from its first
  frame. Each frame's boxes (class and confidence; a tip in red, the one a
  frame kept in orange) are drawn over it as the model gives them, and wiped
  when the gantry moves; a failed reading's stay until the next start, under
  the red banner. No frame is held. The **Detections** chip, on at the start,
  turns them off. Nothing else is drawn; the path and the StatusPill say
  where the run is.

### 3.4 TopBar and footer

- **TopBar.** As implemented in 0.4.0:
  - while a calibration waits for the user, the StatusPill reads "Waiting for
    you" (warn) and **Stop** is shown;
  - while the tip is being found, it reads "Calibrating the tip" (accent);
  - Lock, Park and Retract are dimmed while the robot moves.
- **Footer:**
  - on the left, one sentence: "Put a tip on, then calibrate it." /
    "Calibrate the tip to go on." / "Tip calibrated." (`ok`);
  - on the right, **Back** and **Next**. Next is disabled (0.4) until the
    calibration is done. In Liquid, Next is enabled once a tip is on.

---

## 4. Wiring to existing logic (no behavior changes)

| UI | Calls (existing) | Notes |
|---|---|---|
| Pick up A3 | the tip button's "pick up from a rack's well" | through `MainWindow._tip_command` rules |
| Drop in trash / Drop in place | the tip button's drop in trash / drop in place | the same |
| Go to the disc, Redo | the old **Start tip calibration** / **Redo calibration** (camera to the calibration position) | the current item becomes *check* when the move finishes |
| Yes, calibrate | the old **The disc is there** confirmation, **then at once** the old **Calibrate** | one click, two existing calls in sequence. If the confirmation is refused, stay on *check* and show the reason |
| find progress | none: the routine reports no progress | a bar that only turns, no time left |
| Stop (TopBar) on *check* | the existing stop path (`Session.stop_requested`) | the run ends, nothing saved; the path goes back to *go*, or to its end while an earlier calibration holds; toast "Stopped. Nothing was saved." |
| Stop (TopBar) during find | the same, unchanged | the routine has no point to stop at: a first Stop changes nothing and it ends and saves; a second holds the robot, the run fails with nothing saved, and the path goes back as above |
| Adjust by hand / Save / Cancel | the existing fine-tune, its Save, and its Discard | the routine never waits for it (`manual_touch_up=None`); Cancel leaves the tip where it is and saves nothing |
| status chip, footer, Next | `readiness.tip_calibration_status` | Done only while the measured tip is on, in its robot session (`CLAUDE.md`) |

- **Dropping or changing the tip** makes the calibration not done, as the
  readiness rule already says. The path resets to *go* and the chip reads
  Not done.
- **Coming back to the screen** with a valid calibration shows the path at
  its end (*fine* offered), chip Done, and Next enabled.

---

## 5. Files and commits

**Files:**

- `gui/widgets/path_steps.py` (new);
- the rack drawing (the PlateGrid widget, or `plate_view.py`);
- `gui/pages/calibration_pipette.py`, or the redesign's Tip page;
- the camera screen layout (`widgets/feed_row.py`);
- tests;
- `docs/GUI_BENCH_CHECKS.md`.

**Commits:**

1. `PathSteps` with its own tests;
2. the rack drawing (§2.3);
3. the camera layout rule (§2.1) on all camera screens;
4. the Tip screen (§3, §4);
5. docs, including the `CLAUDE.md` edits in §6.

Tests stay green after each commit.

**Automated checks (pytest, `--mock`, pytest-qt):**

1. `PathSteps.set_current("find")` gives go and check done, find current and
   fine later. `set_all_done()` leaves no current item. The widget never
   shows a digit in any title.
2. With no tip on, the Calibrate card's buttons are not clickable, and
   Pick up names the next tip.
3. **Yes, calibrate** calls the disc confirmation and then the calibration,
   in that order, once each.
4. Stop on *check* ends the run and puts the path back on *go*, and nothing
   is saved. A first Stop during find changes nothing.
5. Dropping the tip after a calibration resets the path and disables Next.
6. The picture's box stays 4:3 when the window is resized: the ratio of its
   width to its height stays within 1 %.
7. The panel column is 440 px wide and centered at window widths of 1920 and
   1600.

**Bench checks** (add a "Tip" section):

1. At 1920×1080 the picture is 4:3, undistorted (the disc is round), and the
   column is centered in the space beside it.
2. A full calibration takes **two clicks**, *Go to the disc* and *Yes,
   calibrate*, after a tip is on.
3. No numbers on the path. No red mark anywhere unless something failed.
4. The rack shows round tips: used ones dashed, the next one blue.

---

## 6. `CLAUDE.md` edits

- **"Camera view chrome", the `FeedRow` paragraph:** replace "the panel the
  rest, within 1-2x its designed width" with "the panel's content keeps its
  designed width (one 440 px column, or two 400 px columns on Manual pick)
  and is centered in the rest; the picture keeps its frame shape and is never
  stretched". Picking keeps the old rule.
- **The tip calibration paragraph** ("The Tip calibration page is numbered
  steps…"):
  - say it is a `PathSteps` path: go to the disc, disc in the picture (one
    click also starts the calibration), find the tip, adjust by hand
    (optional);
  - keep the readiness rule as it is, and "no Check button";
  - Controls folded on the step, opened by Adjust by hand;
- **"Nothing shifts":** the Tip card folding away its rack when a tip goes on
  moves what is under it, a second thing a job's end moves.

---

## 7. Open questions (the defaults are in use)

1. **Convert the other numbered step pages** (Camera calibration, Dish
   bottom, Well center, Picking) to `PathSteps`? Default: not in this task.
   Only Tip, with the component ready for them.
2. **The numbers in the StepBar** (Deck, Tip, Dish bottom…) remain. Should
   they also become nodes without numbers? Default: keep the numbers until
   decided.
3. **The time left during find:** the routine has no estimate, so the bar
   only.

Where the prototype differs from this document (Put it back, Go again,
Calibrate again, Check: tip to crosshair, the ring and the chips on the
picture, Controls hidden, the toast at the middle), this document wins.
