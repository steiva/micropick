"""Slice picking: a small program of its own for a fixed microscope.

Not a part of the main window. It shares the robot's rules with it
(`workflows.motion`, `hardware`) and its look (`gui.theme`, `CameraView`,
`JogPad`) and nothing of `gui.session`. See `calibration` for the geometry,
which is the opposite of the main program's.
"""
