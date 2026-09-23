# micropick

Lab robot (Opentrons OT-2 gantry, pipette, upper and lower cameras) that
picks cuboid microtissues from a dish into a plate. Design notes are in
`docs/DESIGN.md`; bench checks for the GUI in `docs/GUI_BENCH_CHECKS.md`.

## Running things

- Interpreter: conda env `lab` (`conda run -n lab python ...`); the bare
  `python` on PATH is the Windows Store stub.
- Tests: `conda run -n lab python -m pytest -q` (whole suite, ~20 s).
- GUI: `conda run -n lab python -m micropick.gui` (`--mock` for no hardware).
- `lab` has an unrelated package named `tests` installed, so
  `python -m tests.…` resolves to it, not to this repository's `tests/`.

## Decisions to keep

### Picking: analysis is live, a run holds its decision frame

- On the Picking page, *Analyse the dish* draws its contours over the
  **live** feed. If the dish moves afterwards, the contours visibly stop
  matching, which is the cue to analyse again. There is no "back to live".
- During a **run**, the view follows `PickView` from `workflows/picking`:
  live while the session waits for the operator, otherwise the **frame the
  decision was made from is held**, unchanged, with the overlays measured on
  it. Do not make the run view always-live — the overlays belong to that one
  frame, and drawing them over a newer one shows contours where nothing was
  measured.
- Start picking asks for confirmation (dish and plate in place, lids off,
  settings right) and then gives the session its go-ahead at once;
  Resume is only for leaving `needs_operator`.

### Camera view chrome

Text about the camera or the run (resolution, fps, zoom, robot status, gantry
position) is drawn by `CameraView` in widget pixels as semi-transparent
boxes over the picture, never into the frame and never as a separate panel.
