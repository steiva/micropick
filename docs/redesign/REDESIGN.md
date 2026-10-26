# micropick GUI redesign — specification

Status: agreed design, ready to build. Date: 2026-10-26.
Audience: Claude Code (implementation) and Ivan (review).

## 0. How to use this document

**Sources of truth, in order:**

1. **This document.** It says what to build and why.
2. **The prototype**, in `docs/redesign/prototype/*.dc.html`. It is HTML from a
   design canvas, not runnable on its own: it needs a runtime the repo does not
   have. Read it as a precise reference for layout, sizes, colors, copy and
   state logic. Each file has a `renderVals()` script at the bottom that shows
   which states exist and how they change. Where the prototype and this
   document disagree, this document wins.
3. **Screenshots**, in `docs/redesign/screens/` (exported from the canvas),
   when present.
4. **§12, decisions from review.** It overrides anything above it here.
5. **`CLAUDE.md`**. It still applies, **except for the decisions listed in §10,
   which this redesign replaces.**

**Ground rules:**

- **This is a rebuild of the UI layer only.** `core/`, `hardware/`,
  `workflows/` and `config/` keep their behavior. Where the new UI needs
  something they do not offer, §8 lists it. Add those as small, separate
  commits, never mixed with UI work.
- **Existing pages are reused as the engine wherever possible.** Their logic
  (workers, `JogPanel.run_job`, readiness, Stop/hold, the camera views) is
  correct and must not be lost. What changes is how they are laid out, what
  they show and how they are reached.
- **Work in the phases of §9**, one commit per numbered item, tests green after
  each, and add a bench-check section for each phase to
  `docs/GUI_BENCH_CHECKS.md`.
- **If something here is unclear or conflicts with how the robot actually
  works, stop and ask.** Do not invent a flow. The calibration flows in
  particular were corrected by the users several times; the versions here are
  the confirmed ones.

## 1. Principles (each one came out of user testing)

1. **Every action gets a visible response.** A button changes state, a toast
   appears ("Plan ready", "Tip A3 is on."), or a mark turns green. Silence is
   a bug. Toggles (Light, Lock, Show past picks, Keys) are *filled* while on.
   Robot moves started from a button show a busy state ("Parking…") until
   they finish.
2. **Only the words the user needs.** No explanations of what is obvious. No
   internal names (`observe`, `robot session`, `dx/dy`). One short sentence at
   most. Copy in the prototype is a draft; keep it short and in the same
   spirit.
3. **One primary (blue, filled) button per area.** The next thing to do is
   always the most visible thing.
4. **Colors mean something and are never decoration:**
   - blue: action, current step;
   - green: done or saved;
   - yellow: waiting for you, or a warning that needs a conscious choice;
   - **red only for Stop run and destructive confirmations.**

   Color always comes with an icon or a word, because users look at the screen
   from the side, standing.
5. **Lead, don't lock.**
   - Steps are never locked; the user may open any step.
   - What is not ready yet is dimmed and says why in one line, with a button
     that goes where it gets fixed.
   - The section needed next is the open one (e.g. Dispense opens once the tip
     holds something). The user can still open the others.
6. **No scrolling in side panels** if it can be avoided. Use folding sections
   (one open at a time) and short internal lists that scroll inside their own
   box.
7. **The same thing looks and works the same everywhere:**
   - templates work the same in Auto Plan and Liquid Plan;
   - plate choice is the same mini-deck dropdown on every screen;
   - Analyze / Shake sit in the same place in Auto and Manual;
   - the jog pad is the same component everywhere.
8. **Readable from a meter away.** Sized for a mouse, not touch: see §12.6
   (superseded: ≥ 44 px for gloves).
9. **Mouse-only must work, keyboard is a bonus.** Every keyboard shortcut has
   an on-screen equivalent, and the Keys toggle on jog pads teaches the
   shortcuts.

## 2. Design tokens

This is a fixed **dark** theme. It does not follow the OS light/dark setting
(see §10).

### Colors

| Token | Hex | Use |
|---|---|---|
| `bg` | `#0F1114` | window background (dark grey, never pure black) |
| `bar` | `#14171B` | top bar, footer, side panels' secondary surfaces, inner list boxes |
| `steps-bg` | `#111317` | step bar |
| `card` | `#171A1F` | cards |
| `raised` | `#1B1E24` | popovers, dialogs, task tiles |
| `hover` | `#1E2228` | hover on transparent buttons |
| `selected-bg` | `#1A2130` | selected row or tile, current step chip |
| `line` | `#22262C` | dividers, panel borders |
| `card-border` | `#23272E` | card borders |
| `control-border` | `#2E333B` | buttons, inputs |
| `text` | `#E8EAED` | primary text |
| `text-2` | `#C9CED6` | secondary text, icon buttons |
| `text-3` | `#B3B9C2` | descriptions |
| `muted` | `#8A919B` | captions (CAPS labels), units |
| `faint` | `#5B626C` | breadcrumb chevrons, empty slots |
| `accent` | `#6CA6FF` | primary button fill, current step, focus |
| `accent-hover` | `#8DB8FF` | |
| `on-accent` | `#0B1220` | text on accent |
| `accent-bg` | `#17233A` | busy pill / info banner background |
| `info-text` | `#C9DCFF` | text on the info banner |
| `ok` | `#3DD68C` | done, saved |
| `ok-bg` | `#16261E` | |
| `warn` | `#F5B841` | waiting, needs a choice |
| `warn-bg` | `#2A2213` | |
| `warn-text` | `#F5D48A` | |
| `danger` | `#D93A41` | Stop run (hover `#E5484F`) |
| `danger-text` | `#FF8A8F` | destructive button text (Reset, Delete step) |
| `plate` / `plate-bg` | `#A99BFA` / `#221E35` | plates (labware icon, filled wells) |
| `planned` | `#3A3360` | planned but still empty wells |
| `tips` / `tips-bg` | `#4FC9B8` / `#15282A` | tip boxes, tip-rack wells |
| `dish` / `dish-bg` | `#EE93BE` / `#2C1A24` | dishes |
| `point` | `#4FC9E0` | memo points (M1, M2…) |
| `group-*` | `#4FC9B8`, `#EE93BE`, `#F0A35E`, … | liquid groups, in order |

### Type

- **UI font:** Inter, falling back to Segoe UI Variable / Segoe UI.
- **Numbers** (coordinates, Z, volumes, well IDs): JetBrains Mono, falling back
  to Consolas. Bundle both fonts with the app if licensing allows (both are
  OFL).
- **Sizes (px):**
  - 11: CAPS captions, 600 weight, letter-spacing 0.09 em;
  - 12–13: secondary;
  - 14–15: body;
  - 16–18: card titles;
  - 20–22: panel titles;
  - 28: page title;
  - 72: the one big readout (Z on Dish bottom).
- At most two sizes per area.

### Geometry

| Element | Size |
|---|---|
| Spacing grid | 4/8 px |
| Top bar | 64 px |
| Step bar | 56 px |
| Footer | 72 px |
| Side panel | 400–470 px wide |
| Buttons | 44 px high, radius 10 |
| Compact buttons | 30–40 px |
| Cards | radius 14–16 |
| Pills / status | radius 999 |
| Design reference size | 1440×900 (superseded by §12.6: 1920×1080) |

## 3. Shared components

Build these first (phase 1). Each is a widget in `gui/widgets/` and is used
everywhere it appears. Prototype names are in parentheses.

- **TopBar.**
  - Left: breadcrumb `⌂ Home › <Task>` (and `› <Sheet title>` while a sheet is
    open). There is no app name (the Windows title bar has it) and no back
    arrow.
  - Centre-right: **StatusPill**, shown only while the robot is doing
    something: "Picking", "Paused", "Waiting for you", "Calibrating the tip".
    It is visible from every screen including Home, and on Home a click on it
    goes back to the running task.
  - Right, in this order: **Light** (toggle, filled while on), **Lock**
    (toggle, filled while on; lock overlay §7.9), **Park** (moves the head to
    its parking corner; the arrow icon points to that corner; shows
    "Parking…" with `accent-bg` until done), a divider, then **⚙ Settings**
    (icon and word).
  - **No permanent red STOP** (users found it alarming). Esc stays the
    window-wide stop. See §11 for an open question.
- **StepBar.**
  - Numbered steps of the current task; a done step shows ✓ (`ok` on
    `ok-bg`); the current step is an accent dot on `selected-bg`.
  - Any step can be clicked. A step whose needs are not done opens dimmed,
    with one banner (§4).
- **Footer.** Right-aligned **Back** (outlined) and **Next** (primary, the
  word "Next" only). Next is enabled when the step is done or optional. There
  is no message text in the footer. On Picking the footer carries the run
  status (§6.5).
- **Toast.** Green, bottom centre, about 3 s, one sentence: the response to an
  action.
- **Card / FoldCard.** A FoldCard is a header row with a chevron that folds
  its body. In a group of FoldCards one is open at a time, by default the one
  needed next.
- **Segmented control.** 2–3 options; the selected one is a lighter surface.
  Example: In a well / At a point / By hand.
- **Switch.** Pill switch with a label, for on/off options ("Snap to found
  cuboids").
- **Select.** Custom-drawn chevron with right padding. The native arrow
  touched the border and looked broken.
- **MiniDeckDropdown.**
  - A button showing `Slot N · <labware>` opens a popover with the 3×4 deck,
    in the deck's real layout (10 11 12 / 7 8 9 / 4 5 6 / 1 2 3).
  - Valid targets (plates, or any slot for "Look at") are highlighted; the
    rest are dimmed.
  - Each tile may carry a status note ("Measured" / "Not measured").
  - Used in: Plan (plate), Liquid Plan (plate), Manual (Look at), anywhere a
    plate or slot is picked.
- **PlateGrid.**
  - Wells with row letters and column numbers.
  - Click a well to toggle it; click a row letter or column number to toggle
    the whole row or column; drag to box-select.
  - Undo, Redo, Select all and Clear sit above it.
  - No on-screen explanation of these gestures; it goes in the manual.
- **JogPad.**
  - XY arrows plus Z Up/Down, all the same size and font, with "XY" and "Z"
    labels.
  - Step size is a **carousel**: three visible sizes, ‹ › to slide. Sizes: 0.01,
    0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10 mm.
  - The **Keys** toggle shows keycaps on the buttons (←↑→↓, PgUp, PgDn) and the
    step-size keys ([ ]).
  - It wraps the existing `JogPanel` logic (`run_job`, envelopes, the travel-Z
    rules in `CLAUDE.md`); only the face changes.
- **CameraView chrome.**
  - It overlays only what is useful: the robot's current X/Y/Z (mono), found
    counts ("23 cuboids found"), and the contextual buttons for that screen.
  - It does **not** show camera name, slot, FPS, resolution or "TIP EMPTY"
    text.
  - The mouse help is one quiet row: "Left click move · Right click mark a
    point · Wheel zoom".
- **TipGauge.** A drawn tip with a fill level and "In the tip: 15 µl"
  (Manual). The diagram replaces "Aspirated 15 µl" messages.
- **SheetWindow (window-in-window).**
  - A near-full-screen panel over a dimmed page (24 px margin), with a title,
    × (close without saving), Cancel and Done (save).
  - Used for every Robot setup item. The breadcrumb gains the sheet's title,
    so the user knows they are still inside Robot setup.
- **ConfirmDialog.** Small, with the safe option as primary. Example: "Reset
  the setup?" with **Keep setup** (primary) and **Reset** (`danger-text`).
- **Banners.**
  - Info (`accent-bg`, `info-text`): e.g. "The robot remembers what was on it
    last time. Check that it matches the real deck." with "Got it".
  - Warn (`warn-bg`, `warn-text`), always with the action buttons in the
    banner.
- **LockOverlay.** While Lock is on, the page is covered at 82 % `bg` with a
  big lock icon, "Robot locked", "Nothing will move. Work with your hands
  safely." and **Unlock**. No robot command is sent while locked; enforce this
  in the session, not only in the UI.

## 4. Application structure and flows

### Home (replaces the Profile page as the start screen)

Three cards, top to bottom:

1. **Robot.**
   - The headline is the state: **"Robot connected"** (green ✓) or **"Robot
     not connected"** (yellow, not red), with "Please turn the robot on. It
     connects by itself; after power-on this can take a few minutes." and a
     "Looking for the robot" spinner.
   - There are **no Connect / Disconnect / New robot session buttons.** The
     app connects at start and monitors the connection (§8).
   - While not connected, everything below is dimmed with one line saying
     why.
   - After the robot was power-cycled, the card says: "The robot was turned
     off, so it forgot what is on the deck and whether a tip is on. You set
     them at the start of each task."
2. **Robot setup.**
   - "Setup is done" with "Robot setup ›": the whole card opens it.
   - Or "Complete the setup · 2 steps left: …" with a primary **Complete
     setup** button.
   - No profile name and no list of what is set.
3. **What are we doing today?** Three tiles with no icons: **Auto pick**,
   **Manual pick**, **Liquid**, each with one line of copy (draft in the
   prototype; final wording TBD). While a run is in progress they are dimmed:
   "The robot is busy. Go back to Auto pick to stop or finish it."

**Removed from Home:** the interrupted-run "resume" banner, profile chooser,
Operation checklist and task icons.

### Profiles become invisible

Users do not need several profiles.

- **Keep the profile infrastructure in code** (files, loading).
- **Auto-load the last profile at start.**
- **Remove profile choose / create / copy / delete from the UI.**
- Robot setup gets a **Reset setup** button that clears cameras, calibration,
  platforms and positions, after a ConfirmDialog. Keeping a backup copy on
  disk before resetting is cheap and recommended, but it is not shown in the
  UI.

### Tasks are flows of steps

| Task | Steps |
|---|---|
| Auto pick | Deck → Tip → Plan → **Dish bottom** → Picking |
| Manual pick | Deck → Tip → **Dish bottom** → Work |
| Liquid | Deck → Tip → **Well center** → Plan → Run |

- **Deck, Tip and Dish bottom are shared** between tasks: the same screens and
  the same state. Doing Auto and then switching to Manual shows Deck, Tip and
  Dish bottom already ✓, and Manual opens on Work.
- **Liquid's Tip step has no calibration** (liquid does not use the camera).
  The step is done when any tip is on.
- **"Done" is computed from session state, never stored as "visited".** Reuse
  `gui.readiness` (make the status function public).
  - **Deck** is done once the user pressed Next on it **in this robot
    session**: the robot remembers labware from the last session, so "it is
    there" is not "the user checked it". It also needs `deck_problems()` to be
    empty.
  - **Tip (Auto, Manual)** is done when the tip calibration is valid for the
    tip that is on (`readiness.tip_calibration_status`).
  - **Plan** is done when a plan has been confirmed with "Use this plan".
  - **Dish bottom** is done when it was measured or "Use it" (last value) was
    pressed in this session.
  - **Well center** is done when every plate used by the plan is either
    measured or explicitly skipped ("use the drawing").
- **Needs, and opening a step early:** a step may need earlier steps or
  Robot-setup items (Tip needs the camera calibration; Picking needs Deck,
  Tip, Plan, Dish bottom, the position over the dish and the shake position).
  Opening a step whose needs are not done:
  - the page is shown dimmed;
  - one banner names **only the first** missing thing, with a button that
    goes there (e.g. "Put a tip on first. [Go to Tip]").
- **Home during a run:** allowed. The run continues and the StatusPill shows
  it. Anything that would move the robot is disabled while a run is active.
- **Settings and Log** are reached from ⚙ and are outside the flows. The Log
  moves into Settings or a menu entry under ⚙.

## 5. Robot setup (one page and sheets)

**Layout:**
- Page title "Robot setup" and **Reset setup** on the right. No description
  text.
- Three blocks of rows. Each row: status mark (green ✓, or a yellow ring if
  not set), label, value, ›. Clicking a row opens its SheetWindow.

**Rows:**

- **Cameras:**
  - **Upper camera** (e.g. "20 MP USB camera");
  - **Camera calibration**, on the same level, not indented ("Done 3 days
    ago");
  - a divider;
  - **Lower camera** ("Arducam").
- **Platforms:** one row, e.g. "Picking platform in 5 and 8 · Calibration
  module in 9".
- **Positions.** Each value is the approximate slot plus coordinates, e.g.
  `≈ slot 8 · X 245.1 Y 132.4 Z 80.0`. Never internal names.
  - **Position over the dish**;
  - **Shake position**;
  - **Calibration position** (new, §8).

**Dish bottom is not in Robot setup.** It is a step of Auto and Manual.

**Sheets:**

- **Upper / Lower camera:**
  - left: live picture;
  - right: "Which camera is the upper camera?" with a Select of the attached
    cameras. "The picture on the left is live. Pick the camera whose picture
    matches."
  - Done saves (existing `Session.assign_camera_devices`).
- **Camera calibration** (confirmed flow):
  1. **Place the marker** on a platform (picking platform or calibration
     module; any slot) → "It is in place".
  2. **Which slot is it in?** Select of the platform slots → "Move the camera
     there". The camera goes over that slot.
  3. **Center the camera on the marker**: click the marker on the picture or
     use the arrows until it is in the middle → "It is centered". The picture
     says "MARKER FOUND" / "NO MARKER IN VIEW" (existing marker detection).
  4. **Calibrate** → "Start calibration". The sweep runs by itself (about
     2 min; nothing to do meanwhile) with a progress bar and "Don't touch the
     robot."
  5. **Result.**
     - Good: green "Camera calibrated and saved."
     - Poor: yellow "Not accurate enough: the marker was off center." with
       **Redo calibration** (primary) and "Save anyway" (quiet).
- **Platforms:** a big 3×4 deck. Click a slot, then "What stands in this
  slot?" (Picking platform / Calibration module / Nothing). One line: "Platforms
  only differ in height."
- **Position over the dish:**
  - "Where will the dish with cuboids stand?" with the choices "Slot 8, on the
    picking platform" (default) or "Somewhere else", then "Move camera to
    slot 8".
  - Click the picture to move the camera; the dish rim should surround the
    crosshair. Done saves the position (existing `observe`).
- **Shake position:** "1. Put the dish in its holder. 2. Lower the tip into
  the hole of the holder.", a yellow note "The camera can't see this. Watch
  the robot itself.", and a JogPad.
- **Calibration position (new):** "1. Put the crosshair disc on the
  calibration module. 2. Click the picture or use the arrows until the
  crosshair is in the middle. 3. Press Done." Saved like the other positions.

## 6. Task screens

Every task screen uses TopBar + StepBar + content + Footer. Content is
**object on the left** (camera, deck or plate) and **actions on the right**
(side panel).

### 6.1 Deck (shared step 1) — existing `labware` page logic

**Left: the deck,** 3×4 tiles in the real layout.
- Each tile shows "SLOT N" in caps, a platform chip if the profile has one
  there ("Picking platform", dashed tile border), and the labware: colored
  icon, name and one line of facts (wells · volume · shape), or "Empty".
- **Trash:** no icon and no border; a large, faint "TRASH" on a striped
  background. It must not look clickable.
- **Tip-box icon:** a single pipette tip (the old icon was unclear).
- Info banner about the robot's memory, with "Got it".
- Below the deck: "Drag an item to another slot to move it." (drag-and-drop
  between slots).

**Right:**
- Title "What goes to slot 7?" (empty slot) or "In slot 7" (filled).
- Empty slot: search, chips (Frequent · Plates · Tips · Dishes), list items
  with icon, name and facts (always show well volume: two Corning 384 plates
  differ only by it), and + to add.
- Filled slot: the item card, **Remove**, and "Put something else".
- At the bottom, an outlined button **Change platforms in Robot setup**.

### 6.2 Tip (shared step 2) — existing `calibration_pipette` page logic

**Left: camera** with X/Y/Z only, and a JogPad.

**Right, card "Put a tip on":**
- **Tip box:** a Select of the tip boxes on the deck, e.g. "VWR tips, 200 µl ·
  slot 10". The slot is known from the deck.
- "Click the tip to take." and the 8×12 rack (used wells grey, chosen well
  ringed).
- **Take tip A3**, a fixed-width button that does not resize for "F12".
- Once on: "Tip A3 is on." with **Drop in trash** and "Put back".
- No tip box on the deck: a warn banner "There is no tip box on the deck. Add
  it on the Deck step." with **Back to Deck**.

**Right, card "Calibrate the tip"** (confirmed flow). Its steps are visible
from the start and dimmed until a tip is on:
1. **Start**: "The camera goes to the calibration position." → **Start tip
   calibration**. The robot moves; the user must not be placing the disc
   while it moves, so the disc is checked in step 2.
2. **Disc on the calibration module**: "Check on the picture that the
   crosshair disc is there. If not, put it on now." → "The disc is there".
3. **Calibrate**: "The robot finds the tip under the camera by itself.
   About 30 s." → Calibrate. This is the existing automatic routine: upper
   camera finds the crosshair, the tip goes there with an offset, the lower
   camera sees tip and crosshair and corrects.
4. **Fine-tune by hand** (optional): nudge with the JogPad, then **Save** or
   **Skip**.

When done: green "Tip calibrated." with "Check: tip to crosshair" (the old
Manual "Targets" check, moved here).

**Removed:** dx/dy coarse offset (moves to Settings), the "Details" button,
"A1 is the top-left corner", and the camera name chips.

### 6.3 Plan (Auto step 3) — existing `routine` page logic

**Left:**
- **MiniDeckDropdown** for the plate.
- Toolbar: Undo, Redo, Select all, Clear.
- **PlateGrid** (16×24 for 384), with no gesture explanation.

**Right:**
- **Start from:** two tiles, **A template** (saved earlier) or **New plan**
  (choose wells).
- **Template mode:**
  - "Saved templates" list in its own box about 3 rows high that scrolls
    inside itself. Items show the **user's name** for the template and a fact
    line ("24 wells · saved Oct 20").
  - "Open from file…" opens a file picker. Templates are files in the outputs
    folder; this allows sharing them.
- **New mode with no wells:** info line "Choose the wells on the plate."
- **Cuboids per well:** stepper.
- **Fill order:** "Row by row" or "Column by column", as tiles with a small
  arrow drawing.
- **Summary box:**
  - "24 wells × 1 cuboid", or "No wells chosen yet."
  - **Use this plan** (primary); after use, a green "Plan ready".
  - New plan: **Save as template** (asks for a name inline).
  - Template that was edited: **Save changes to "<name>"**. No "are you
    sure" dialogs.

**Removed:**
- "Whole row / Whole column / Drag a box / One by one" tool buttons;
- "Also save as a template" checkbox;
- "Your label for physical plate";
- the run-history file in the UI. An unfinished run is detected silently and
  offered on Picking.

### 6.4 Dish bottom (shared step: Auto 4, Manual 3) — moved out of Picking

**No camera on this screen** (the user watches the tip with their eyes).

**Left:** a large **Z readout** (72 px mono). Below it, big **Up** and
**Down** buttons with the step-size carousel between them. They are active
only during step 2.

**Right:**
- Title "Find the dish bottom".
- "Last time: Z 66.40 mm · 3 days ago" with **Use it**.
- Steps:
  1. **Tip over the dish center**: "The tip goes to the middle of the dish
     and stops a little above the bottom." → Move the tip.
  2. **Lower until it touches**: "Go down until the dish bottom bends a
     little. Then go up one step, make the step smaller and lower again." →
     **Done**.
  3. **Save** → **Save dish bottom** (no extra text).
- Result: green "Dish bottom: Z 66.42 mm".

### 6.5 Picking (Auto step 5) — existing `picking` page logic

**Left: camera picture of the dish.**
- Overlay "23 cuboids found".
- At top right, **Camera to dish center**. It works only while paused, because
  during a run the tip must stay over the dish; while running it shows "Pause
  to move the camera".
- **Removed:** "Upper camera · dish" and "Light off while picking".

**Right, top to bottom, with no panel scroll:**
1. **Analyze the dish** and **Shake the dish** (two buttons side by side).
2. FoldCard **Cuboid sizes** (summary "250–500 µm").
   - The histogram with two draggable white limit lines: "Drag the white
     lines to change which sizes are picked."
   - "0.5 mm above the bottom · 15 µl", and **More settings**, which opens the
     existing picking-settings dialog.
3. FoldCard **Plate · slot 5** (summary "12 of 24 wells").
   - A mini map of the **whole** plate in its real shape, with legend Filled
     (`plate`) / Planned (`planned`). The current well is ringed.
   - "Missed" is not shown.
   - Only one FoldCard is open at a time: Sizes before the run, Plate during
     it.
4. When done: a card "2 wells were not filled — A7 and A18" with **Finish by
   hand** (opens Manual on Work with everything carried over).

**Footer = run status:**
- A colored CAPS label (READY / PICKING / PAUSED / WAITING FOR YOU / DONE /
  NOT READY) and one sentence of what the robot is doing, e.g. "Well A13 of
  24 · taking a cuboid" or "No cuboids found after 2 shakes. Add cuboids, or
  finish by hand." The robot's own messages go here, and the retry count must
  match reality.
- Buttons:
  - ready: Back + **Start picking**;
  - running: **Pause** / **Resume** (one button, yellow while paused) and
    **Stop run** (red, `danger`).
- Start asks nothing extra beyond the existing confirmation.

**Waiting for the user:** superseded by §12.5 (no light pulse).

### 6.6 Manual pick: Work (Manual step 4) — existing `manual` page logic

The user saw Manual as **a toolbox, not a script**: the user composes their own
workflow from aspirate and dispense actions. The screen guides by opening the
section needed next, but nothing is forced.

**Left: camera.**
- Top-left overlay: **TipGauge** ("In the tip 15 µl") and X/Y/Z.
- Top-right: "A click moves the **[Camera | Tip]**" (segmented) and **Camera
  back to center** / **Tip back to center** (the label follows the selection).
- Bottom-left: the one-line mouse help.
- Memo points M1… are drawn **only while "At a point" is selected** in
  Aspirate or Dispense.
- "Show past picks" draws earlier pick spots: the last one bright, older ones
  faint.

**Right, top to bottom:**
1. **Analyze the dish** and **Show past picks** (toggle, filled while on).
   This is the same place as on Picking.
2. **Look at:** a MiniDeckDropdown labeled "Slot 8 · Petri dish". Choosing a
   slot moves the **camera** there; nothing is aspirated.
3. **Aspirate** (FoldCard):
   - **Settings line**, folded: "0.5 mm above the bottom · 15 µl · 50 µl/s",
     with **Change** to unfold the fields (height above the bottom, volume,
     speed). The height is relative to the measured dish bottom.
   - **From:** segmented control.
     - **In a well:** plate and well Selects.
     - **At a point:** the list of points, each with × to delete, and "Right-
       click the picture to add a point." Points must be deletable; today they
       cannot be.
     - **By hand:**
       - Switch "Snap to found cuboids": a click snaps to the nearest detected
         cuboid. Off: the click point itself is used, which is how plain
         liquid is taken.
       - Switch "Aspirate as soon as I click".
       - Hint "Click the spot to aspirate from on the picture." Once a spot is
         chosen: "Cuboid selected. Press Aspirate."
   - **Aspirate** (primary). Enabled only when a source is chosen. No
     "Aspirated 15 µl" toast; the TipGauge is the response.
4. **Dispense** (FoldCard, under Aspirate; it opens by itself when the tip
   holds something):
   - **How much:** All | Part, with a µl field for Part.
   - **Where:**
     - **In a well:** plate and well Selects plus a **mini map of the plan's
       plate** (Filled / Planned, still empty), so missing wells after Auto
       are visible. A click on a well selects it.
     - **At a point:** the points list.
     - **Here:** where the tip is now.
   - Button: "Go to A7 and dispense" or "Dispense here". Enabled only when the
     tip holds something.

**Removed:**
- step numbers "1 / 2" on the cards;
- "Take a cuboid" / "Take it" / "Where it goes";
- the moved-cuboids counter;
- Finish;
- the camera-name chips;
- the "Targets / crosshair Z" card (moved to Tip);
- the Click-to-move checkbox (left click moves, right click marks a point);
- "Nothing to release yet" text (the button is grey instead).

### 6.7 Well center (Liquid step 3) — from Liquid's / Manual's "well center"

Purpose: for each plate the plan uses, measure the real well center and the
bottom (needed when aspirating near the bottom).

**Left:** camera over a well, with X/Y/Z and a JogPad.

**Right:**
- Title "Well center and depth". One line: "Measure every plate you will use.
  A plate you skip still works, with the depth from its drawing."
- **List of the plates on the deck,** with status:
  - "Measured today" (green ✓);
  - "Not measured: using the drawing" (✓ with yellow text);
  - "Not measured yet" (empty ring).
- **Steps for the selected plate:**
  1. **Tip to a well:** well Select → Move the tip.
  2. **Center the tip:** arrows until the tip is in the middle → It is
     centered.
  3. **Lower to the top of the well:** until the tip end is level with the top
     of the plate → It is level.
  4. **Well depth:** a field defaulting to the labware drawing ("10.67 mm,
     from the plate's drawing") → **Save**. The robot computes the bottom as
     top minus depth.
- **Skip this plate, use the drawing:** marks it skipped (✓, yellow text).

The step is done when every plate the plan uses is measured or skipped.

### 6.8 Liquid Plan (Liquid step 4) — existing `liquid` page logic; renamed from "Program"

**Left:**
- MiniDeckDropdown for the plate. Tiles show "Measured" or "Not measured".
- Choosing a plate that is not measured shows a **warn banner** "The well
  center of this plate was not measured. The robot will use the plate's
  drawing." with **Measure now** (goes to Well center) and **Use anyway**.
  Not blocked: the user decides consciously.
- Toolbar: Undo, Redo, Select all, Clear.
- PlateGrid colored by group.
- "N wells selected" and **Make a group**.

**Right:**
- **Start from:** A template / New plan, and the templates box. This is
  **identical in behavior to Auto Plan** (§6.3).
- **Take liquid from:** In a well / At a point / By hand. By hand means the
  robot waits for the user to bring the tip, then aspirates.
- **Groups, in the order they run** (drag to reorder). Each group is a
  FoldCard; all can be folded.
  - Each step is **one summary line** ("Aspirate 200 µl · fast · 2 mm above
    bottom") with **Edit**.
  - Edit unfolds Volume (µl), Speed (Slow / Normal / Fast), and Height: a
    reference (Top / Center / Bottom / Measured bottom) plus an offset in mm,
    with a tiny well drawing.
  - The unfolded step also has **Try once** and **Delete step**
    (`danger-text`).
  - "+ Aspirate", "+ Dispense", "+ Mix", "More…".
- **What to run:** "All groups, one after another" or "Only one group".
  Today it silently runs all, which surprised users.
- Summary: **Use this plan**, then "Save as template" or "Save changes to
  "<name>"".

### 6.9 Run (Liquid step 5)

Not drawn yet. Build it like the Picking footer and status:
- left: the plate with progress;
- footer: status label and sentence, Pause / Resume, Stop run (red).

## 7. Cross-cutting behaviors

1. **Auto-connect:** at start, connect (continue the robot's run or start one,
   as Connect does today). Then poll the connection every few seconds; Home
   and the TopBar reflect it.
2. **Start new robot session / Disconnect** leave the main UI. If kept at all,
   put them under Settings › Advanced.
3. **Light / Lock / Park** live in the TopBar on every screen with the states
   of §3. Lock must block robot commands in `Session`, like `hold_robot`.
4. **Esc** stays the window-wide stop (existing two-press behavior).
5. **Keyboard:** every action keeps its `gui.keys` binding. The Keys toggle on
   JogPads shows them.
6. **Toasts** replace the next-step hint and the footer messages.
7. **Copy** is American English, short, no internal names. Show the
   **robot's own status messages** in the run status line, translated to human
   words.
8. **Templates** (Auto Plan and Liquid Plan) are named files. "Open from
   file…" and a saved list behave the same in both.
9. **Lock overlay** as in §3.

## 8. New logic needed (separate small commits, before the screens that use them)

1. **`calibration` position in the profile:** a stored pose like
   `observe`/`shake`. The tip calibration starts by moving the camera there
   instead of asking for the disc slot.
2. **Connection monitor:** a periodic probe and a signal, plus a "robot was
   power-cycled" flag when the run id changes. Labware and tip are then
   unknown, and Deck confirmation is reset.
3. **Deck confirmation per robot session** (§4).
4. **Park state:** a busy signal while the park move runs.
5. **Reset setup:** clear cameras, calibration, platforms and positions (keep
   a backup copy on disk).
6. **Templates as named files** for routines and liquid programs, with a
   save-changes vs save-as distinction.
7. **Well center per plate with a "skipped" state,** and a well-depth entry
   that defaults from the labware definition.
8. **Dish bottom:** "use last value" in this session, and the measurement
   steps (moved from Picking).
9. **Memo points:** delete one.
10. **Manual dispense:** Part (µl) vs All, and "Here".
11. **Step-size list:** 10 values for the JogPad carousel.
12. *(Dropped, §12.5.)* ~~**Light pulse** when waiting for the operator longer than N minutes, with
    the minutes in Settings.~~
13. **Run-status messages:** expose a short human sentence per state and fix
    the retry counter so it matches reality (observed: 2 shakes reported as 3).

## 9. Implementation plan (phases)

Each phase is a set of commits on a branch `redesign`, from `gui-0.5` (§12.1). Run
`--mock` after each phase and write its bench checks.

1. **Theme and components.**
   - Fixed dark palette (§2) and fonts.
   - Then each §3 component as a widget, with a tiny gallery (`python -m
     micropick.gui.gallery --mock`) showing all of them in their states.
   - No page changes yet.
2. **Shell and flows.**
   - TopBar, StepBar, Footer, Home, flows and steps, "done" from state, needs
     with banners, page reuse (see the earlier architecture prompt, now
     folded into §4).
   - Old tabs, NextStepHint and the Operation checklist on Home go away.
3. **Robot setup page and sheets** (§5), including Reset and the calibration
   position (§8.1, §8.5).
4. **Deck and Tip** (§6.1, §6.2), with Deck confirmation (§8.3).
5. **Plan and Dish bottom** (§6.3, §6.4), with templates (§8.6) and dish
   bottom (§8.8).
6. **Picking** (§6.5), with the run-status line, light pulse and fold cards.
7. **Manual Work** (§6.6), with memo delete, dispense part/all and Look at.
8. **Liquid: Well center and Plan** (§6.7, §6.8), then Run (§6.9).
9. **Cleanup:**
   - remove dead pages and widgets;
   - move dx/dy, Start new robot session and Log to Settings;
   - update `CLAUDE.md` (§10), the CHANGELOG and the bench checks.

## 10. `CLAUDE.md` decisions this redesign replaces

Update these in `CLAUDE.md` in phase 9, and earlier where a phase depends on
them, so later sessions do not restore the old design.

- **The theme no longer follows the OS.** `theme/__init__.py` says
  `micropick.qss` "contains no colours" and the app follows the OS light/dark
  setting through qdarktheme. Now:
  - the app is always dark with the §2 palette;
  - use `qdarktheme.setup_theme("dark", custom_colors={...})` for the base;
  - colors may be defined in our QSS or in a Python token module that
    generates it (preferred: `theme/tokens.py` as the single source).

  `accent()` now returns the fixed token.
- **"Camera view chrome"** (tab row; Stop at the right of the status bar; the
  camera window toggled from the status bar) is replaced by TopBar + StepBar +
  Footer.
- **"Manual control drives the robot from the picture":**
  - a single left click moves (not a double click);
  - right click marks a point (not the "Mark points" checkbox);
  - memo points can be deleted.

  The travel-Z and `run_job` rules **stay**.
- **"The next step of the Operation checklist floats…"** is removed; the
  StepBar and banners replace it.
- **The Operation checklist** is no longer shown on its own page. The Picking
  page's own gating stays.
- **"Stop is the status bar's red button and Esc":** the button is gone and Esc
  stays. See §11.
- **The Profile page** "is Robot and Load profile…" is replaced by Home and
  Robot setup.
- **Tip calibration page:** the steps change to §6.2 (calibration position,
  then disc check, then calibrate, then optional fine-tune).
- **"Words on screen":** still short and neutral, and now **no internal
  names** at all (`observe`, robot session, `tip_calib`).

## 11. Open questions (all answered in §12)

1. **Emergency stop button.** Users found the permanent red STOP alarming and
   removed it. Esc remains, but on a touch screen with gloves Esc is not
   reachable, and the per-screen Stop run exists only on Picking and Run.
   Proposal from design: show a STOP button in the TopBar **only while the
   robot moves**. Needs confirmation from Ivan.
2. **Final copy** for the Home task tiles and some descriptions. Prototype text
   is a draft.
3. **Liquid Run screen** (§6.9) is not drawn; confirm the layout before
   building.
4. **Numbers inside mini-plate wells** when more than one cuboid goes per well:
   discussed, not decided.

## 12. Decisions from review, 2026-10-26

Answers given by Ivan while the spec was read against the code. Where this
section and anything above disagree, this section wins.

1. **Branch.** `redesign` from `gui-0.5`, not `main` (`main` has no GUI).
   One phase at a time; after each phase tests are green, its bench checks
   are written, and work stops for review.
2. **Calibration position (§5, §8.1) already exists**: it is the profile
   position `tip_calib`, the disc position the tip calibration starts at. No
   new pose and no migration: only its UI moves from the Tip page to a
   Robot setup sheet.
3. **Emergency stop (§11.1).** A red STOP shows in the TopBar only while a
   **run** has the robot: picking, liquid, the camera calibration sweep, the
   tip calibration, pauses and waits for the operator included. Short jobs
   (a jog step, Park, Go to, Move the tip) do not show it. It does what Esc
   does (first press stop, second press hold).
4. **Lock (§3, §7.3)** is a state in `Session`. While on, the robot wrapper
   refuses every command that moves (like `RobotHeld`) and a claim of the
   robot's lease is refused with "Robot locked.". It cannot be turned on
   while a run has the robot (grayed, tooltip names the holder). Light still
   works while locked.
5. **No light pulse (§6.5, §8.12 dropped).** While a run waits for the
   operator: the yellow WAITING FOR YOU status in the footer and the
   StatusPill, and the window flashes in the Windows taskbar
   (`QApplication.alert`). No sound, no setting.
6. **Screen and sizes.** The bench screen is 1920×1080, mouse and keyboard,
   no touch. Screenshots and the prototype are a guide, not to be copied
   pixel for pixel. Sizes for a mouse:
   - buttons 36 px high (radius 8), compact buttons 28 px, inputs 32 px;
   - jog pad buttons 40 px;
   - top bar 52 px, step bar 48 px, footer 60 px;
   - cards radius 12;
   - side panel 400–520 px; minimum window 1280×800.
7. **Clicks.** A single left click on the picture moves (inside the jog
   envelope), a right click marks a point, as §6.6 says.
8. **Profiles (§4).** At start the last used profile is loaded; with none, a
   profile `default` is created silently. **Reset setup** asks "Reset the
   setup?" first, then archives the profile folder (dated, beside it) and
   clears cameras, camera calibration, platforms and positions. The deck
   (labware) and the picking settings are kept.
9. **Names.** A Plate plan (code `Routine`) and a Liquid plan (code
   `core.liquid.Program`) are both "Plan" on screen. A **Template** is a
   named file of either: `Outputs/templates/plate-plans/` and
   `Outputs/templates/liquid-plans/`. Existing plan presets move there.
10. **Skipped plate (§6.7)** is remembered for the robot session only, in
    memory, like the Deck confirmation and Dish bottom's "Use it".
11. **Open questions §11.2–11.4.** Home tiles use the prototype's draft
    copy. Liquid Run is built as §6.9 sketches it. A mini plate shows a
    number in a well only when more than one cuboid goes per well.
12. **Tests.** When a page is laid out anew, the tests of its logic move
    with it and the tests of the old layout go with the old page. Each
    new component gets pytest-qt tests of its states.
13. **Fonts.** Inter and JetBrains Mono are bundled in `gui/theme/fonts/`,
    registered with `QFontDatabase`, and included in the build.
14. **Park** is today's "Home robot position" (all axes home, back right),
    renamed; its icon points to the back right. "Home" on screen is only
    the Home screen.
15. **Settings** opens from ⚙ as a sheet, with today's Settings sections
    (Robot address, Folders, Keys, Picking camera, Models, Tip calibration
    with dx/dy) plus **Advanced** (Start new robot session, Disconnect
    robot session) and **Log**. The camera choice leaves Settings for Robot
    setup.
16. **Liquid: "Only one group" (§6.8)** is a parameter of
    `workflows.liquid` (which groups run), added in its own commit.
17. **Deck confirmation (§8.3)** is cleared by any change to the deck; it
    comes back only with Next on Deck.
