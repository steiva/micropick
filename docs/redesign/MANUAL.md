# Manual pick: Run screen, camera chrome, Retract — redesign specification

Status: agreed design, ready to build. Date: 2026-11-05.
Audience: Claude Code (implementation) and Ivan (review).

This document does two things:

1. **It replaces §6.6 of `REDESIGN.md`** ("Manual pick: Work", now the step
   **Run** of the Manual flow) with §3–§6 below.
2. **It changes shared parts of the app** (§2). These apply on **every**
   screen, not only on Manual pick:
   - the camera picture's overlays;
   - the Controls (jog) overlay;
   - the always-visible step size;
   - the **Retract** button in the TopBar;
   - one points list;
   - some names.

When you start, edit `REDESIGN.md` so the two documents agree:

- in §6.6, write "Replaced by `MANUAL.md`";
- in §3 (TopBar, CameraView chrome, JogPad), add a line under each
  component: "Changed by `MANUAL.md` §2".

---

## 0. How to use this document

**Sources of truth, in order:**

1. **This document.**
2. **The prototype:** `docs/redesign/prototype/ManualPick.dc.html`. It is
   canvas HTML that does not run here. Read it for layout, sizes, colors,
   copy and states.
   - It is drawn at **1920×1080**, the bench monitor, at 100 % Windows
     scaling, so its pixels are the app's pixels.
   - The `scenario` tweak at the bottom lists the states the designers
     reviewed.
   - Where the prototype and this document disagree, this document wins.
3. `REDESIGN.md` (tokens §2, components §3), then `CLAUDE.md`.

**§10, decisions from review, overrides everything above it here.**

**Why this rework exists.** The first implementation of §6.6 had everything,
but users and Ivan found it chaotic. The problems on the 0.4.0 screen:

- **Actions sit in five places with no visible logic:**
  1. over the picture;
  2. above Aspirate;
  3. in the Aspirate and Dispense cards;
  4. in Points;
  5. in a separate Machine card.

  Analyze sat next to "Look at", and Home sat away from the jog pad.
- **Everything looks different:** each part is drawn by a different widget.
  Qt tables, camera overlays, cards and checkboxes each have their own font,
  border and height.
- **Long settings push the action button off screen.** Opening *Change* in
  Aspirate (Retract, Delay, Retract slowly) pushes the Aspirate button below
  the fold.
- **The "Here" actions are hidden.** The most used actions, aspirate or
  dispense where the tip is (keys A and D), are the fourth option in a
  segmented control.
- **The Points actions are ambiguous.** "Camera to / Tip to / Z from tip" sit
  under the tables, so it is not clear which point they act on.

**Ground rules (as in `REDESIGN.md` §0):**

- This is UI work. `workflows/manual.py`, `JogPanel.run_job`, the travel-Z
  rules, `Session.memo_points` and `profile.positions` keep their behavior.
- §7 lists the few pieces of new logic. Each one is a separate small commit
  with tests, before the UI that uses it.

---

## 1. Layout of the Manual pick Run screen (1920×1080)

```
┌ TopBar 64 ─ ⌂ Home › Manual pick ............ Light  Lock  Park  Retract | ⚙ Settings ┐
├ StepBar 56 ─ ✓Deck ─ ✓Tip ─ ✓Well center ─ ✓Dish bottom ─ (5) Run ────────────────────┤
│ ┌──────────── camera picture (4:3) ─────────────┐ ┌ column 1, 400 ┐ ┌ column 2, 400 ┐ │
│ │ [tip · XYZ · step]      [Look at][Detect]     │ │ In place      │ │ Points        │ │
│ │                         [layers][📷]          │ │ ① Aspirate    │ │  list         │ │
│ │                         [23 cuboids found]    │ │ ② Dispense    │ │  …            │ │
│ │                                               │ │               │ │ [at crosshair]│ │
│ │ [mouse help]                    [Controls ▾]  │ │               │ │ New points Z  │ │
│ └───────────────────────────────────────────────┘ └───────────────┘ └───────────────┘ │
├ Footer 72 ─ "2 wells filled today · Slot 2 · Corning 96" ............ Back  [Finish] ──┤
```

- **Body:** padding 16 px vertical and 20 px horizontal; gaps of 16 px
  between the picture and the columns.
- **The picture is 4:3, as on every screen (`TIP.md` §2.1), in the width the
  columns leave.** The columns keep their width, so at 1920×1080 it is about
  1048 × 786, not the full body height. It is drawn with the existing
  `FeedRow` and `CameraView`, letterboxed in `bg`.
- **Column 1 (400 px), the work:**
  - **In place** card;
  - **Aspirate** FoldCard;
  - **Dispense** FoldCard;
  - **Retract** card, pinned at the foot, outside the scroll, in the same place
    whatever folds or scrolls, in one column too (#136).

  It scrolls vertically only if it must. At 1080 px, with Dispense open, the
  lifting row goes under the scroll: accepted for the Retract card.
- **Column 2 (400 px):** the **Points** card, full height. There is **no
  Machine card any more**: Home is Park, and Retract is in the TopBar (§2.1).
- **Narrower windows:** under about 1600 px of window width, the two columns
  become one column of 420 px, with Points as a FoldCard after Dispense
  (folded by default). Do not shrink the cards below 380 px.
- **Footer:**
  - on the left, a one-line status: "*N* wells filled today · Slot 2 ·
    Corning 96" (§7.3); leave it empty when nothing was filled;
  - on the right, **Back**, then **Finish** (primary), which goes Home.

---

## 2. Shared changes (all screens)

### 2.1 Retract in the TopBar

- **Place:** in the TopBar, right after **Park** and before the divider and
  Settings, on **every** screen including Home:
  `Light  Lock  Park  Retract | ⚙ Settings`.
- **Look:**
  - the icon is an up-arrow over a line, stroke 1.8 like the others;
  - the label is "Retract";
  - while it runs, it shows "Retracting…" on `accent-bg`, like Park's
    "Parking…".
- **What it does:** exactly what **Retract Z** does today. It raises the tip
  to the travel height (`workflows.manual.raise_tip` / `travel_z`, the rules
  in `CLAUDE.md`).
  - It runs through the shown page's `JogPanel.run_job` when the page has
    one. Otherwise it uses the same path the status-bar *Home robot
    position* uses today.
  - It is refused while the robot is busy (`MainWindow._robot_busy`). The
    refusal is a toast "The robot is busy.", never a dialog.
  - It is disabled while Lock is on.
- **Removed:**
  - the *Retract Z* button on Manual;
  - the *Machine* card with *Home robot position*. **Park** covers Home.
    Park's behavior does not change.
- **Keyboard:** the `gui.keys` action `retract`, R by default (#136), window-wide;
  rebound or cleared on the Settings page. A field with the keyboard keeps it.
- **Manual pick's Retract card** (#136) does the same by the same path, gray
  when this button is, its key drawn on it as bound.

### 2.2 Camera picture chrome (every screen with a camera picture)

This replaces the "CameraView chrome" bullets of `REDESIGN.md` §3. It
applies to Manual pick, Picking, Dish bottom, Well center, Tip calibration,
Camera calibration and the Liquid point picker.

**One overlay style for everything drawn on the picture:**

- background `rgba(12,14,17,.84)`, border 1 px `rgba(255,255,255,.08)`;
- radius 9 px for chips and buttons (36 px high, 32 px for the small ones)
  and 12–14 px for panels;
- text Inter 13 px, weight 600; numbers JetBrains Mono.

Native checkboxes, Qt default buttons and the blue "Step 1 mm" pill go away.
Overlays are drawn by `CameraView` in widget pixels (`CLAUDE.md`, "Camera
view chrome"), never into the frame.

**Fixed places, the same on every screen:**

| Corner | What | Notes |
|---|---|---|
| top left | **Status plate:** drawn tip with "IN THE TIP · 10 µl" (only on screens that handle liquid), then X / Y / Z (mono, one per line), then **STEP · 1 mm** (mono, `accent-hover` color) | dividers between the groups; the step is always visible (§2.4) |
| top left, under the plate | the small X/Y axis glyph, as today | unchanged |
| top right, row 1 | **Look at** `Slot 4 · Dish ▾` (mini-deck popover: "Move the camera to"), then the page's main camera action, if any (e.g. **Detect cuboids**) | |
| top right, row 2 | **layer toggles** as chips: *Crosshair*, *Points*, *Past picks* (only where past picks exist), then the camera button (save a picture; icon only, `aria-label`) | a toggle that is on is filled `text` on dark text, like Light in the TopBar |
| top right, row 3 | the result of the main action: "23 cuboids found · 9 in size" (`ok` text) | only after Detect |
| bottom left | **mouse help**, one row: "**Left click** move · **Right click** add a point · **Wheel** zoom · **Middle-drag** pan" | the words change with the mode, e.g. "move and aspirate" |
| bottom right | **Controls** overlay (§2.3) | |

"Show past picks" stops being a button and becomes the *Past picks* layer
toggle. The crosshair and points checkboxes become layer toggles.

### 2.3 Controls overlay (the JogPad on the picture)

This replaces "JogPad" in `REDESIGN.md` §3 wherever the pad sits on a
picture.

- A panel in the bottom-right corner, 340 px wide, overlay style, radius 14.
- **Header row:**
  - **▾ Controls** (a fold button; the chevron turns −90° when folded);
  - on the right, **Undo step** and **Keys**. Keys is a toggle: filled while
    keycaps are shown.

  The old title "MOVE THE ROBOT" is renamed "Controls".
- **Body (when open):**
  1. **Step** ‹ 0.5 **1** 2 › mm: the 10-value carousel from `REDESIGN.md`
     §8.11. The current value is on `accent`. With Keys on, ‹ shows `[` and
     › shows `]`.
  2. A row with the XY cross on the left (3×3 grid of 48×44 buttons, with
     "XY" in the middle cell) and the Z column on the right (**Up**, "Z",
     **Down**, 64 px wide).
  3. With Keys on, each button shows its key under the icon (←↑→↓, PgUp,
     PgDn, as bound in `gui.keys`).
- **Folded:** only the header row stays. The folded or open state is
  remembered for the session and shared by all screens.
- **Nothing else goes in this panel:** no Home, no Retract, no Lift. Those
  are TopBar buttons.
- It wraps the existing `JogPanel` logic unchanged (`run_job`, envelopes,
  travel-Z rules, typing focus rule).

### 2.4 Step size always visible

- The current jog step is always shown in the status plate (§2.2) as
  "STEP · 1 mm", including when Controls are folded.
- It updates the moment the step changes, from the carousel or from the `[`
  `]` keys.

### 2.5 One points list

- Memo points (`Session.memo_points`, temporary) and saved points
  (`profile.positions`) are shown as **one list**. A star marks a saved
  point:
  - ★ filled `warn` means saved in the profile;
  - ☆ a gray outline means a memo point.
  - A thin line stands between the memo points and the saved points.
- Clicking ☆ saves the point to the profile (§7.1); clicking ★ makes it a
  memo point again, after a ConfirmDialog "Remove *name* from saved
  points?" with **Keep** as the primary button.
- This is the same model the Liquid point picker uses (`LIQUID.md` §3.2).
  Build the row widget once and reuse it.

### 2.6 Names

| Was | Now | Where |
|---|---|---|
| Analyze the dish | **Detect cuboids** | Manual, Picking, everywhere |
| Show past picks | **Past picks** (layer toggle) | camera chrome |
| MOVE THE ROBOT | **Controls** | Controls overlay |
| Retract Z | **Retract** | TopBar |
| Home robot position | (removed; **Park**) | |
| Here (A) / Here (D) | **Aspirate in place** / **Dispense in place** | Manual, In place card |
| By hand (as a source) | **By clicking** | Manual Aspirate |
| Retract (after aspirate/dispense) | **After aspirating / After dispensing: Lift the tip** | Manual cards (§4.4) |
| Retract slowly | **Lift slowly** | Manual cards |
| Place memo at crosshair | **Point at crosshair** | Points |

### 2.7 Visual consistency (the cause of the "different fonts" look)

- **No `QTableWidget` or `QTableView` for points or any other list on these
  screens.** Rows are our own widgets on the card background:
  - Inter for names and labels, JetBrains Mono for numbers;
  - row height 44, radius 10;
  - selected row: `selected-bg` with a 1 px `accent` border;
  - column captions in CAPS 11 px `muted`.
- **Number inputs:** 38 px high, mono 14 px, right-aligned, unit in the
  label above them ("Amount, µl", "Speed, µl/s"). No spin arrows: use our
  own field with ↑/↓ keys and the wheel.
- **Only three button heights:** 44 (primary and footer), 40 (card header
  actions), 36 (secondary in cards and overlays).
- **Every font, color and radius comes from the tokens** in
  `REDESIGN.md` §2 (`theme/tokens.py`). If a widget needs a value that is
  not a token, add the token.

---

## 3. Column 1, top: In place card

This card is always visible, at the top of column 1, and has no fold. It is
the quickest way to work: aspirate or dispense exactly where the tip is,
without any move.

- **Row 1:** the title **In place**, then on the right two fields, **Amount,
  µl** (default 10) and **Speed, µl/s** (default 50). These are this card's
  own values, separate from the Aspirate and Dispense cards.
- **Row 2:** two buttons side by side, each 50 % wide:
  - **Aspirate in place** `[A]`, with a `tips`-colored border and text;
  - **Dispense in place** `[D]`, with a `plate`-colored border and text.
  - The key is drawn as a small outlined keycap inside the button, from the
    bound key in `gui.keys`, not hard-coded.
- **Dispense in place** is disabled (opacity 0.4) while the tip is empty.
  It is not hidden.
- **Lifting:** In place does **not** lift the tip afterwards; the tip stays.
  That is the point of "in place". If the existing Here action lifts the tip
  today, ask Ivan before changing it (§9, Q2).
- The keys A and D trigger these two buttons from anywhere on the screen,
  except while a text field has the keyboard (the `CLAUDE.md` typing rule).
- Each one ends with a toast: "Aspirated 10 µl here." or "Dispensed 10 µl
  here."

---

## 4. Column 1: Aspirate and Dispense FoldCards

### 4.1 Common shape

- Both are FoldCards (`REDESIGN.md` §3); one is open at a time.
- **Header, 64 px:**
  - the number badge (① in `tips`, ② in `plate`);
  - the title (**Aspirate** / **Dispense**, 17 px, weight 600);
  - under the title, a **one-line summary of the whole action**, ellipsized,
    e.g. "Click the picture · 10 µl · 50 µl/s · lift right away" or
    "Slot 2 · A3 · 10 µl · 50 µl/s · lift after 5 s, slowly";
  - on the right, the **action button**, 40 px high.
- **The action button is always in the header**, so it works with the card
  folded and never scrolls away. The body holds only settings.
- **Which one is primary (`accent` fill):**
  - when the tip is empty, Aspirate is primary and Dispense is outlined and
    disabled. Dispense's summary then reads "The tip is empty. Aspirate
    first." in `warn-text`;
  - when the tip holds liquid, Dispense is primary and Aspirate is outlined.
- **Opening rule:**
  - after a successful Aspirate (from the card, or by a click with "Aspirate
    as soon as I click"), the Dispense card opens;
  - after a Dispense that empties the tip, the Aspirate card opens;
  - the user can open either one at any time.
- The card border tints with its color when open (`#2F4A47` for Aspirate,
  `#3A3360` for Dispense) and is `card-border` when folded.

### 4.2 Aspirate body

1. **Segmented control** (small, 32 px): **By clicking | At a point | In a
   well**.
   - **By clicking:**
     - the line "Click the picture: the tip goes there.";
     - the switch **Snap to found cuboids**, with the current size window on
       the right as an accent link ("250–500 µm"). The link opens the
       existing Sizes histogram as a popover anchored to it. This replaces
       the Sizes fold card;
     - the switch **Aspirate as soon as I click**.
   - **At a point:** the points as chips (★ or ☆ and the name), from the same
     list as §2.5. The selected chip has the `accent` border.
   - **In a well:** a MiniDeckDropdown with the plate (default: the plate
     chosen on Well center), then the plate drawn in **real SBS proportions** (max
     320 px wide; `LIQUID.md` R5). One well is chosen by click; selected in
     `tips` with a `text` ring.
2. **Fields:**
   - **Amount, µl**;
   - **Speed, µl/s**;
   - **Height Z, mm** with **From tip**. Z is shown for *By clicking* and
     *In a well* only; a point has its own Z.
3. **Lifting row** (§4.4).

### 4.3 Dispense body

1. **Segmented control:** **In a well | At a point**.
   - **In a well:**
     - a MiniDeckDropdown with the plate and, on the right, the chosen well
       in mono `plate` color ("A3");
     - the plate in real proportions (max 340 px wide). Wells filled today
       are `plate`-filled (§7.3); the chosen well is `accent` with a ring;
     - the caption "Click a well. Filled wells already got liquid today.";
     - the header button reads **Dispense into A3**.
   - **At a point:** point chips, as in Aspirate. The button reads
     **Dispense 10 µl**.
2. **Fields:**
   - **How much:** **Part | All** (small segmented);
   - **µl** (only when Part);
   - **Speed, µl/s**.
3. **Lifting row** (§4.4).

### 4.4 Lifting row (both cards)

This is the existing Retract / Delay / Retract slowly, made readable and
folded away.

- **Folded:** a full-width row "After aspirating: **lift right away**" with a
  chevron. It shows the current choice in words, which also appears at the
  end of the header summary.
- **Open:**
  - **Lift the tip:** a small segmented control **Right away | After a wait |
    Stay**;
  - **Wait, s:** a field that appears **only** with *After a wait*. A
    disabled grey field must never be shown;
  - the switch **Lift slowly**, with no caption under it.
- Map these to the existing options. If the existing Retract list has other
  or more choices, keep its set and give each a plain label in this style,
  then say so in the summary (§9, Q1).
- **Defaults:** right away, not slowly. The values are per card, kept for the
  session.

---

## 5. Column 2: Points card

- **Header:** **Points** (17 px, weight 600), then on the right **Undo** and
  **Redo** icon buttons (36 px, `aria-label`).
- Under the header, the line "Right-click the picture to add a point. The
  star keeps it for later."
- **Column captions** (CAPS 11 px `muted`): ·, *Point*, *X*, *Y*, *Z*,
  right-aligned for the numbers.
- **The list (§2.5, §2.7):** one row per point:
  - ★ or ☆ (a button);
  - the name (ellipsized);
  - X, Y and Z in mono 13 px. Z with no value reads "travel" in `muted`;
  - × at the end: deletes the point, a saved one after asking.
  - The list scrolls inside the card.
- **Click a row to select it.** A selected row unfolds an action row under
  the point at once (three equal buttons):
  - **Camera to**;
  - **Tip to**;
  - **Z from tip**;

  The actions are attached to the point they act on.
- **Bottom of the card,** above a 1 px divider:
  - two buttons, **Point at crosshair** (adds a memo point, selects it, toast
    "M3 added.") and **Tip to crosshair**;
  - the row "New points get Z" with a Select (the existing options:
    travel height, …).
- **The picture and the list stay in sync:**
  - points are drawn on the *Points* layer in `point` color with their
    names;
  - a selected point is drawn with a ring;
  - a new right-click point appears in the list selected.

---

## 6. States (match the prototype's `scenario` tweak)

| Scenario | What shows |
|---|---|
| Aspirate: click the picture | tip 0 µl, Aspirate open on *By clicking*, Snap on |
| Aspirate: from a point | Aspirate open on *At a point*, a chip selected |
| Aspirate: lifting settings open | lifting row open: *After a wait*, Wait 5.0 s, Lift slowly on |
| Dispense: into a well | tip 10 µl, Dispense open on *In a well*, A3 chosen, A1 and A2 filled today |
| Dispense: the tip is empty | Dispense open; header button disabled; summary in `warn-text` |
| Controls folded | the Controls overlay shows only its header; STEP still in the status plate |

Toasts (green, bottom center, about 3 s, one sentence) answer every action:

- "Aspirated 10 µl.";
- "Dispensed 10 µl into A3.";
- "Tip moved here.";
- "Camera moved to slot 4.";
- "23 cuboids found.";
- "M3 added.";
- "test kept for later.".

---

## 7. New logic (separate commits, with tests, before the UI)

1. **Star a memo point → saved point.**
   - `Session.keep_memo_point(name, new_name=None)` moves a memo point into
     `profile.positions` and saves the profile. It asks for a name, with the
     memo name as default.
   - `Session.unkeep_position(name)` does the reverse.
   - Both emit `memo_points_changed`, and a profile signal for positions.
2. **Retract action.** `gui.keys` gets `retract`, and the TopBar button uses
   the shared busy and lock rules (§2.1).
3. **Wells filled today.**
   - `Session.manual_filled: dict[(slot, load_name), set[well]]` records the
     wells that got a dispense on the Manual pick screen in this program
     session.
   - It drives the plate map in Dispense and the footer line, and is reset
     by a new experiment (the reset already planned for the flow's first
     step).
4. **Jog step readout.** `JogPanel` (or `JogController`) gets a `step_changed`
   signal that `CameraView`'s status plate listens to. Do not poll.
5. **Controls fold state.** It is stored in `Session` (or app settings for
   the session) and read by every Controls overlay.

---

## 8. Files, commits, checks

**Files** (names as in the redesign branch; find the real ones):

- the camera chrome: `widgets/camera_view.py`, plus a new
  `widgets/overlay_chip.py` if that helps;
- the Controls overlay: the JogPad widget;
- the TopBar;
- the shared point row widget: new `widgets/point_list.py`. It replaces
  `memo_points.MemoTable` here and is reused by Liquid;
- the Manual Run page;
- `gui/keys.py`;
- `session.py`;
- tests;
- `docs/GUI_BENCH_CHECKS.md`.

**Commits:**

1. the new logic of §7, one commit per item;
2. the camera chrome (§2.2, §2.4) on all screens;
3. the Controls overlay (§2.3);
4. Retract in the TopBar (§2.1);
5. the point list (§2.5, §2.7);
6. the Manual Run screen (§1, §3–§5);
7. the names (§2.6) across the app;
8. docs.

Tests stay green after each one.

**Automated checks (pytest, `--mock`, pytest-qt):**

1. Retract is present in the TopBar on Home, Deck, Picking and Manual.
   - It calls the retract path once.
   - It is refused (with a toast, no robot call) while a job runs, and
     disabled while locked.
2. Changing the step (carousel and `]`) updates the status plate's STEP
   text on the picture, also with Controls folded.
3. Folding Controls on Manual leaves them folded on Picking.
4. Starring a memo point moves it into `profile.positions` and the profile
   is saved. Unstarring reverses it.
5. Aspirate in place and Dispense in place:
   - use the In place card's amount and speed;
   - Dispense in place is disabled with an empty tip;
   - A and D do nothing while a number field has focus.
6. After an Aspirate the Dispense card is open, and after a Dispense that
   empties the tip the Aspirate card is open.
7. The *After a wait* choice shows the Wait field, and the other choices hide
   it (not disable it).
8. No `QTableWidget` is a child of the Manual Run page (a guard against
   regressions of §2.7).

**Bench checks** (add a "Manual pick" section to `GUI_BENCH_CHECKS.md`):

1. At 1920×1080, 100 % scaling, the whole screen fits with no scrolling:
   - with Aspirate open and its lifting row open;
   - with Dispense open on *In a well*.
2. Every text on the screen is Inter or JetBrains Mono, and the buttons have
   only three heights. Compare with the prototype side by side.
3. One full manual pick, done twice:
   - once by clicking: aspirate with "Aspirate as soon as I click", then
     dispense into a well; the well turns filled and the footer counts it;
   - once only with A and D.
4. The camera chrome looks the same on Picking, Dish bottom and Well center
   (same corners, same chip style).

---

## 9. Open questions (ask Ivan; until then use the default)

1. **The existing Retract options.** Default: three choices, Right away /
   After a wait / Stay. If the code has a different set, keep the code's set
   and label it in this style.
2. **Does In place lift the tip?** Default: no, the tip stays.
3. **Wording in Liquid.** Manual now says *Aspirate / Dispense*, while
   `LIQUID.md` uses *Take from / Put into* in the plan's sentences. Default:
   leave Liquid as specified (a plan reads as a sentence), and use
   Aspirate / Dispense for direct actions. Confirm.
4. **The footer status line** "*N* wells filled today". Keep it, or leave the
   footer with only the buttons? Default: keep it.

---

## 10. Decisions from review, 2026-11-05

These override §0–§9 and the prototype. The rule behind them: a mockup changes
the flow, almost never a function or a behavior. Where the text above describes
a behavior other than the code's, the code's stands unless listed here.
Explanatory sentences from the mockup are not copied onto the screen.

**Manual Run**

1. **By clicking behaves exactly as By hand does today** (#59, #60): a click
   moves the camera, Aspirate takes from under the crosshair, and a click never
   lowers the tip. Only the name changes. There is no "Tip moved here." toast.
2. **No hint sentences.** Left out: "Click the picture: the tip goes there.",
   "Right-click the picture to add a point. The star keeps it for later." and
   "Click a well. Filled wells already got liquid today." Liquid's `POINTS_HELP`
   stays.
3. **In place does not lift** (§9 Q2), keys A and D included. This changes #104
   on purpose. The tip is raised with Retract (the TopBar's, or the card's, #136).
4. **In place's values** start from what Aspirate's fields start from, then
   live in the page's memory, apart from the cards. Not the literal 10 and 50.
5. **Dispense in place above what the tip holds** is refused in a red toast,
   nothing moved: "Only 5 µl in the tip."
6. **Heights stay.** By clicking keeps Z | Above the bottom (From tip only with
   Z). In a well has an "Above the bottom, mm" field. Dispense has no height
   field: it reads Aspirate's height, as today.
7. **The height line stays** (#95), under the status plate, in the overlay
   style.
8. **Lifting** has the code's three choices (§9 Q1): Right away = Retract
   automatically, After a wait = Retract after delay, Stay = Don't retract.
   Lift slowly is hidden with Stay.
9. **FoldCards as §4.1**: this replaces #105. The opening rule follows the
   operator's click. In place's buttons switch no card.
10. **No wells filled today.** §7.3 and the footer's status line are dropped
    (§9 Q4). Dispense's map is the chosen plate, any well choosable, the plate
    plan's wells on it in one color, nothing of what Picking filled, no legend.
    Finish by hand still chooses the first well left short.

**Points**

11. Rows keep double click to rename and to edit X, Y and Z, and the red
    `memo_problem` flag.
12. **§7.1:** `Session.save_memo_point` already is `keep_memo_point`. Only
    `Session.unkeep_position` is new: it keeps the name and the Z, and is one
    Undo step. ☆ asks a name, the point's own by default. × on a saved
    point still asks "Delete point <name>?".
13. **New points get Z:** a Select of Travel height, Z of the tip now and Last
    Z given, Last Z given chosen at start (it is travel until a Z is given, as
    today).
14. **The Points chip alone decides** whether points are drawn on Manual: on at
    start, and shown until it is turned off. Other screens as today.
15. **Liquid's behavior stays:** the star and row widgets are shared, but only
    Manual takes a star off.

**Shared**

16. **Retract is `Motion.rise()`**: straight up to the travel height, no
    confirmation, staying where it is above it. Without a jog panel it takes
    Park's path, and it is refused as Park is. Not today's `_retract`.
17. **The camera chrome aligns what the feeds have in common, no more** (replaces
    the table's "same on every screen"): X Y Z and the step in one plate top
    left; Crosshair and Points chips and the picture button top right; mouse help
    bottom left; Controls bottom right; `set_status` in one style. A page's own
    buttons and the histogram stay where they are, restyled. Look at and Detect
    cuboids sit on the picture on Manual only.
18. **The axis glyph is removed everywhere.**
19. **Dish bottom and Well center have no picture** and get none: they leave §2.2
    and bench check 4. Well center keeps its step chip, restyled. Dish bottom
    does not change.
20. **Controls' fold is remembered per screen for the session**, not shared
    (replaces §2.3, §7.5, automated check 3): folded on Manual, left and come
    back, still folded. Picking starts folded, the others open.
21. **Keys keeps its list** under the panel while on (U, H, the page's keys,
    `TYPING_HELP`). Folded, Undo step and Keys stay in the header.
22. **The step keys stay `+` and `−`**; keycaps show the key as bound. Automated
    check 2 uses the bound key.
23. **Liquid Plan keeps its Controls button.** The step is in the plate even
    with the pad hidden.
24. **The toast stays at the window's bottom left** (#94), not bottom center.
25. **§6's and §3's toast texts are used**, as simpler than today's, except
    "Tip moved here.".
26. **§2.6 on Picking:** the button stays where it is, its words change: "Detect
    cuboids", "Detect again.", "Lights go off while detecting.", and the
    statistics window's text. Code names stay.
27. **Words:** Retract is the TopBar's, Lift what follows an aspirate or a
    dispense, Liquid's "Retract slowly" included, as "Lift slowly". Liquid keeps
    Take from / Put into (§9 Q3).
28. **§2.7 applies to Manual Run and what is drawn on a picture**, no further.
29. STOP and the StatusPill stay in the TopBar during a run. §7.4 needs no new
    signal: `JogPad.step_changed` exists. `Manual.dc.html` and its row in
    `prototype/README.md` go in the docs commit, where `CLAUDE.md` is brought up
    to date.
