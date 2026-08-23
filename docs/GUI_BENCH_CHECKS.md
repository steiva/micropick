# GUI checks that need the bench

Everything in the GUI is developed against `--mock`, which covers most of it.
This file collects what mocks cannot answer: the real robot, the real cameras,
the machine the rig is actually driven from.

One entry per check. Each says which commit introduced the need for it and what
failure to expect if something is wrong, so a check that goes badly points at
something rather than just at "the GUI".

---

## Commit 1 — package skeleton, entry point, theme

- [ ] **`micropick-gui` starts on the bench machine.**
  Expected failure if wrong: `ModuleNotFoundError` for PySide6 — the extra was
  never installed there (`pip install -e ".[gui]"`). This is the only check
  here that is about the installation rather than the rig.

- [ ] **The UI font is "Segoe UI Variable Text".**
  The bench machine is the only one that has it; every development machine
  falls back. Compare against a screenshot from a development machine: if they
  look identical, `theme.FONT_FAMILIES` did not match and the fallback ran.

- [ ] **The window is not blurred or half-sized on the bench display.**
  `theme.configure_hidpi` sets a PassThrough rounding policy, which is only
  exercised on a fractional-scale display. Expected failure if wrong: text
  crisp but everything oversized (rounding to 2x), or a window that opens at
  half the requested 1400x900.

- [ ] **The theme follows the operating system's light/dark setting.**
  `qdarktheme` reads it through `darkdetect`, which is per-platform. Flip the
  OS setting with the application open. Expected failure if wrong: nothing
  changes, and `micropick.qss` is not the cause — it holds no colours.

---

## Commit 2 — application shell

- [ ] **Navigation rows are not clipped and do not overlap.**
  The row height is computed from the font's metrics, and the bench machine is
  the only one running the intended font, so this is the only place the number
  is what it will be in use. Expected failure if wrong: the selected row's
  rounded highlight runs into the entry below it, which is what stylesheet
  `padding` and `min-height` did before the height moved into `shell.py` —
  qdarktheme's QProxyStyle paints those but does not measure them.

Nothing else in this commit touches hardware: the status bar's three fields are
static until the session owns them.

---

## Commit 3 — session

- [ ] **Connect brings the robot up in the right order.**
  `create_run()`, then the custom definitions, then `load_pipette()`. The log
  shows all three with the run id and the count of definitions. Expected
  failure if the order is wrong: the connect appears to succeed and the first
  jog step fails instead, because nothing that moves the robot works before a
  run and a pipette exist — DESIGN section 9.

- [ ] **The definitions in `labware/` reach the run.**
  The log line says how many were uploaded and names them. Expected failure if
  wrong: `LabwareError` with the directory it looked in, and no connection —
  deliberately, since a run without them cannot load a plate.

- [ ] **The window does not freeze while connecting.**
  The robot is on the other end of HTTP. Drag the window during the connect;
  it should keep repainting, and Connect should be greyed out until the log
  says the pipette is loaded. Expected failure if the worker is bypassed: the
  window stops responding for the length of the round trip.

- [ ] **Both real cameras open at the resolutions in the profile.**
  The log line reports what was actually opened, and the lower camera should
  report its view crop. Expected failure: `CameraError` naming the requested
  and the delivered resolution — the camera refuses to resize rather than
  invalidating the pixel map silently.

- [ ] **Closing the window parks the axis and releases the devices.**
  Watch the gantry: `leftZ` retracts. Afterwards no python process holds the
  cameras — reopening the application should find them free. Expected failure:
  a second launch reports the device is busy, which means a grab thread
  outlived the window.

- [ ] **A real profile's calibration state reads correctly.**
  The Profile page should show the degree, the pose count and the held-out
  error of the profile fitted on the bench, not "not calibrated".
