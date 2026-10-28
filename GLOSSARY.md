# micropick

A lab robot (OT-2 gantry, pipette, upper and lower cameras) that picks
cuboid microtissues from a dish into a plate.

## Moving the tip

**Motion**:
Every move of the tip across the deck, and the rules they share: travel height, the measured top, reach. One per robot connection, used by every page and run alike.
_Avoid_: mover, travel, drive

**Retract**:
Driving the Z axis all the way up, to the top of its travel with the tip that is on.
_Avoid_: home (homing is all axes, to their switches)

**Measured top**:
The Z the robot reports right after a retract. It depends only on the tip that is on, so it holds until the tip or the robot connection changes.
_Avoid_: z top, Z max, ceiling

**Travel height**:
The height a move across the deck is made at: 1 mm under the measured top, since the robot refuses a move to the retracted height itself.
_Avoid_: safe height, clearance height

**Park**:
Moving the head out of the way, to the back right of the deck at the travel height, without homing.
_Avoid_: home (homing drives all axes to their switches and is done from Settings)

**Jog envelope**:
The box a jog step or a click target must stay in: the deck's X and Y, a floor raised over each deck module, and a top computed from the bare nozzle's height less the tip's length.
_Avoid_: soft limits (on screen only), bounds

**Reachable**:
Of a stored pose: no higher than the travel height. A pose taught at the top is reached 1 mm under it.

## Using the robot

**Run**:
A picking run, a liquid run, the camera calibration sweep or the pipette calibration: work that has the robot from Start to its end, its pauses and waits for the operator included.
_Avoid_: job (a job is short: a jog step, a Go to, Home)

**Robot busy**:
Something has the robot - a run, or a short job on any page - so nothing else may move it or change its robot session until that ends. A request meanwhile is refused, never kept for later.
_Avoid_: locked, queued

**Robot session**:
The OT-2's HTTP run, holding the loaded labware, the offsets and the tip record.
_Avoid_: run (a run is a picking or liquid run)

## Setting up

**Robot setup**:
What is set once for this robot and its cameras and kept between days: which camera is the upper and which the lower, the camera calibration, the platforms, and the stored positions.
_Avoid_: profile (the profile is how it is stored, never shown)

**Calibration position**:
The stored position the upper camera looks at the crosshair disc on the calibration module from. The tip calibration starts by going there.
_Avoid_: disc position, tip_calib (on screen)

**Shake position**:
The stored position of the tip in the hole of the source dish's holder. It is set with a calibrated tip on, so it comes after the calibration position.
_Avoid_: shake (on screen)

**Position over the dish**:
The stored position the upper camera looks at the whole source dish from, its rim around the crosshair.
_Avoid_: observe (on screen)

**Source dish**:
The dish the robot picks from.
_Avoid_: dish with cuboids, picking dish

**Reset setup**:
Clearing the robot setup to start it again, after the user confirms. The deck and the picking settings are kept.

**Lock**:
A state the operator turns on to work at the deck with their hands: nothing moves the robot until it is turned off. It cannot be turned on during a run.
_Avoid_: hold (a hold is Stop's second press, ended by the robot becoming free)

## A day's work

**Task**:
One of the three kinds of work started from Home: Auto pick, Manual pick, Liquid. A task is a fixed order of steps.
_Avoid_: mode, workflow

**Step**:
One screen of a task. A step is done when the state it sets up holds now, never because it was visited. Deck, Tip and Dish bottom are the same steps, with the same state, in every task that has them.

**Plate plan**:
Which wells of a plate get cuboids, how many each, and in what order.
_Avoid_: routine (on screen), program

**Liquid plan**:
Groups of wells and the steps each well gets, run in order.
_Avoid_: program (on screen), protocol

**Template**:
A plate plan or a liquid plan saved under a name the user gave it, as a file that can be shared.
_Avoid_: preset

**Skipped plate**:
A plate whose well center the user chose not to measure in this robot session; its drawing is used instead.
