# micropick

Control software for an Opentrons OT-2 that has been extended into a platform for
handling tumor microtissues (cuboids), fluids, and hydrogels.

A standard OT-2 cannot detect, pick, or sort tissue. A set of custom hardware
modules adds those capabilities: a gantry-mounted camera for workspace-wide
detection and localization, a back-illuminated picking platform with an actuated
dish-retaining ring, an automatic pipette calibration module, and a HEPA
filtration unit for sterile operation on the deck. This repository contains the
software that drives them.

## What it does

- Detects and localizes cuboids in a dish from the gantry camera
- Maps image coordinates to robot coordinates, including lens distortion
- Runs pick-and-place routines into well plates, with verification and retries
- Detects floating cuboids and excludes them from picking
- Logs experiment runs in a readable format

## Installation

Requires Python 3.10 or newer.

```bash
git clone https://github.com/steiva/micropick.git
cd micropick
pip install -e ".[hardware,ml,dev]"
```

On a machine without the robot attached, the hardware extras can be skipped:

```bash
pip install -e ".[dev]"
```

Model weights are not tracked in this repository. Place them in `ml_models/`
before running detection routines.

## Layout

```
src/micropick/
  config/        profile schema and storage
  core/          calibration, vision, geometry (no hardware imports)
  hardware/      robot and camera interfaces, plus mocks for offline work
  workflows/     calibration and picking routines
  viz/           overlay rendering
labware/         custom Opentrons labware definitions
profiles/        per-installation configuration
tests/
```

## Configuration

Each installation gets a profile under `profiles/`. Profiles carry a schema
version and are validated on load, so a mismatch fails immediately rather than
surfacing later as a positioning error. `profiles/example/` documents the format.

## Acknowledgements

Developed in the Folch Lab at the University of Washington. Robot communication
uses [`opentrons-ot2-http-python-wrapper`](https://github.com/steiva/opentrons-ot2-http-python-wrapper).
