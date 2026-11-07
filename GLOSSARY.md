# micropick

A lab robot (OT-2 gantry, pipette, upper and lower cameras) that picks
cuboid microtissues from a dish into a plate.

## Moving the tip

**Motion**:
Every move of the tip across the deck, and the rules they share: travel height, the measured top, reach. One per robot connection, used by every page and run alike.
_Avoid_: mover, travel, drive

**Retract**:
The tip straight up where it stands, to the travel height, from anywhere in the program. Above the travel height it stays. While the measured top is not known it goes to the top and measures it.
_Avoid_: home (homing is all axes, to their switches), lift (a lift ends an aspirate or a dispense)

**Lift**:
How the tip leaves the liquid after an aspirate or a dispense: right away, after a wait, or not at all.
_Avoid_: retract (on screen, for this)

**Lift slowly**:
The tip first rises a little, very slowly, so the liquid leaving with it does not pull a cuboid along; then it goes up as usual.
_Avoid_: retract slowly (on screen), lift out slowly

**Measured top**:
The Z the robot reports right after a retract. It depends only on the tip that is on, so it holds until the tip or the robot connection changes.
_Avoid_: z top, Z max, ceiling

**Travel height**:
The height a move across the deck is made at: 1 mm under the measured top, since the robot refuses a move to the retracted height itself.
_Avoid_: safe height, clearance height

**Well height**:
Where in a well the tip goes: the top, the middle or the bottom of the well, with an offset up or down from it.
_Avoid_: center (the well center is a place in X and Y, not a height)

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

**Setup position**:
One of the three positions set only in Robot setup: the calibration position, the shake position and the position over the dish. Never listed or drawn among the points.
_Avoid_: system point, saved point

**Shake position**:
The stored position of the tip in the hole of the source dish's holder. It is set with a calibrated tip on, so it comes after the calibration position.
_Avoid_: shake (on screen)

**Position over the dish**:
The stored position the upper camera looks at the whole source dish from, its rim around the crosshair.
_Avoid_: observe (on screen)

**Source dish**:
The dish the robot picks from.
_Avoid_: dish with cuboids, picking dish

**Adjust by hand**:
After a tip calibration, the operator moving the tip onto the central crosshair and saving where it ended up. Optional.
_Avoid_: fine-tune, touch-up (on screen)

**Drop in place**:
Letting go of the tip where the pipette is, nothing moved. The tip does not go back into its rack.
_Avoid_: put back

**Reset setup**:
Clearing the robot setup to start it again, after the user confirms. The deck and the picking settings are kept.

**Lock**:
A state the operator turns on to work at the deck with their hands: nothing moves the robot until it is turned off. It cannot be turned on during a run.
_Avoid_: hold (a hold is Stop's second press, ended by the robot becoming free)

## A day's work

**Task**:
One of the three kinds of work started from Home: Auto pick, Manual pick, Liquid. A task is an order of steps.
_Avoid_: mode, workflow

**Step**:
One screen of a task (a plan step is something else). A step is done when the state it sets up holds now, never because it was visited. Deck, Tip and Dish bottom are the same steps, with the same state, in every task that has them.

**Optional step**:
A step a task can go past without its being done. It still gets its done mark, once the state it sets up holds.

**Path item**:
One line of a path on screen: a thing the operator goes through once, in order, shown done, current or later, never numbered. Not a step of a task and not a plan step.
_Avoid_: step (for this)

**Run step**:
The last step of every task, where the work itself is done. In Manual pick it holds no run: each action there is a short job.
_Avoid_: Work, Picking (as a step name)

**Well center**:
The measured center of a plate's wells, one per slot and plate type. Measuring it is a step in every task, for the plates the operator chooses; a plate not measured uses its drawing. It belongs to the module height it was measured on: on another height the plate is not measured.

**Plate plan**:
Which wells of a plate get cuboids, how many each, and in what order.
_Avoid_: routine (on screen), program

**Liquid plan**:
An ordered list of plan steps, with one trash for all of them.
_Avoid_: program (on screen), protocol

**Plan step**:
One line of a liquid plan: move liquid from a source to destinations, mix, blow out, move the tip, wait, or wait for the operator. On screen "Step 2". Not a step of a task.
_Avoid_: group, block

**Trash**:
The one well or point of a liquid plan that leftover liquid goes to. A run needs it.
_Avoid_: waste (in code names)

**Spot**:
A place kept in a liquid plan's step by its coordinates: brought to by hand, or a memo point used there.
_Avoid_: here

**Template**:
A plate plan or a liquid plan saved under a name the user gave it, as a file that can be shared.
_Avoid_: preset

**Memo point**:
A point that lasts until the program closes. Marked with a right click on the picture it is named M1, M2 ...; a saved point the user takes the star off keeps its name.
_Avoid_: mark

**Saved point**:
A point with a name the user gave it, kept in the profile between days and marked with a star. Made by starring a memo point.
_Avoid_: position (positions are Robot setup's), preset
