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
