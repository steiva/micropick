# Liquid: Plan and Run — redesign specification

Status: built (#67). Date: 2026-11-03. This document says what was built;
where it was written as instructions to the builder, they are kept as the
reasons for the design.
Audience: Claude Code (implementation) and Ivan (review).

**This replaces §6.8 (Liquid Plan) and §6.9 (Run) of `REDESIGN.md`.**
Everything else in `REDESIGN.md` still applies: tokens (§2), shared components
(§3), flows (§4), cross-cutting behaviors (§7).

---

## 0. How to use this document

**Sources of truth, in order:**

1. **This document.**
2. **The prototype:** `docs/redesign/prototype/LiquidPlan.dc.html` and
   `LiquidRun.dc.html`. As before, this is canvas HTML that does not run here.
   Read it for layout, sizes, colors, copy and states. Each file has a
   `scenario` tweak at the bottom that lists the states the designers
   reviewed, and `renderVals()` shows how one state turns into the next.
   Where the prototype and this document disagree, this document wins.
3. `REDESIGN.md`, then `CLAUDE.md` (§10 below).

**Why this rework exists.** The first implementation of §6.8 was tested with
users, and they could not use it. The full list of problems is in §1. In
short, the screen spoke the program's language (groups, locations, levels,
offsets) instead of the biologist's: "take this from here, put it there".

**Hard requirements.** These were missed or got lost last time. Each one has
an acceptance check in §9, and the work is not done until those checks pass.

- **R1. Steps can be reordered by dragging their cards** (§5). Arrows alone,
  or reordering only in a dialog, do not count.
- **R2. A step reads as a sentence**, from → to, never as a list of
  aspirate/dispense lines.
- **R3. "From" is asked before "To".**
- **R4. The trash is set once per plan.** A missing trash is never a dead
  end; the screen says how to fix it.
- **R5. Plates are drawn in their real proportions.** Every well plate has the
  same footprint (SBS, 127.76 × 85.48 mm); only the number and size of the
  wells change. A 6-well plate and a 96-well plate are drawn as boxes of the
  same size.

---

## 1. What users could not do (from testing, 2026-11)

| # | Problem | Fixed by |
|---|---|---|
| 1 | The order was inverted: you had to "Make a group" of destination wells first, and the source was a side form. | §3: a step asks From, then To |
| 2 | The source form (Where / Labware / Well / Level / Offset XYZ) was not understood. | §3.2: pick the source on the plate or on the camera |
| 3 | Steps were unreadable lines and could not be reordered. | §3.1, §5 |
| 4 | Groups felt heavy; users asked for "Step 1, Step 2…". | §2: the plan is steps |
| 5 | Mix, More, Move to, Blow out, Wait and Pause were unexplained. | §3.5, §3.6: each has a one-line explanation |
| 6 | Everything was one color, with no visible card edges and no cue that the list scrolls. | §3.1 |
| 7 | Saved points could not be created; memo points and saved points were separate things. | §3.2 "A point" |
| 8 | The trash was a dead end ("no saved point 'targetwell'", *Use this plan* disabled with no reason). | §3.7, §4 |
| 9 | Height references ("Measured bottom") were unclear. | §3.4: a drawing of the well |
| 10 | "What to run" wording was unclear. | §6: Run screen modes |
| 11 | The Run screen did not show which step is running, from → to, or how far along it is. | §6 |

---

## 2. Model: the plan is a list of steps

### 2.1 What the user sees

A **plan** is an ordered list of **steps**. Kinds of step:

| Kind (UI name) | What it does | One-line help shown in the "Add a step" menu |
|---|---|---|
| **Move liquid** | Take from a source, put into destinations. | "From a well or a point into wells, or into the trash." |
| **Mix** | Pipette up and down in wells. | "Pipette up and down in wells, so what was added mixes in." |
| **Blow out** | Blow the tip out in a well, at a point, at a spot brought to by hand or over the trash. | "Blows what is left out of the tip." |
| **Move the tip** | Take the tip to a well, a point or a spot, with no liquid. | "Moves the tip to a place, with no liquid." |
| **Wait** | Do nothing for a set time. | "The robot does nothing for a set time, e.g. 5 min." |
| **Wait for me** | Hold until the user presses Continue. | "The robot stops until you press Continue." |

Every **Move liquid** step has:

- **Take from:** one of
  - *A well:* one or more wells of one plate;
  - *A point:* a saved or memo point;
  - *By hand:* a spot the user drives the tip to.
- **Put into:** one of
  - *Wells:* one or more wells of one plate;
  - *A point;*
  - *Trash.*
- **Amount:** volume per well (µl); take speed and put speed, each a number in
  µl/s (10 by default); *Take it at* and *Put it at*, each a height reference,
  an offset in mm and a shift aside in X and Y.
- **Extras:** switches and the wait after each well, listed in §3.5.

### 2.2 Pairing rules

The rule that applies is decided by how many sources and destinations the
step has. It is always shown under the plate as a sentence, and it is the
only explanation the user needs.

| From | To | Rule shown on screen | Allowed |
|---|---|---|---|
| 1 (well, point or spot) | N wells | "One source, *V* µl into each of *N* wells. The robot refills when the tip runs low." (the second sentence only without mixing, §2.3) | yes |
| N wells | N wells | "Well by well: first to first, second to second, *V* µl each." | yes |
| N wells | 1 well or a point | "From each of *N* wells into *X*, *V* µl each." | yes |
| N wells | Trash | "Empty each of the *N* wells into the trash." | yes |
| 1 | 1 / point / Trash | "Move *V* µl from *A* to *B*." | yes |
| N wells | M wells, N ≠ M, both > 1 | "Take from 1 well, or from as many wells as it goes into (*M*)." — shown in warn color | **no**, the step shows *Not finished* |

**Order of wells:** wells are taken by row (A1, A2 … A12, B1 …), using the
plate's own ordering (`core.liquid.ordered_wells`, `by_row`). For N→N, the
k-th source goes to the k-th destination in that order. When the user hovers
a selected well, its partner well is outlined on the other plate. This is
optional polish, so do it last.

### 2.3 Code: a plan layer over the existing engine

Do **not** rewrite `core.liquid.Program` or `workflows.liquid.run`. They are
correct and tested: refills, auto empty, measured bottom, slow lift, the
trash blow-out, pause/stop/resume. Add a thin layer that compiles each step
into the existing model.

**New module `core/liquid_plan.py`** (no Qt, no robot). It is data
(pydantic, `extra="forbid"`), saved as JSON the way `Program` is
(atomic write).

```text
LiquidPlan
  version: int
  name: str
  tip_ul: float = TIP_UL
  trash: Location | None          # the existing core.liquid.Location
  steps: list[PlanStep]           # discriminated on "kind"

PlanStep = MoveLiquid | MixStep | BlowOutStep | MoveTip | WaitStep | WaitForMe
  common fields: id (uuid4 str, stable), color (hex, see §3.1)

MoveLiquid
  source: Place
  dest: Place
  volume_ul: float
  take_flow_ul_s: float = 10
  put_flow_ul_s: float = 10
  take_at: Height                 # used when the source is a well
  put_at: Height                  # used when the destination is a well
  mix_after: bool; mix_cycles: int = 3; mix_ul: float | None (None: = volume_ul)
  pause_after_first: bool
  empty_at_end: bool = True
  slow_lift: bool                 # notebook 03's slow lift after taking from wells
  wait_after_s: float = 0         # the old per-well Wait: seconds after each well

Place
  kind: "wells" | "point" | "spot" | "trash"
  slot, load_name, wells: list[str]          # kind "wells"
  point: str                                 # kind "point": a name in profile.positions
  spot: {name, x, y, z}                      # kind "spot": coordinates kept in the plan
                                             #   (By hand, or a memo point that was not starred)

Height
  ref: "top" | "middle" | "bottom"
  offset_mm: float
  aside_x_mm: float = 0           # shift from the well's center: beside a cuboid
  aside_y_mm: float = 0

MixStep:   wells Place (kind "wells"), cycles, volume_ul, flow_ul_s, at: Height
BlowOutStep: place Place (a well, a point, a spot or the trash), flow_ul_s, at: Height
MoveTip:   place Place (a well, a point or a spot), at: Height
WaitStep:  seconds
WaitForMe: message: str = "Check the plate, then continue."
```

**`compile_step(plan, index, plates) -> Program`** turns one Move liquid, Mix,
Blow out or Move the tip step into a `Program` that `workflows.liquid.run` can
execute. Wait and Wait for me do not compile; the plan runner handles them
(item 2 below). The compile rules:

| Step | Groups | Steps inside each group |
|---|---|---|
| 1 → N wells | one group: the N destination wells | `Aspirate(source, refill=True, volume = largest multiple of V that fits in tip_ul)`, `Dispense(this_well, V)`, then `Mix(this_well)` if *mix_after* |
| 1 → N wells with *mix_after* | same | `Aspirate(source, refill=False, V)`, `Dispense(this_well, V)`, `Mix(this_well)`. With mixing the tip carries the destination liquid, so it must not go back to the source holding a reserve (see §11, Q3) |
| N → N, 1 → 1 well | a group of one destination well each | `Aspirate(fixed source well k, refill=False, V)`, `Dispense(this_well, V)`, mix if on. 1 → 1 takes exactly V, with no refill |
| N → 1 well, point or spot | one group: the N source wells | `Aspirate(this_well, V)`, `Dispense(fixed dest, V)`, mix in the fixed destination if on |
| N → Trash | one group: the N source wells | `Aspirate(this_well, V)`, `Dispense(plan.trash, auto_empty=True)`. This is the washing pattern that already exists |
| Point or spot → point, spot or Trash | one carrier group | `Aspirate(source, V)`, `Dispense(dest, V)` or auto empty, mix if on |
| Mix | one group: the wells | `Mix(this_well, cycles, volume)` |
| Blow out | one carrier group | `BlowOut(place)`. At the trash it needs the trash, as a step going into the trash does |
| Move the tip | one carrier group | `MoveTo(place)` |

A **carrier group** is one well of a stand-in plate that only the plan layer
adds, since the engine runs wells of groups and these steps have none. It never
reaches the screen.

Then, for all of the above:

- `pause_after_first` goes on the first compiled group only, and
  `empty_at_end` (`Group.empty_after`) on the last only, so N → N does not
  visit the trash per well.
- `wait_after_s` adds a `Wait` at the end of each compiled well's steps, after
  any mixing.
- *slow_lift* sets `Aspirate.lift_steps`, `lift_step_mm` and `lift_pause_s`
  to notebook 03's values: 4 rises of 0.1 mm, 0.2 s apart.

**Heights** map to `Location.level` and `offset[2]`:

- `top` → `top`.
- `middle` → `center`.
- `bottom` → `measured_bottom` when the plate's well center and depth are
  known (`Plate.has_bottom`), otherwise `bottom` from the labware definition.
  The UI says which one it is (§3.4).
- Points and spots use their own Z plus the offset. Their height reference is
  not shown.

**Speed** is `flow_rate` itself: typed in µl/s, 10 by default (Ivan,
2026-11-03; the value notebook 03 washes at). There is no Slow / Normal / Fast.
Taking and putting have a field each, as the old steps did: a wash takes at 10
and empties at 200.

**Aside** maps to `offset[0]` and `offset[1]`: from the measured center, the
old steps' sideways offset, kept because a wash draws beside the cuboid.

**Points and spots.** `workflows.liquid` looks points up by name in
`positions`. At compile time, the runner passes `positions` =
`profile.positions` plus one entry per spot under a reserved name
(`"plan-spot:<step id>:<role>"`, since both ends of a step can be spots), in
the same form as `profile.positions`. Nothing about this reaches the screen.

**Small engine additions** (separate commits, with tests, before any UI):

1. `Group.empty_after: bool`. After the group's last well, dispense
   everything that is left over the program's trash and blow out. This backs
   the *empty_at_end* switch. An auto-empty dispense cannot do it, because in
   a refill chain it would empty after every well. It runs before the well
   counts as done, so a stop during it resumes it; with an empty tip it makes
   no trip; the group carries no speed, so it empties at a fixed 200 µl/s.
2. `workflows/liquid_plan.py: run_plan(...)`. It runs the steps in order, or
   only one when `only=index` is given. It compiles each step and calls the
   existing `run()`.
   - One `LiquidState` per step for `done` and `resume`, so a stopped step
     continues where it stopped. `in_tip` is carried from step to step.
   - A new plan's run begins with the blow-out over the trash, a program of
     its own on the carrier, not a plan step and not a setting. Only from
     step 1 with nothing done (`PlanState.fresh`), so Continue does not.
   - Wait: sleep in short slices through the same pause/stop gate (`gate`),
     so Pause and Stop work during it. A stopped Wait continues with the time
     it had left.
   - Wait for me: count it as done, set `pause`, call `on_paused(message)`,
     then `gate`, so a Continue does not wait again.
   - `after_step(index)` is asked after each step but the last, for One step
     at a time: `NEXT`, `REPEAT` or `FINISH`. It blocks, the run still holding
     the robot (`StepChoice` is that wait for a caller on another thread).
   - Callbacks: `on_step(index, state)`, where state is one of `started`,
     `done`, `paused` and `waiting`, plus the existing `on_progress(done,
     total, gi, well)`, mapped to the step's own count of wells.
   - Run through `JogPanel.run_job`, like every robot command (`CLAUDE.md`).
3. `plan_problems(plan, plates, positions, capacities) -> dict[step_id |
   "plan", list[str]]`. Compile each step and reuse
   `workflows.liquid.problems`, with a stand-in trash so that a missing trash
   is a plan problem and never a step's, plus the plan's own checks: trash
   set, pairing rule valid (§2.2), and the volume played through all steps
   with `in_tip` carried over. `capacities` is what a well holds by slot, the
   page's to give, so the engine's `Plate` stays as it was. The messages must read as sentences for a biologist
   ("Choose where it goes.", "Set the trash to run this plan.",
   "50 µl is more than this well holds (36 µl)."). Rewrite the existing
   `problems()` messages for the screen, not in the engine.

**Old program files.** `Program` JSON stays readable by notebooks and the
engine. The template list shows only plans; old programs are not converted
and are listed gray as "Made with an older version" (§11, Q4).

**Washing must still be possible.** It is the new plan as a test fixture (no
bundled template: it would not know the bench's slots and wells), and a bench
check:

1. *Move liquid* from the N wells (bottom, measured, +2.5 mm, aside X −2 mm,
   10 µl/s) to the Trash (200 µl/s), with *slow lift* on.
2. *Move liquid* from the reservoir to the same N wells (top), with *pause
   after the first well* on.

### 2.4 What the old pages could do, and where it went

This redesign changes the workflow, never the robot's functions. Every function
of the old Plan and Run pages stays reachable:

| Old function | Where it goes |
|---|---|
| Offset X, Y, Z in a location | Aside X, Y and the offset on Take it at, Put it at |
| Separate aspirate and dispense flow rate | Take speed, Put speed |
| What a tip holds | A field beside the Trash row |
| Trash anywhere: well or point, with its level and offset | The Trash chooser keeps level and offset |
| Run one group only | One step at a time, Repeat; `run_plan(only=)` |
| Blow out as a step | A kind of plan step, **Blow out**, from the menu (no switch); and automatically first when a new plan's run starts |
| Wait or Pause inside a chain, per well | **Wait after each well**, seconds, in Extras; Pause after the first well stays |
| Move to (no liquid) | A kind of plan step, **Move the tip**, to a well, a point or a spot |
| Refill switch | Automatic by the pairing; 1 → N refills unless mixing |
| `here` (same place as before), By hand as a pause | Replaced by a stored spot (§3.2) |
| Dispense a part of what was drawn | Volume per well; a remainder stays in the tip when Empty the tip is off |

---

## 3. Plan screen (Liquid step 4)

Shell as in `REDESIGN.md`:

- TopBar `⌂ Home › Liquid`;
- StepBar: Deck ✓, Tip ✓, Well center ✓, **Plan**, Run;
- Footer: on the left, the reason Next is disabled when it is disabled; on the
  right, **Back**, **Next**.

The body is two areas:

- **Right panel (460 px): the plan.** Start from (A template / New plan),
  the Trash row with what a tip holds beside it, then the step cards, **Add a
  step**, and under it **Save as template**.
- **Left area: whatever the current question is about.** It changes with the
  section of the open step (§3.2–3.6). With no step open it shows the
  overview (§3.8).

### 3.1 Step cards (right panel)

Each card is a **Card** (radius 14, `card` background, 1 px `card-border`,
which the users asked to be visible). Collapsed, a card shows one header row:

```
⠿  (2)  Move 50 µl                                    Not finished
        [Slot 6 · A2]  →  [Slot 2 · B1–B12 (12)]
```

- `⠿` is the **drag handle** (§5).
- `(2)` is the step number on a filled circle in the step's color.
- The title is the sentence's verb part:
  - "Move 50 µl";
  - "Empty 12 wells" when the destination is Trash;
  - "Mix 12 wells · 3×";
  - "Blow out";
  - "Move the tip";
  - "Wait 5 min";
  - "Wait for me".
- Chips: From in `tips`-colored text on `tips-bg`, then →, then To in `plate`
  on `plate-bg`. A well range reads `Slot 2 · B1–B12 (12)`; a single well
  reads `Slot 6 · A2`; then `Point · Reservoir`, `By hand`, `Trash`.
- *Not finished* (warn color) shows only on collapsed cards that have a
  problem from `plan_problems`.

**Step color.** It is picked from the group palette when the step is created
and **stays with the step when the step is moved**. The number changes with
the position; the color does not. The plate highlight follows the step.

**Open card.** Click the header to open it; only one card is open at a time.
Open, the card has the `accent` border on a slightly lighter background and
its rows. A Move liquid card has four; the other kinds' are in §3.6:

| Label | Value |
|---|---|
| Take from | `Slot 6 · A1` |
| Put into | `Slot 2 · A1–A12 (12)` |
| Amount | `50 µl per well · 10 µl/s` |
| Extras | `pause after the first well` / `none` |

- Each row is a button that selects that section and changes the left area.
  The selected row has the `selected-bg` background. Take from is labeled in
  `tips` color, Put into in `plate` color. A value still missing reads
  "choose", in warn color.
- Under the rows, **Try once**, **Delete** (`danger-text`), and on the right
  the primary **Next: Put into → Next: Amount → Next: Extras → Done**.
  - Next opens the next section.
  - Done closes the card with the toast "Step 2 ready."
  - Try once runs the step on its first source/destination pair only (§7).
- Above the list, the caption **STEPS**, and on the right "drag ⠿ to
  reorder". This is the one place the gesture is explained on screen.
- The list scrolls inside the panel. When it is scrolled, a 16 px fade at the
  top and bottom edges shows that there is more.

After a change of section, by a row or by Next, no field has the focus, so a
stray key changes nothing. A field the operator clicked keeps the focus while
they type, also while what they type updates the page. This holds for every
section, whichever side its fields are on.

### 3.2 Section "Take from" (left area)

- Header: the caption "STEP 1 · TAKE FROM" (`tips` color) and the title
  "Where is the liquid taken from?".
- On the title's row, its center at the center of the left area, the Segmented
  control **A well | A point | By hand**. Nothing under the row moves for it.
  When the title would touch it, it moves right just enough to clear the title,
  on the same row. Every place choice is there: Take from, Put into, Where and
  the trash chooser.

**A well:**

- A row with the **MiniDeckDropdown** (`Slot 6 · 6-well plate`). Only plates
  are selectable; the tiles show "Measured" or "Not measured" (`REDESIGN.md`
  §3). Next to it, the facts in `text-3`: "6 wells · 16.8 ml · from drawing".
  Then, on the right, **Undo, Redo, Select all, Clear**.
- The **PlateGrid** (`REDESIGN.md` §3: click a well, click a row or column
  header, drag a box):
  - drawn in the plate's **real proportions** (R5), centered in the area;
  - selected wells in the `tips` color with a 3 px `text` ring;
  - wells used by *other* steps on this plate in those steps' dim colors, so
    the user sees what is taken.
- Under the plate, the hint "Click a well. Click several wells to take from
  each of them." Once the destination is chosen, this becomes the rule
  sentence (§2.2).
- Choosing a plate that is not measured shows the warn banner from the old
  §6.8 ("The well center of this plate was not measured. The robot will use
  the plate's drawing." **Measure now** / **Use anyway**).

**A point:** the live camera picture.

- Over it, at the top left, the X/Y/Z readout.
- Points are drawn on the picture in `point` color with their names.
- At the bottom, an overlay with the point buttons: saved points (★ filled,
  from `profile.positions`) and memo points (☆ empty, from
  `Session.memo_points`), and the line "Right-click the picture to add a
  point. The star keeps a point for later experiments."
  - Right-click adds a memo point. This is the existing memo point code,
    with the right-click rule from `REDESIGN.md` §10.
  - Clicking ☆ makes the point a saved point in the profile, asking for a
    name with M1 as the default. One kind of point, two states: kept and not
    kept.
  - A memo point used in a step is stored in the plan as a **spot**, with its
    coordinates, so the plan still works after the program restarts.
- The **Controls** button opens the JogPad.

**By hand:** the same camera, plus "Bring the tip to the spot with Controls,
then use it. The robot comes back here every time." and **Use this spot**
(primary).

- Use this spot stores the current tip X/Y/Z as the step's spot. The toast
  says "Spot saved for this step."
- It is not a pause during the run: the run drives to the stored spot.

### 3.3 Section "Put into"

The same layout as Take from:

- caption "STEP 1 · PUT INTO" in `plate` color; title "Where does it go?";
- Segmented control **Wells | A point | Trash**;
- selected wells in `plate` color;
- under the plate, the **rule sentence** (§2.2). It turns warn-colored, with
  an ⓘ icon, when the combination is not allowed.

**Trash** shows the trash icon, "Into the trash", "The waste dish in slot 3.
It is set once for the whole plan; every step can use it." and **Change
where the trash is**, which opens the same chooser as the Trash row (§3.7).

### 3.4 Section "Amount"

The fields are in the left area, in the middle of the room under the title
(the open card keeps only its rows and buttons):

- **Volume per well** (µl), **Take speed** and **Put speed** (µl/s, 10 by
  default), in one row;
- **Take it at** (Measured from: Bottom / Middle / Top, then the offset in mm,
  then Aside X and Y in mm). Only when the source is wells.
- **Put it at** (the same). Only when the destination is wells.

Under that row, side by side, a card for each height: **Take it at** (`tips`
color) and **Put it at** (`plate` color), its fields beside a small
cross-section of a well, which draws the height instead of explaining it in
words. Each one has:

- dashed *top* and *bottom* lines;
- the tip drawn down to the chosen height, with a colored line at that height;
- a plain sentence under it: "2 mm above the bottom", "1 mm below the top",
  "in the middle";
- a quiet note on where "bottom" comes from: "Bottom measured in Well center"
  or "Bottom from the plate drawing".

The drawing updates live as the fields change. A positive offset is up and a
negative one is down. The sentence uses "above" and "below", never "+" and
"−".

### 3.5 Section "Extras"

These are switches in the left area, a card each, about half its width, in the
middle of the room under the title. Each one has its help line under it:

| Switch | Help line | Shown when |
|---|---|---|
| Mix after putting it in | "Pipettes up and down 3 times in each well." When on, *times* and *µl* appear inline | the destination is wells |
| Pause after the first well | "So you can check it before the rest." | always |
| Empty the tip into the trash at the end | "Leftover liquid goes to the trash." | always; on by default |
| Lift out slowly | "Small steps up after taking, so tissue is not pulled along." | the source is wells |
| Wait after each well | "The robot stays still this long." A seconds field, 0 for none | always |

### 3.6 Mix, Blow out, Move the tip, Wait, Wait for me (other step kinds)

- **Mix:** sections *Wells* (the plate, the same as Put into, colored with the
  step) and *How*: Times, Volume and Speed in one row in a card, over the
  height card.
- **Blow out:** sections *Where* (**A well | A point | By hand | Trash**, By
  hand as Move the tip's, a spot kept in the plan) and *How*: Speed over the
  height card in a well, anywhere else Speed and the line "A point and the
  trash are used at their own height." Add a step makes one on A well with
  nothing chosen, so the plate is seen and the place chosen on purpose: until
  then it says "Choose the place." and Try once and the footer's Next are gray.
  Only Add a step does: a step's stored default stays Trash, so plans and
  templates saved before read as they were.
- **Move the tip:** sections *Where* (**A well | A point | By hand**) and, in a
  well, *How*: only the height card.

*How* is asked in the left area with Amount's parts, in a column of Extras'
width, 480 px, in the middle of the room under the title. The open card holds
none of it.
- **Wait:** in the left area, in the same column, a large "5 min" over
  **Minutes** and **Seconds** in a card, which it follows as they are typed,
  and "The robot does nothing for this long. Pause and Stop still work."
- **Wait for me:** in the left area, in the same column, the message the user
  will see in a card, over a preview of the Run screen's waiting strip, which
  follows it as it is typed.

No kind of step has a field in the open card: only its rows and buttons.

### 3.7 Trash row (once per plan)

This row sits under *Start from*, above the steps. It is a full-width button
with the trash icon:

- **Set:** "Trash · Waste dish · slot 3 · used by every step", then
  **Change** in accent text.
- **Not set:** warn border and background, "Not set yet. Needed before
  running.", **Set**.

Click opens a chooser with **A well** (MiniDeckDropdown of any labware, then
one well) or **A point** (the camera, as in §3.2), then its height and offset.
It has a "Use for future plans" checkbox, ticked by default, which stores the
choice in the profile as the default trash for new plans, **Remove the trash**
and **Done**. Users asked for "set it once, for all future steps".

The trash is `LiquidPlan.trash`, a `Location`, so a memo point chosen there is
named and saved first. It is needed to *run*, never to edit, and to Try once
only for a step that goes into it (§7).

### 3.8 Overview (no step open)

The left area shows the caption "THE WHOLE PLAN" and the title "3 steps, in
this order", then:

- the plate, or a plate switcher if the plan uses several, with every step's
  wells in that step's color;
- a legend: "● Step 1: Slot 6 · A1 → Slot 2 · A1–A12 (12)", one line per step.

### 3.9 Add a step

A dashed full-width button **+ Add a step** under the list. It opens a menu
upward with the six kinds from §2.1, each with its help line.

- *Move liquid* adds a card at the end, opens it, and selects Take from.
- The other kinds add their card open as well.

### 3.10 Start from, templates, Next

- **A template / New plan** tiles behave like Auto Plan (`REDESIGN.md` §6.3,
  §8.6): named files, the saved list, Open from file…, and "Save changes to
  "<name>"" versus "Save as template".
- **New plan** starts empty, with the default trash if one was set.
- There is nothing to confirm: the plan is handed to the session as it is
  edited, and the step is done while it has no problems.
- **Next** goes to Run. It is disabled (opacity 0.4) while `plan_problems`
  returns anything. The footer then says why, in one sentence: "Set the trash
  to run this plan." or "Finish step 2 to run this plan." The step cards with
  problems show *Not finished*.
- **Empty plan:** the left area shows "Add the first step" with a large
  **+ Move liquid** button, and the right panel shows only Start from, Trash
  and Add a step.

---

## 4. States and copy that must not regress

- No internal names on screen: no "this_well", "here", "measured_bottom",
  "Location", "group", "aspirate" or "dispense". The verbs are *take*, *put*,
  *move*, *empty*, *mix*.
- No dead ends. Every disabled control is accompanied by a sentence that says
  what to do.
- American spelling, short sentences (`CLAUDE.md`, "Words on screen").

---

## 5. Reordering steps by drag and drop (R1, hard requirement)

**Last time this was not built. Build it first in the Plan screen work, and
make it pass §9 before anything else.**

### 5.1 Behavior

1. The user presses on the **⠿ handle** of any card, open or collapsed, and
   moves the mouse more than `QApplication.startDragDistance()`.
2. The card lifts:
   - a semi-transparent pixmap of the card (`card.grab()`, about 85 %
     opacity) follows the pointer;
   - the card's original place shows an empty placeholder of the same height
     (dashed `control-border`, `bg` fill).
3. While dragging, a **2 px `accent` insertion line** shows between the cards
   where the step will land. It snaps to the nearest gap: above a card's
   middle means before that card.
4. Near the top or bottom edge of the list (24 px), the list **autoscrolls**.
5. On release, the step moves to that position:
   - the cards renumber;
   - colors stay with their steps;
   - the open card stays open;
   - the left area (overview, plate colors) updates;
   - the toast reads "Step moved to position 1".
6. **Esc** during the drag, or a release outside the list, cancels it: nothing
   moves. This is the only case where Esc does not mean stop, so take care
   that the window-wide Esc stop is not triggered by a drag cancel. If that
   cannot be separated cleanly, cancel only by releasing outside the list,
   and say so in the commit.
7. The cursor is `OpenHandCursor` over the handle and `ClosedHandCursor`
   while dragging.
8. Clicking the header (not the handle) still opens and closes the card. A
   press on the handle that does not move does nothing.
9. **Undo** (Ctrl+Z, and the Undo button where the plan has one) reverts the
   move.
10. **Keyboard and a no-drag fallback, in addition to dragging, not instead of
    it:** Alt+↑ and Alt+↓ move the focused card. The card header has a ⋯
    menu with *Move up*, *Move down*, *Duplicate* and *Delete*.
11. **While a run is in progress the plan cannot be changed:** handles are
    hidden and Add, Delete and Move are disabled.

### 5.2 Implementation

- **Do not use `QListWidget` / `QListView` with `setItemWidget` and
  `InternalMove`.** On a move, Qt deletes or loses the item widget, which is a
  known pitfall, and the card's state goes with it. It also cannot draw our
  placeholder.
- Build `gui/widgets/step_list.py: StepList(QWidget)`:
  - a `QScrollArea` containing a column (`QVBoxLayout`) of `PlanStepCard`
    widgets (`StepCard` is the calibration pages');
  - the drag starts in `PlanStepCard`'s handle (`mousePressEvent` /
    `mouseMoveEvent`) with a `QDrag`, mime type
    `application/x-micropick-plan-step`, payload = the step's `id`;
  - the `StepList` accepts drops (`dragEnterEvent`, `dragMoveEvent`,
    `dropEvent`) and paints the insertion line in an overlay widget above the
    cards;
  - a `QTimer` drives the autoscroll while the pointer is near an edge.
- On drop, the widget **does not reorder itself**. It emits
  `moveRequested(step_id, new_index)`. The page applies the move to
  `LiquidPlan.steps` (one undo entry), then calls `StepList.set_steps(...)`,
  which reuses the existing `PlanStepCard` widgets by `id` and only re-lays them
  out. The model is the single source of order.
- Pure function in `core/liquid_plan.py`: `move_step(plan, index,
  new_index) -> LiquidPlan`. It is easy to test; the page uses it.

---

## 6. Run screen (Liquid step 5)

StepBar: Deck ✓, Tip ✓, Well center ✓, Plan ✓ (clickable, back to Plan),
**Run**.

### 6.1 Left area: what the robot is doing now

- **Header:**
  - a caption in the step's color: "STEP 2 OF 3 · RUNNING". It turns warn
    color with "PAUSED" or "WAITING FOR YOU", `ok` with "DONE", and `muted`
    with "READY";
  - the title: the step's sentence, From → To ("Slot 6 · A2 → Slot 2 ·
    B1–B12 (12)");
  - on the right, a big mono counter "7 / 12" with "wells done in this step".
- **Progress bar** (8 px, radius 999) in the step color, warn color while
  paused.
- **Two equal cards side by side, with an arrow between them:** *Taking from*
  and *Putting into*.
  - Each has a caption, `Slot N · <plate>` and the **plate drawn in its real
    proportions (R5)**. Both cards are the same width and the plate box is the
    same size in both; a 6-well plate has six big wells and a 96-well plate
    has 96 small ones.
  - Wells: done in the step color, the current one with a 2 px `text` ring,
    still to do in the step's dim color, and wells of earlier steps in their
    colors.
  - Under the source: "50 µl per well · refills when the tip runs low".
    Under the destination: a legend (done / now / still to do).
  - When the source is a point or a spot, the source card shows the point's
    name and coordinates instead of a plate. When the destination is Trash,
    it shows the trash icon and "Into the trash".

### 6.2 Right panel (420 px): the plan, with where we are

- **How to run**, a Segmented control:
  - **One step at a time** (default): "After each step the robot stops and
    asks what next: go on, repeat the step, or finish."
  - **All steps in a row:** "The robot goes from step to step without asking.
    Pauses you put in the plan still happen."
- **Steps:** read-only cards with the same chips as on Plan, and a status on
  the right:
  - "✓ Done" (`ok`, the card at 70 % opacity);
  - "Running · 7/12" (the step color, accent border);
  - "Paused" (warn);
  - "Next" (`muted`);
  - "First" before the start.
- Next to the Steps caption: "Trash: waste dish · slot 3".
- Under the steps, what the tip holds, with **Tip is empty** for a tip emptied
  some other way.

### 6.3 Banners in the left area

- **After a step** (One step at a time): an `ok-bg` banner, "Step 2 is done.
  12 of 12 wells." / "Check the plate if you want. Nothing happens until you
  choose." with **Finish here**, **Repeat step 2** and **Go to step 3**
  (primary).
  - Repeat runs the step again from its first well, with a new `LiquidState`
    for that step.
- **After the last step:** "All 3 steps done." with **Run again**, **Back to
  plan** and **Home**.
- **Waiting for you** (Wait for me, or Pause after the first well): a
  `warn-bg` strip.
  - Title: "First well done. Check it, then continue." or the Wait for me
    message.
  - Sub-line: why it stopped ("You asked for this in the plan.").
  - No light pulse (`REDESIGN.md` §12.5): the window flashes in the taskbar,
    as on Picking.
- **Paused by the user:** a `warn-bg` strip, "Paused. The robot holds the tip
  where it is." / "Press Continue to go on from well 8."
- **Wait step running:** the counter shows the time left, "4:12 left".

### 6.4 Footer

- **Before the start:** **Back to plan** and **Start step 1**, primary. In
  *All steps* mode the button reads **Start**. Start asks nothing beyond the
  existing confirmation (labware in place, lids off).
- **Running:**
  - Back to plan is disabled (opacity 0.4) with "The plan can be changed when
    the robot is not moving.";
  - **Pause** / **Continue**: one button, warn-filled while paused;
  - **Stop run**, `danger`.
  - Stop keeps the per-step states, so the primary becomes **Continue step 2
    from well 8**, with **Start the step over** while the step was begun and
    **Start from step 1** as secondary options.
- The StatusPill in the TopBar shows "Liquid · step 2 of 3", "Paused" or
  "Waiting for you" (`REDESIGN.md` §3).

---

## 7. Try once (from the Plan screen)

- Try once runs the open step on its **first pair only**: the first source to
  the first destination. It goes through `run_plan(only=index)` with a
  one-well limit (`first_pair=True`, through `do_step`), without the trash
  blow-out.
- The tip is left where the step put it, as `run_step` does today, so the
  user can look.
- Toasts: "Step 1: trying on the first well." when it starts and "Done. The
  tip holds 150 µl." when it ends.
- It needs no trash, unless the step itself goes into the trash: then it is
  refused with "Set the trash to try this step." It is refused, with a
  sentence, when the step has a problem. While the one place of a Mix, a Blow
  out or a Move the tip is not chosen the button is gray, that sentence its
  tooltip.

---

## 8. Files

| File | What |
|---|---|
| `core/liquid_plan.py` | new: model, `compile_step`, `move_step`, (de)serialization |
| `workflows/liquid.py` | add `Group.empty_after` handling (§2.3, item 1) |
| `workflows/liquid_plan.py` | new: `run_plan`, `plan_problems` |
| `gui/widgets/step_list.py` | new: `StepList`, `PlanStepCard` with drag (§5) |
| `gui/widgets/plan_forms.py` | new: the fields of Amount, Extras and How |
| `gui/widgets/well_heights.py` | new: the well cross-section (§3.4) |
| `gui/widgets/plate_grid.py` (or wherever PlateGrid lives) | real-proportion drawing (R5); "used by other steps" dim colors |
| the Liquid Plan and Run pages of the redesign | rebuilt to this spec, reusing MiniDeckDropdown, PlateGrid, CameraView, JogPad, Segmented, Switch, Select, Toast and banners |
| `tests/` | see §9 |
| `docs/GUI_BENCH_CHECKS.md` | a section for Liquid Plan and Run |
| `CLAUDE.md` | §10 |

Commits:

1. model and compile;
2. `empty_after`;
3. `run_plan` and `plan_problems`;
4. StepList with drag;
5. Plan screen;
6. Run screen;
7. docs.

Tests stay green after each commit.

---

## 9. Acceptance checks

### Automated (pytest, `--mock`, pytest-qt where a widget is involved)

1. `move_step` moves any index to any index and keeps ids and colors.
   Moving to the same place is a no-op.
2. **StepList drag:** with three cards, simulate a drag of card 3's handle to
   above card 1 (`QTest.mousePress` / `mouseMove` / `mouseRelease` on the
   handle, or by driving the `QDrag` through the list's
   `dragMoveEvent`/`dropEvent` with real mime data). Assert that
   `moveRequested(id3, 0)` is emitted, that after the page applies it the
   plan order is 3, 1, 2, and that the numbers on the cards read 1, 2, 3 in
   the new order.
3. Alt+↓ on card 1 moves it to position 2. Ctrl+Z restores the order.
4. While a run is in progress, the handles are hidden and drag does nothing.
5. Each row of the compile table (§2.3) gives the expected groups and steps.
   The washing plan compiles to the same steps as the existing washing
   program.
6. Each row of the pairing table (§2.2) gives its sentence, and N→M with
   N ≠ M is a problem.
7. `run_plan` on the mock robot:
   - runs all steps in order;
   - with `only=1` runs only step 2;
   - Stop in step 2 then Continue resumes at the next well;
   - Repeat redoes the step;
   - Wait honors Pause and Stop;
   - `in_tip` carries across steps;
   - `empty_after` leaves the tip empty.
8. `plan_problems`: a missing trash is a plan problem, not a step problem; an
   empty source or destination is a step problem with a sentence.

### Bench (in `docs/GUI_BENCH_CHECKS.md`, with a wash with the aside shift)

1. Build the three-step example from the prototype by mouse only:
   - 6-well A1 → 96 A1–A12, 50 µl, pause after the first well;
   - 6-well A2 → 96 B1–B12, mix on;
   - 96 D1–D12 → Trash.

   Then **drag step 3 to the top**, check the numbers and colors, and drag it
   back.
2. Delete the trash. Next is disabled and says why. Set the trash from the
   Trash row; Next is enabled.
3. Run in *One step at a time* mode:
   - the after-step banner appears;
   - Repeat works;
   - Go to step N works;
   - the counter and plate progress match the robot.
4. A 6-well and a 96-well plate look the same size on Run and on Plan.

---

## 10. `CLAUDE.md` update

Done. `CLAUDE.md`'s section **"Liquid is a plan of steps"** replaces "Liquid
handling is a program of blocks", with what was kept from it: the saved points,
the measured well center per slot and plate type. The PlateMirror window went
with the old page.

---

## 11. Questions, and how they were decided

1. **Speed numbers.** A typed field in µl/s, 10 by default (§2.3).
2. **Several destination plates in one step** (A → B, C, D). Not in this
   version. It is one step per plate; *Duplicate* in the ⋯ menu makes that
   quick.
3. **Mix after a 1 → N step.** No reserve in the tip; each well gets its own
   trip to the source (§2.3), because a tip that has mixed in a destination
   well carries that liquid.
4. **Old program files.** Not converted; listed gray.
5. **The Stop button in the TopBar while the robot moves** (`REDESIGN.md`
   §11, Q1). Still open, and it stays as it is; the Run screen has its own
   Stop run either way.
