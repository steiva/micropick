# Test suite audit, 2026-11-10

Audit of `tests/` at commit `218a634` (branch `ghent-gui`), written on
branch `audit/tests`. Nothing in `src/` or `tests/` was changed. Every
number below was measured on one machine: Apple M1 (4 fast + 4 slow cores),
macOS 26.3, Python 3.14.3, PySide6 6.12, pytest 9.1.1, xdist 3.8, Qt
platform `offscreen`, mock robot and mock cameras only. The suite normally
runs on Windows; rates and times there will differ, the causes will not.

Each finding is marked **confirmed** (reproduced or measured here) or
**suspected** (from reading only).

The scripts that produced the evidence are in `scripts/audit/` (section 8).
`pytest-cov`, `pytest-randomly` and `pytest-timeout` are not installed in
`lab`; they were installed with `pip install --target` into a scratch
folder and put on `PYTHONPATH`, so the env is as it was.

## 1. Summary

### What matters most

1. **Half of the parallel runs fail, each time on a different test.**
   8 of 18 whole-suite runs under xdist failed; all 4 serial runs passed.
   Five tests and one teardown account for every failure (section 2). Confirmed.
2. **The causes are three shapes of wait, copied 43 times.** A wait that
   checks its condition again after a last `processEvents()`, a wait for the
   lease when the test then reads what the job shows, and a click made while
   the panel still reads the pose. Stretching each window by 30 ms makes 17
   tests fail every time (section 2.3). Confirmed, and the fix was checked on
   scratch copies: 13 of 80 failures became 0 of 240.
3. **The GUI conftest's camera clean-up can raise in teardown.** It joins a
   grab thread that is listed but not started yet. It failed 2 of 18 parallel
   runs and 1 of 3 random-order serial runs, on a test that opens no camera. Confirmed.
4. **One test makes a third of the serial run slow.** `themed` is module
   scoped and `test_liquid_plan_page.py` first asks for it at line 776, so
   the 91 tests after it build a themed MainWindow (1.25 s, not 0.32 s).
   Leaving that one test out takes the file from 147 s to 75 s. Confirmed.
5. **`-n auto` is not the fastest setting here.** 8 workers took 105 to
   187 s; 6 workers 106 s; 8 workers with `--dist worksteal` 100 s (one run
   each for the last two). Confirmed, one machine.
6. **Typed numbers are misread on a comma-decimal computer.** The program
   sets no `QLocale`, so with Qt's locale at `nl_BE` a typed `7.5` is read
   as 75 (a point's Z) and `1.5` as 15. No test covers it, and 3 tests fail
   under that locale. Confirmed with `QLocale.setDefault`; whether the Ghent
   PC's Windows uses a comma was not checked. This is a product matter
   found on the way, not a test defect: see G-01.
7. **The suite is not bloated by copied tests.** Only 12 pairs of tests are
   near copies (91 lines). The duplication is in setup: 69 `app` fixtures,
   36 `session`, 18 `window`, 43 wait loops, about 1,500 lines. Deleting
   tests would save almost nothing. Confirmed.
8. **Five of 16 deliberate breaks pass the whole suite.** A stale job's end
   taken as the panel's own, unreadable profile JSON read as empty, a
   half-written file left after a failed write, a non-atomic write, and a
   built app's data folder. Confirmed (section 5).
9. **Tests leave a lot alive, at little cost.** At the end of a serial run
   5,732 widgets and about 186,000 Python-side QObjects are alive (63
   parentless `QMenu` per MainWindow, held in cycles until the collector
   runs), and 5 mock camera threads from `tests/workflows`. Late files run
   0 to 12 % slower for it. Confirmed.
10. **Nothing stops a hung test.** There is no timeout plugin and no
    `faulthandler_timeout`; a hang (one happened during the audit, caused by
    an audit plugin, not by the suite) blocks the run until killed. Confirmed.

### Baseline

| | |
|---|---|
| Test code | 27,950 lines in 112 files |
| Source | 49,725 lines |
| Tests collected | 1,839 (1,622 test functions, the rest by parametrize) |
| Serial wall time | 396 s and 400 s (two runs) |
| `-n auto` wall time | 105 to 187 s over 6 runs, median 148 s |
| Sum of test times, serial | 392 s |
| Sum of test times, `-n auto` | 960 s: each test runs 2.4 times slower under 8 workers |
| Line coverage | 87.6 % (27,502 statements, 3,400 not run) |

| Directory | Files | Lines | Tests | Serial time |
|---|---|---|---|---|
| `tests/gui` | 76 | 22,372 | 1,396 | 384.4 s |
| `tests/workflows` | 13 | 3,050 | 205 | 5.0 s |
| `tests/core` | 8 | 1,508 | 171 | 1.6 s |
| `tests/config` | 6 | 481 | 37 | 0.1 s |
| `tests/hardware` | 4 | 284 | 20 | 1.2 s |
| `tests/viz` | 4 | 239 | 10 | 0.1 s |

`tests/gui` is 98 % of the time. 347 of its tests build a whole
`MainWindow`; 105 of those build it under the theme.

Coverage per module, lowest first (modules of 40 statements or more):

| Module | Statements | Not run | Covered |
|---|---|---|---|
| `export_models.py` | 69 | 69 | 0.0 % |
| `selftest.py` | 65 | 65 | 0.0 % |
| `gui/report.py` | 75 | 58 | 22.7 % |
| `core/vision/floaters.py` | 234 | 180 | 23.1 % |
| `hardware/robot_clock.py` | 46 | 35 | 23.9 % |
| `workflows/calibrate_camera.py` | 207 | 155 | 25.1 % |
| `opentrons_api/ot2_api.py` | 364 | 258 | 29.1 % |
| `gui/widgets/checklist.py` | 74 | 47 | 36.5 % |
| `gui/app.py` | 47 | 27 | 42.6 % |
| `hardware/devices.py` | 87 | 47 | 46.0 % |
| `core/calibration/pixel_map.py` | 158 | 81 | 48.7 % |
| `workflows/jog.py` | 293 | 146 | 50.2 % |
| `hardware/labware.py` | 90 | 39 | 56.7 % |
| `hardware/camera.py` | 576 | 237 | 58.9 % |
| `gui/detector.py` | 128 | 46 | 64.1 % |
| `core/vision/yolo.py` | 135 | 46 | 65.9 % |
| `hardware/protocols.py` | 117 | 34 | 70.9 % |
| `config/store.py` | 264 | 74 | 72.0 % |
| `gui/camera_moves.py` | 87 | 22 | 74.7 % |
| `paths.py` | 96 | 23 | 76.0 % |
| `workflows/picking.py` | 741 | 161 | 78.3 % |

The large GUI modules are well covered: `gui/session.py` 91.5 %,
`gui/shell.py` 92.7 %, `gui/pages/manual.py` 92.3 %,
`gui/pages/liquid_plan.py` 97.4 %, `gui/widgets/jog_panel.py` 96.2 %,
`workflows/motion.py` 99.5 %. The whole table is printed by
`scripts/audit/coverage_report.py <file> --modules`.

Two coverage runs, one serial and one on 6 workers, ran exactly the same
lines (diff 0 both ways), so a coverage diff between two runs is a usable
check for a deletion.

## 2. Flaky tests

### 2.1 The runs

| Mode | Runs | Failed |
|---|---|---|
| Whole suite, `-n auto` | 6 | 4 |
| Whole suite, `-n auto`, 4 processes burning a core each | 4 | 2 |
| Whole suite, `-n 4`, `-n 6`, `--dist worksteal`, `--dist loadfile` | 4 | 1 (`loadfile`) |
| Whole suite, `-n auto`, random order (seeds 11, 12) | 2 | 0 |
| Whole suite, `-n 6`, with coverage | 2 | 1 |
| Whole suite, serial (two plain, one with the leak counter, one with coverage) | 4 | 0 |
| `tests/gui`, serial, random order (seeds 1, 2, 3) | 3 | 1 (seed 1) |

Light audit work ran beside some of the parallel runs and beside the second
serial run and the random-order serial runs, so the parallel rate is for a
machine that is also doing something else.

Random order found nothing that order alone breaks: the one failure there
is the teardown race below, which also fails in file order.

### 2.2 Tests that failed without any help

"Under load" is the test repeated 40 times on 8 workers beside 4 busy
processes (`scripts/audit/stress.sh`).

| Test | Failed in | Under load | Cause | Fix |
|---|---|---|---|---|
| `test_dish_bottom.py::test_measured_with_the_tip` | 1 of 18 parallel runs | 13 of 40 | A. `_until` returns `condition()` evaluated again after a last `processEvents()`. Save is followed by a lift started on a zero timer: the loop ends between the two jobs, the last pass starts the lift, the panel is busy again and the wait reports False at once, not after 10 s. | Commit F2 |
| `test_dish_bottom.py::test_redo_goes_back_to_the_first_step_and_keeps_the_value` | 1 of 18 | 9 of 40 | A, the same lines. | Commit F2 |
| `test_pipette_page.py::test_the_two_cards_work_embedded_in_another_widget` | 1 of 18 | 0 of 40 | B. `tip.take_button.click()` right after the `JogPanel` is made, while it reads the pose; a busy panel refuses the job, nothing is queued, the wait runs out after 10 s. | Commit F3 |
| `test_top_bar_retract.py::test_it_shows_retracting_on_its_own_worker` | 2 of 18 | 0 of 40 | D. Asserts "Retracting…" right after the click. `MainWindow._retract` starts the worker and then calls `_show_park()`; with the instant mock the job can end and give the lease back before that call, which puts the text back to "Retract" inside the click. The job trace shows the worker's `run` left before the assert. | Commit F4 |
| `test_settings_page.py::test_home_from_advanced_asks_and_undoes_the_tip_calibration` | 1 of 18 | not run | C. Waits for `not robot_lease.busy`, then reads the label. The lease is let go on the worker's thread before the page's finish slot runs: "Homing…" is still shown. | Commit F3 |
| Teardown of whichever test is ending (`tests/gui/conftest.py:49`), seen on `test_pipette_page.py::test_the_cards_stand_in_one_column_of_440`, `test_manual_page.py::test_every_button_on_the_cards_is_one_of_three_heights`, `test_plan_templates.py::test_the_presets_saved_before_templates_move_into_the_folder`, `test_step_chip.py::test_every_page_with_a_picture_has_the_chip[PickingPage]` | 2 of 18 parallel, 1 of 3 random serial | not run | E. `_delete_what_the_test_left` walks `threading.enumerate()` for `grab-` threads and calls `camera.close()`, which joins. A camera a page is still opening on a worker is listed from the moment `start()` is called and before it has started: `join` raises "cannot join thread before it is started". `Session.shutdown` does not wait for an open in flight, so the camera can even belong to the test before. `scripts/audit/limbo_join.py` shows the window with plain threads. | Commit F1 |

All six are **confirmed**: each failed in a run, and its cause was read from
the job trace that `scripts/audit/hang_dump.py` adds to a failure, or, for
E, from the traceback and the thread demonstration.

The two dish bottom tests and `test_pipette_page.py` are the flakes noted on
2026-11-04 on Windows. The cause given then, a wait that runs out, is right
for B and wrong for A: A fails at once.

### 2.3 Tests that fail every time when a window is stretched

`scripts/audit/slow_mock.py` stretches one race window by 30 ms: the mock's
pose read (`read`), the start of a worker's job (`start`), or the gap between
a worker letting the lease go and saying it finished (`end`). A test that
fails then depends on the mock answering within the same millisecond. Run on
`tests/gui` with 4 workers; every other GUI test passed in all three modes.

| Test | Fails in | Cause |
|---|---|---|
| `test_dish_bottom.py::test_measured_with_the_tip` | read, start, end | A |
| `test_dish_bottom.py::test_redo_goes_back_to_the_first_step_and_keeps_the_value` | read, start, end | A |
| `test_pipette_page.py::test_the_two_cards_work_embedded_in_another_widget` | read, start, end | B |
| `test_pipette_page.py::test_the_title_and_icon_follow_the_robots_answer` | read, start, end | B: `page.take_button.click()` with no wait for the page's first pose read |
| `test_pipette_page.py::test_a_click_on_the_rack_chooses_the_tip_to_take` | read, start, end | B, the same |
| `test_pipette_page.py::test_a_tip_taken_says_so_in_a_toast` | start | B or C, not read further |
| `test_pipette_page.py::test_a_tip_dropped_says_calibration_needs_a_new_one[drop_here_button]` | start | B or C, not read further |
| `test_settings_page.py::test_home_from_advanced_asks_and_undoes_the_tip_calibration` | start, end | C |
| `test_manual_page.py::test_from_tip_takes_the_z_of_the_tip_now` | end | F: `page.jog.refresh_position()` does nothing while the panel is busy, and the silent `_wait` then goes on with the old pose |
| `test_manual_page.py::test_the_height_line_is_the_cameras_slot_not_the_tips` | end | F |
| `test_manual_page.py::test_the_height_line_reads_a_measurement_of_an_earlier_session` | end | F |
| `test_manual_page.py::test_the_height_line_says_drawing_for_a_measurement_on_another_height` | end | F |
| `test_manual_page.py::test_by_hand_a_plate_measured_in_an_earlier_session_has_its_bottom` | end | F |
| `test_run_status.py::test_parking_shows_until_the_move_ends` | end | C: "Parking…" still shown after a wait for the lease |
| `test_camera_calibration_sheet.py::test_the_sweep_is_a_run_with_stop_and_the_pill` | end | C: `sheet.step` read before the sweep's end is delivered |
| `test_robot_monitor.py::test_a_look_is_not_work_that_stop_waits_for` | read | not read further |
| `test_step_chip.py::test_every_page_with_a_picture_has_the_chip` | end | E, in teardown |

These are **confirmed** as depending on timing. Five of them also failed
unhelped (2.2); the other twelve did not fail in 25 unhelped runs here and
are the ones to expect on a slower or busier machine.

### 2.4 The three shapes behind them

`scripts/audit/wait_helpers.py` lists every hand-made wait loop in the tests:
43 helpers in 38 files.

- **16 check the condition again after a last `processEvents()`** (cause A):
  the eight identical `_until` (`test_dish_bottom`, `test_liquid_plan_page`,
  `test_liquid_run_step`, `test_movement_keys`, `test_picking_step`,
  `test_pipette_page`, `test_robot_lease_pages`, `test_tip_liquid`) and the
  asserting `_wait` and `_settled` of seven more files. The last pass is
  there for a reason: `JogPanel.busy` goes False when the worker's thread
  ends, before `_job_done` is delivered. `JogPanel.job_pending` is the
  property that is False only once the end was delivered, and a wait on it
  needs no last pass.
- **23 are silent**: when the wait runs out the test goes on. About 260 calls,
  137 of them `_wait(app, page)` in `test_manual_page.py`. A failure then
  shows up later as a wrong value, as in cause F.
- **11 calls of a returning `_until` are not asserted**
  (`test_liquid_plan_page` 3, `test_liquid_run_step` 5, `test_movement_keys`
  1, `test_picking_step` 2), which makes them silent too.

What is waited for differs from file to file: `jog.busy`, `jog.job_pending`,
`robot_lease.busy`, `window._robot_busy()`, or the lease and every panel
(`_robot_free`, four copies). Only the last two forms are what CLAUDE.md asks
for. In the `window` fixtures of `test_camera_sheet`,
`test_camera_calibration_sheet`, `test_setup_sheets`, `test_movement_keys`,
`test_liquid_plan_page` and `test_liquid_run_step`, the wait for the lease
after `new_run()` waits for nothing: `new_run()` is synchronous and a
panel's pose read takes no lease.

### 2.5 The fix, checked

On scratch copies of the test files (not committed):

- `test_dish_bottom.py` with the wait loop below and the two saves waiting
  for the lift's end: 0 failures in 2,040 tests (the two flaky tests 240
  times) under the load that fails the original 13 times in 680.
- `test_manual_page.py` with `_wait` on `job_pending`, asserting, and a
  wait before each of its 11 `page.jog.refresh_position()`: all 178 pass
  with `end` and with `read` stretched; the original fails 5.

```python
def until(condition, timeout_s=10.0):
    end = time.monotonic() + timeout_s
    while True:
        app.processEvents()
        if condition():
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.005)
```

Nothing runs between the check that succeeds and the return.

### 2.6 Looked for and not found

- **State leaking through the theme**: `themed` puts the style sheet,
  palette, font and style back; no test calls `apply_theme` on the app.
- **Order dependence**: three random orders of `tests/gui` serially and two
  of the whole suite in parallel, no failure of that kind.
- **Log handlers and `sys.excepthook`**: 2 root handlers before and after
  every test.
- **Timers and QThreads left running**: at most 1 timer and 2 QThreads
  alive between tests, 0 at the end.
- **Shared files**: every GUI fixture sets `MICROPICK_ROOT` to `tmp_path`.

Suspected and never seen failing in 25 runs: `test_point_list.py::
test_the_actions_of_a_row_that_lifted_take_no_click` (`qWait(INTERVAL_MS +
15)` and then asserts the row is still inert, which holds for two
intervals; a `qWait` that overshoots by one interval fails it),
`tests/hardware/test_clip_writer.py` (`assert time.monotonic() - started <
1.0`, and `time.sleep(0.3)  # the file is open by now`), and
`tests/workflows/test_picking.py::test_stop_interrupts_mid_batch` (a watcher
thread polling every 1 ms against moves at 400 mm/s).

## 3. Slow tests and fixtures

Serial times. A `MainWindow(Options(mock=True))` with its show, a task
step and its teardown costs 0.32 s plain and 1.25 s under the theme
(`scripts/audit/time_window.py`; CLAUDE.md says 0.25 and 1.1 s). The themed
second is Qt polishing the style sheet as widgets are parented
(`QBoxLayout.addWidget` 0.32 s, `setCentralWidget` 0.16 s, the stack's
`addWidget` 0.14 s): it is in Qt, not in the tests.

| What | Time now | Why | Proposed | Saves |
|---|---|---|---|---|
| `test_liquid_plan_page.py`, 129 tests | 147 s (38 % of the suite) | Every test builds a MainWindow. `test_the_wait_help_line_is_not_cut_off_on_a_small_window` (line 776) asks for the module-scoped `themed`; the 91 tests after it are themed, 1.40 s each against 0.52 s for the 38 before. The comment at line 1767 says the themed tests are last. | Move that test to the end, beside the other two themed ones. Measured by deselecting it: 75 s. | 72 s serial, confirmed |
| MainWindow per test, 347 tests | about 208 s of 392 s (242 × 0.32 + 105 × 1.25) | 18 files have a `window` fixture; a MainWindow builds all its pages. | After the line above, 14 themed windows are left. Beyond that the cost is the program's own start-up; making pages lazily is a change in `src/` and outside this audit. | none proposed |
| `test_retract_card.py`, 14 tests | 9.3 s | Its `page` fixture is themed, so its 5 MainWindow tests are too once any `page` test ran. | Order the file so the MainWindow tests come first, or give the themed tests a class of their own. | about 4 s, suspected |
| `test_toast.py`, 9 window tests | about 11 s | Autouse `_in_the_theme` over a MainWindow. The toast's place over the Footer needs the window; its colors need the theme. | Leave. | 0 |
| `test_nothing_shifts.py`, 38 tests | 28.5 s | Themed pages and `stands_still`, which settles 0.15 s twice per check. | Leave: the settle is the scroll column's own timer. | 0 |
| `test_manual_columns.py::test_no_action_changes_the_columns[size0]` | 3.2 s | Many actions on a themed page at two sizes. | Leave. | 0 |
| `test_worker.py::test_cancel_connected_as_a_slot_would_never_arrive` | 3.0 s | Waits for a cancel that must not arrive. | Leave: the wait is the test. | 0 |
| `test_step_list.py::test_the_drags_in_a_real_drag_loop` | 1.7 s | A process of its own on `minimal`. | Leave (CLAUDE.md). | 0 |
| `tests/core/vision/test_onnx_yolo.py::test_letterbox_is_ultralytics_own` | 1.4 s | Imports ultralytics. | Leave. | 0 |
| xdist setting | 148 s median at `-n auto` | 8 workers on 4 fast and 4 slow cores: each test takes 2.4 times longer. | `-n 6` (106 s) or `-n auto --dist worksteal` (100 s). One run each: measure on the Windows PC before changing CLAUDE.md. `--dist loadfile` is worse (194 s): one file is a third of the suite. | about 45 s parallel, one machine |

No test was found moving the mock without the instant speed by accident: the
only `speed_mm_s` is `test_stop_interrupts_mid_batch`, on purpose.

## 4. Other findings

**T-01. One wait, written 43 times in three shapes.** Section 2.4. Files:
40 under `tests/gui`. Evidence: `scripts/audit/wait_helpers.py tests`.
Confirmed.

**T-02. The same fixtures in every file.** `app` 69 times (68 identical),
`session` 36 times in 7 bodies, `window` 18 times, `page` 14 times,
`_connect` 7, `_tip_on` 6, `_robot_free` 4, `_calibrated` 3; 32 helpers
have an identical body in two or more files, about 535 lines beyond one
copy each, and about 1,500 with the near copies and the ten lines of
`importorskip`, `os.environ.setdefault` and `# noqa: E402` imports at the
top of each of 72 files. Evidence: `scripts/audit/duplicates.py tests`.
Confirmed.

**T-03. Teardown differs between the copies.** Of the 18 `window` fixtures,
some hide the window before `shutdown()` and some do not; `test_lock.py`,
`test_run_status.py`, `test_settings_page.py`, `test_retract_card.py` and
`test_top_bar_retract.py` never show it; only `test_liquid_plan_page.py`
and `test_liquid_run_step.py` stop a run first. Confirmed by reading; no
failure traced to it.

**T-04. What a test leaves alive.** From the leak counter over a serial
run (`scripts/audit/leak_plugin.py`): 5,732 widgets, 4,616 of them top
level, and 186,582 QObjects reachable from Python at the end; 311 tests
leave more than 20 top-level widgets each. One MainWindow leaves 63
parentless `QMenu` after the fixture's teardown, held in reference cycles:
`gc.collect()` frees all but one widget (`scripts/audit/left_after_window.py`).
Up to 7 `Session` objects of earlier tests were alive in one xdist worker.
`tests/workflows` has no clean-up: 5 `grab-mock` camera threads are alive
at the end of the process, three of them from `test_labware_identity.py`
(`test_session_refuses_empty_slot`, `test_session_refuses_wrong_definition`
and `test_unconfirmed_resume_refused_then_allowed` each leave one: the
session is refused after the camera is made). Cost in time: `test_shell.py`
8.6 s alone and 9.6 s inside the whole run, `test_top_bar_park.py` 3.9 and
4.3 s, `test_well_center_pages.py` 4.7 and 4.7 s. Confirmed.

**T-05. No guard against a hang.** No `pytest-timeout`, no
`faulthandler_timeout`, no pytest section in `pyproject.toml` at all. During
the audit a run hung for 14 minutes with one worker spinning; the cause was
an audit plugin (a wrapped `Worker.run` under another name is called on the
GUI thread: PySide finds a slot's thread through the method's name), not the
suite, but nothing would have ended it. Confirmed.

**T-06. Tests reaching into private state.** 63 uses of a private
attribute of the page or view in `test_manual_page.py`, 36 in
`test_liquid_run_step.py`, 28 in `test_well_center_pages.py`, 26 in
`test_picking_sizes.py`, 20 in `test_camera_view.py`: for example
`page._cuboids = ...; page._show_sizes(); page._refresh()` and then
`page.view._histogram.inside`, or `run._ended == "done"`. They break on a
rename that changes nothing on screen. Confirmed by count; which of them
could go through the public surface was not worked out.

**T-07. Exact words and pixels are mostly on purpose.** 377 asserts
compare a label's text exactly and about 40 compare a width or height.
CLAUDE.md fixes those words and sizes ("Words on screen", the 340 px pad,
the 400 and 420 px columns), so these pin decisions and are to keep. Not a
defect.

**T-08. Near copies that should be one parametrized test.** 12 pairs, 91
lines beyond one copy each, at 80 % token overlap: for example
`test_manual_page.py::test_without_a_pixel_map_a_click_moves_nothing` and
`..._a_click_by_hand_moves_nothing`; the four `test_the_height_line_*`
pairs; `test_manual_dispense.py::test_dispense_all_empties_the_tip` and
`test_dispense_part_leaves_the_rest`; `test_work_columns.py` wide and
narrow; `test_camera_sheet.py` focus saved and not saved. Evidence:
`scripts/audit/duplicates.py tests --similar 0.8`, list 3. Confirmed.

**T-09. Tests of the same behavior through different pages: few.** Read by
theme (busy, locked, pixel map, typing, undo, templates): each page's own
graying or refusal is a separate decision in CLAUDE.md and has one test.
Two real repeats: `test_top_bar_park.py::
test_the_toast_is_at_the_windows_bottom_left` (the toast's place is
`test_toast.py`'s first two tests) and `test_redesign_components.py::
test_plate_grid_click_on_a_header_takes_its_column`
(`test_plate_grid.py::test_the_headers_take_their_column_and_row`, for
every plate). Three tests are named
`test_without_a_pixel_map_a_click_moves_nothing` in three files, each for
its own page. Confirmed by reading.

**T-10. Tests that test nothing: none found by reading, five gaps found by
breaking.** Every test function has an assertion of its own or through a
helper, but for `tests/workflows/test_labware_identity.py::
test_session_starts_when_slot_matches`, which is "does not raise" beside
two tests that do. Of 16 breaks made in `src/` one at a time
(`scripts/audit/mutate.py`), 11 were caught by the tests named for them and
5 passed the whole suite; those are G-02 to G-05. Confirmed.

**T-11. The suite depends on the computer's locale.** With Qt's default
locale at `nl_BE`, `test_point_list.py::test_a_double_click_edits_x_y_and_z`,
`test_point_list.py::test_a_typed_z_is_said_and_a_z_left_as_it_was_is_not`
and `test_liquid_plan_page.py::
test_a_wash_is_built_on_the_page_and_compiles_to_the_bench_wash` fail: they
type a number with a dot. Evidence: `scripts/audit/locale_plugin.py`.
Confirmed. On macOS Qt takes the locale from the system, so this was set
through `QLocale.setDefault`.

**T-12. Docs that no longer say what is so.** `docs/DESIGN.md` §8 opens
"There is no test suite in the repository yet". CLAUDE.md gives 0.25 s and
1.1 s for a MainWindow (0.32 and 1.25 s here) and `-n auto` as the way to
run. Confirmed.

**T-13. Small things.** `tests/slices/` holds only a `__pycache__`.
`test_liquid_plan_page.py:255` builds a `QDragMoveEvent` with a deprecated
constructor, the suite's only two warnings. A scratch copy of a test file
run from outside the repository needs `--rootdir`, or pytest takes the
file's folder as the root and `tests/conftest.py` (the instant mock) is not
applied: the tests still pass, three times slower. Confirmed.

## 5. Gaps

What the task asked about, and what was found. "Break" means the line was
changed in `src/`, the whole suite run, and the line put back.

**G-01. A comma-decimal locale.** Not tested, and the program misreads.
`number_field` and the point list's editors are `QDoubleSpinBox`, which
follow Qt's locale, and nothing sets one. With `nl_BE` or `de_DE`: typed
`1.5` shows `15,00` and reads 15.0; `12.5` reads 125.0; `1,5` reads 1.5.
With the C locale `1,5` reads 15.0. So on a comma-decimal Windows a dot
typed into a height, a volume or a point's Z is dropped
(`scripts/audit/comma_locale.py`). Confirmed for the widget. Not checked:
the regional settings of the lab PCs. The decision is the user's (fix the
locale to C at start, or accept both signs); the test follows the decision.

**G-02. A stale job's end.** `JogPanel._claim` ignores a finish that is not
from the worker in flight. With the check removed the whole suite passes.
Confirmed. Test: a panel whose read is replaced by a job before the read's
end is delivered keeps the job as its own (`job_pending` stays True until
the job's end, `_then` is called once).

**G-03. Unreadable and half-written files.** `config/store.py` lines
122-123 (profile JSON that is not JSON), 135-137 (a failed write removes its
temporary file) and 147-148 (JSON that fails validation) are never run, and
so are the same lines of `core/routine.py` (591-592, 781-783) and
`core/liquid.py` (139-141). Breaks that pass the suite: `_read_json`
returning `{}` for bad JSON; the temporary file left behind; `os.replace`
swapped for a plain write. Confirmed. `config/app_settings.py` is the one
that is tested (`test_an_unreadable_file_is_the_defaults_with_a_reason`).
Tests: a profile file holding `{not json` raises `ProfileError` naming the
file; with `os.replace` made to raise, the old file is whole and no `.tmp`
is left in the folder, for `store`, `routine` and `liquid`.

**G-04. A built app's folders.** `paths.py` lines 61-69 and 80-82
(`frozen()`, `bundle_dir()`, `root()` in a build) and 164-189
(`seed_from_bundle`'s copy) are never run; `root()` returning the working
directory in a build passes the suite. Confirmed. Test: with `sys.frozen`
and `sys._MEIPASS` set by monkeypatch and no `MICROPICK_ROOT`, `root()` is
`Documents/micropick` under a patched home, `bundle_dir()` is the bundle,
and `seed_from_bundle()` copies only what the root lacks.

**G-05. A jog step from above the top.** `JogController._from_the_top`
(`workflows/jog.py` 238-272), a decision in CLAUDE.md, is never run;
`workflows/jog.py` is 50 % covered, the rest of the gap being the
OpenCV jog window (421-512 and after), which needs no test here.
Confirmed by coverage. Test, without Qt: after a retract a step in X is one
move that also comes down to the envelope's top, and a step down smaller
than the gap goes to the top.

**G-06. Robot moves and the lease.** Covered: a second claim, a refused
claim not kept, the lock, a failed job giving the lease back, Park, Retract
and the pages' graying. All four breaks there were caught. No gap found.

**G-07. Pause and Stop between moves.** Covered for picking
(`test_stop_interrupts_mid_batch`, `test_pause_blocks_then_resumes`), liquid
runs (`test_liquid_run.py`, `test_liquid_run_step.py`) and the second Stop
(caught when broken). `workflows/picking.py` is 78 % covered; which of its
161 lines not run are stop paths was not worked out. Suspected gap only.

**G-08. Shutdown during a move.** Covered: closing during a run asks,
stays open until stopped, parks after a short job, a second close does not
shut down twice; both breaks caught. Not covered: `shell.py` 363 and
366-369, and a camera still opening at `Session.shutdown` (cause E): the
session closes the cameras that are open and does not wait for one on its
way. Confirmed by coverage and by E. Test: a session shut down while
`open_camera` runs on a worker leaves no `grab-` thread alive once the
worker ends.

**G-09. Errors in slots.** `log_bridge`'s `sys.excepthook` is installed and
its body is run by some test, but no test asserts that an exception raised
in a slot reaches the log as "uncaught"; the GUI conftest only puts the hook
back. Suspected: PySide prints an exception in a slot and goes on, and
whether it calls `sys.excepthook` was not checked.

**G-10. The robot's HTTP wrapper.** `opentrons_api/ot2_api.py` is 29 %
covered: the tests go through `MockRobot`. The hold that refuses moving
commands (`RobotHeld`) is tested through the session. Not proposed: it needs
a fake HTTP server and is a topic of its own.

The tests worth writing, in order: G-01 (after the decision), G-03, G-02,
G-05, G-08, G-04.

## 6. Restructuring plan

### Shared fixtures, in `tests/gui/conftest.py`

Fixtures, not an imported module: `lab` has an unrelated package named
`tests`, and a conftest needs no import.

- `app`, session scoped: `QApplication.instance() or QApplication([])`.
  `QT_QPA_PLATFORM` is set at the top of the conftest, before PySide6 is
  imported, and `collect_ignore_glob = ["test_*.py"]` there when PySide6 is
  missing replaces the 72 `importorskip` lines.
- `until`: the loop of 2.5, as `until(condition, timeout_s=10.0)`, which
  **asserts** and names what it waited for when given `what=`.
- `robot_free`: `robot_free(window)` or `robot_free(session, *panels)`:
  `until` no lease held and no panel with `job_pending`. The four
  `_robot_free` and every `_settle`, `_settled`, `_wait(app, page)` become
  this.
- `read_pose`: `read_pose(panel)`: `robot_free`, `panel.refresh_position()`,
  `robot_free`. For cause F.
- `session`: mock session with the profile loaded, `connected=True` by
  default (`probe_robot`, `new_run`); `bare_session` for the three files
  that want none. Teardown `shutdown()`.
- `window`: `MainWindow(Options(mock=True))`, shown, connected, `robot_free`;
  teardown: stop requested, `robot_free`, hide, `shutdown()`,
  `deleteLater()`. A file that needs more (labware, a patched class) wraps
  it in a fixture of its own.
- `tip_on(session, well="A1")` and `calibrated(session)` (the toy pixel map
  and offset, three copies with `W, H, K`).

`tests/conftest.py` gains a clean-up for cameras left by `tests/workflows`.

### Files to merge

Only where one subject is split and the fixtures are the same. Tests are
moved whole, none dropped.

| Into | From | Tests |
|---|---|---|
| `test_jog_panel.py` | `test_jog_pad_panel`, `test_jog_says`, `test_jog_pose_kept`, `test_jog_positions`, `test_jog_view_axes` | 32 |
| `test_retract.py` | `test_top_bar_retract`, `test_retract_card` | 24 |
| `test_manual_page.py` | `test_manual_dispense` (7 tests on the session's tip count; they may also go to `test_labware_session.py`) | 7 |

`test_redesign_components.py` (26 tests over a dozen widgets) is better
split than kept: each test to its widget's file. Optional, no gain in lines.

### Tests to parametrize

The 12 pairs of T-08, each into one test with two cases: 24 tests stay 24
cases, about 90 lines less.

### Tests to delete

| Test | Why | Lines only it runs |
|---|---|---|
| `test_top_bar_park.py::test_the_toast_is_at_the_windows_bottom_left` | The toast's place is `test_toast.py`'s; its last line, the tone of `window.say`, moves into `test_toast.py::test_an_answer_is_green_and_a_refusal_red` as one assert first. | none |
| `test_redesign_components.py::test_plate_grid_click_on_a_header_takes_its_column` | `test_plate_grid.py::test_the_headers_take_their_column_and_row` does it for every plate. | none |

Checked: the whole suite without these two runs every line the whole suite
runs (`coverage_report.py --diff`, 0 lines lost; 87.6 % both). No other
deletion is proposed: there is nothing else that repeats.

### Afterwards, estimated

| | Now | After |
|---|---|---|
| Lines | 27,950 | about 26,300, then about 26,600 with the new tests |
| Tests | 1,839 | 1,837, then about 1,855 |
| Serial | 396 s | about 320 s (measured part: 72 s) |
| Parallel | 148 s median | about 80 to 100 s on this machine (estimated from the serial saving and 6 workers; not measured together) |
| Parallel runs that fail | 8 of 18 | 0 expected for causes A to F; to be shown by 10 runs |
| Coverage | 87.6 % | 87.6 % after the restructuring, higher after the new tests |

The size barely moves. The suite is large because the program has many
decisions pinned one test each, not because it repeats itself.

## 7. Fix plan

One commit each, in this order. After every commit:
`conda run -n lab python -m pytest -q -n auto tests` passes, and for the
commits marked *coverage* the diff against the baseline loses 0 lines:

```
PYTHONPATH=<plugins> COVERAGE_FILE=<out>/after.coverage conda run -n lab python -m pytest -q \
    -p no:randomly -p no:cacheprovider --cov=micropick --cov=opentrons_api --cov-report= -n 6 tests
PYTHONPATH=<plugins> conda run -n lab python scripts/audit/coverage_report.py --diff <out>/base.coverage <out>/after.coverage
```

No commit changes `src/`, but N1 if the user decides so.

### Flaky fixes

**F1. `test(gui): the camera clean-up waits for an open in flight`**
Files: `tests/gui/conftest.py`.
In `_delete_what_the_test_left`, before the deferred deletes: for every
worker in `micropick.gui.workers.base._RUNNING` that is not `background`,
`worker.wait(5000)`. In the loop over threads, skip a thread for which
`thread.is_alive()` is False, and wrap `camera.close()` in
`try/except RuntimeError` that waits `thread.join(1)` once the thread is
alive and closes again. Update the docstring. Check: 10 runs of
`-n auto tests` show no "cannot join thread before it is started".

**F2. `test(gui): one wait, that does not run events after its answer`**
Files: `tests/gui/conftest.py`, `tests/gui/test_dish_bottom.py`.
Add the `app`, `until`, `robot_free` and `read_pose` fixtures of section 6
(leave the files' own `app` fixtures for now: a file's fixture wins). In
`test_dish_bottom.py` delete `_until`, take `until`, and replace every
`not page.jog.busy` and `not p.jog.busy` by `not ....jog.job_pending`. In
`test_measured_with_the_tip` the wait after `page.save_button.click()`
becomes confirmed **and** `float(xyz(robot)[2]) > low + 1` **and**
`not page.jog.job_pending`; in `test_redo_goes_back_...` confirmed and
`float(xyz(session.robot)[2]) > 77.0` and not pending. Check:
`sh scripts/audit/stress.sh 40 4 8 tests/gui/test_dish_bottom.py` passes
three times (it fails 11 to 13 of 680 before).

**F3. `test(gui): wait for the panel before a click, and for what a job shows`**
Files: `test_pipette_page.py`, `test_settings_page.py`, `test_run_status.py`,
`test_camera_calibration_sheet.py`, `test_robot_monitor.py`.
`test_pipette_page.py`: delete `_until`, take `until`; in the `page`
fixture and in `test_the_two_cards_work_embedded_in_another_widget` wait
`not jog.job_pending` after the page or panel is made; every wait on
`.jog.busy` becomes `job_pending`. `test_settings_page.py` line 250:
wait for `advanced.state.text() == "Homed."`, not for the lease. `test_run_status.py::
test_parking_shows_until_the_move_ends`: wait for
`window.top.park.text() == "Park"`. `test_camera_calibration_sheet.py::
test_the_sweep_is_a_run_with_stop_and_the_pill`: wait for
`sheet.step == "center"`. `test_robot_monitor.py::
test_a_look_is_not_work_that_stop_waits_for`: read it under
`AUDIT_SLOW=read` first; its cause was not read in the audit. Check, for
each of `read`, `start`, `end`:
`PYTHONPATH=scripts/audit AUDIT_SLOW=<mode> conda run -n lab python -m pytest -q -p slow_mock -n 4 <the five files>`
passes.

**F4. `test(gui): Retracting… is asserted while the job is held`**
Files: `tests/gui/test_top_bar_retract.py` (and `test_retract_card.py`
line 161 if the same shape).
In `test_it_shows_retracting_on_its_own_worker`, patch
`window.session.motion.rise` with a function that waits on a
`threading.Event` (10 s at most) and then calls the real one; click, assert
"Retracting…", set the event, `robot_free`, assert "Retract" and enabled.
`src/` is not changed: the product's order (`worker.start()` before
`_show_park()`) is right with a robot that takes time; whether to swap the
two lines is the user's call.

**F5. `test(gui): the Manual page's waits assert and read the pose for sure`**
Files: `tests/gui/test_manual_page.py`.
`_wait(app, page)` becomes `robot_free(session, page.jog)` (asserting, on
`job_pending`); each of the 11 `page.jog.refresh_position()` becomes
`read_pose(page.jog)`. Check: the file passes under `AUDIT_SLOW=end` and
`AUDIT_SLOW=read` (5 fail before).

**F6. `test: a test that hangs is ended and says where`**
Files: `pyproject.toml`.
Add `[tool.pytest.ini_options]` with `faulthandler_timeout = 120` (built
in, dumps every thread's stack; it does not end the test). Ending it needs
`pytest-timeout` in the `dev` extra and `timeout = 300`: ask the user before
adding a dependency.

**F7. `test(gui): every wait asserts`**
Files: the 23 files with a silent helper and the 4 with unasserted `_until`
calls (listed by `scripts/audit/wait_helpers.py`).
Replace each helper by `until` or `robot_free`. A wait that now fails where
it used to pass silently is a real finding: fix the condition (most will
want `job_pending`), do not lengthen the time. Do it file by file, running
the file under the three `AUDIT_SLOW` modes; split into several commits if
more than a few tests need a new condition. *coverage*.

After F1 to F7: 10 runs of `-n auto tests`, and `tests/gui` under each
`AUDIT_SLOW` mode. All pass, or the failing test is read and fixed the
same way.

### Speed

**S1. `test(gui): the themed liquid plan tests are last again`**
Files: `tests/gui/test_liquid_plan_page.py`.
Move `test_the_wait_help_line_is_not_cut_off_on_a_small_window` (line 776,
with nothing else) to the end of the file, after the "place choice" section.
Check: the file alone takes about 75 s, not 147 s.

**S2. `docs(claude): how many workers`**
Files: `CLAUDE.md`.
Only after timing `-n auto`, `-n 6` and `-n auto --dist worksteal` three
times each on the Windows PC. Correct the MainWindow times in the same
commit.

### Restructuring

**R1. `test(gui): one app, session and window fixture`**
Files: `tests/gui/conftest.py` and every `tests/gui/test_*.py`.
Add `session`, `window`, `tip_on`, `calibrated` as in section 6; set
`QT_QPA_PLATFORM` and `collect_ignore_glob` in the conftest. In each file
delete the `app` fixture, the `importorskip`, the `setdefault`, the
`# noqa: E402` marks, and the `session` or `window` fixture where it is the
shared one; keep a file's own fixture where it does more, built on the
shared one. Ten or so files per commit. *coverage* after each.

**R2. `test(gui): the jog panel's tests in one file; Retract's in one`**
Files: the merges of section 6. Tests moved whole. The count of collected
tests is the same before and after (`pytest --collect-only -q | tail -1`).
*coverage*.

**R3. `test: twelve pairs as parametrized tests`**
Files: those of T-08. The count of collected tests is the same. *coverage*.

**R4. `test: two tests that repeat others`**
Files: `test_top_bar_park.py`, `test_redesign_components.py`,
`test_toast.py` (the tone assert moves there first). *coverage*: 0 lines
lost, as measured.

**R5. `test: nothing left alive after a test`**
Files: `tests/conftest.py`, `tests/gui/conftest.py`,
`tests/workflows/test_labware_identity.py`.
Close the camera in the three refused-session tests (make it before
`pytest.raises` and close it in `finally`); an autouse fixture in
`tests/conftest.py` that fails a test leaving a `grab-` thread alive, once
those are fixed. In the GUI conftest, `gc.collect()` after the deferred
deletes every 25th test (count in a module global): measure the serial time
before and after and keep it only if it is not slower. Remove
`tests/slices/`. Check with `scripts/audit/leak_plugin.py`: widgets at the
end under 500.

**R6. `docs: DESIGN §8 says what the suite is`**
Files: `docs/DESIGN.md`. Replace the first paragraph of §8; keep the rest.

### New tests

**N1. Comma decimals** (G-01). Ask the user first which way: the C locale
for the whole program (`QLocale.setDefault(QLocale.c())` in `gui/app.py`
before the first widget) or both signs accepted. Then
`tests/gui/test_number_field.py`: under `QLocale.setDefault` of `nl_BE`,
put back after, typing `1.5` reads 1.5; the three tests of T-11 pass under
`scripts/audit/locale_plugin.py`.

**N2. Files that cannot be read or written** (G-03). New
`tests/config/test_store_io.py`; additions to `tests/core/test_routine.py`
and `tests/core/test_liquid_program.py`.

**N3. A stale job's end** (G-02). `tests/gui/test_jog_panel.py` (or
`test_jog_pose_kept.py` before R2). Hold the first job on an event so the
order is certain; no sleep.

**N4. A step from above the top** (G-05). `tests/workflows/test_jog_steps.py`,
with `MockRobot(top_mm=...)`.

**N5. A camera opening at shutdown** (G-08). `tests/gui/test_auto_camera.py`.
If it shows the session leaves the camera open, that is a finding for the
user, not a line to change in passing.

**N6. A built app's folders** (G-04). `tests/config/test_seed_bundle.py`.

Each new test is shown to fail first: run it against the break named for
its gap in `scripts/audit/mutate.py` (N2, N3, N6) or against the code with
the tested line commented out, then put the code back.

## 8. The audit's scripts

All under `scripts/audit/`; each file's docstring says how to run it. None
touches the robot or a camera.

| Script | For |
|---|---|
| `run_many.sh`, `junit_summary.py` | The repeated runs of 2.1 and what failed in which |
| `stress.sh`, `repeat_plugin.py` | A test's failure rate under load |
| `hang_dump.py` | The panels, the lease and the jobs' last 60 events, added to a failing test's report |
| `slow_mock.py` | The three stretched windows of 2.3 |
| `wait_helpers.py` | Every wait loop in the tests, by shape |
| `duplicates.py` | Copied helpers, repeated names, near-copy tests |
| `leak_plugin.py`, `left_after_window.py` | What a test and a MainWindow leave alive |
| `time_window.py` | What a MainWindow costs, plain, themed, profiled |
| `coverage_report.py` | Coverage per module, lines not run, the diff between two runs |
| `mutate.py` | The 16 breaks of T-10 |
| `limbo_join.py` | Cause E with plain threads |
| `comma_locale.py`, `locale_plugin.py` | G-01 and T-11 |

Per-test coverage contexts were tried and dropped: on Python 3.14 coverage
measures through `sys.monitoring`, which sees the jobs' QThreads but stops
reporting a line once it ran, so contexts are incomplete; the older tracer
keeps contexts and does not see a QThread. A second whole run without the
tests in question is the check that works here.
