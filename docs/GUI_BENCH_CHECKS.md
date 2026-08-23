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
