"""The desktop application.

The same outer layer as the notebooks, and it obeys the same rules. `gui` may
use `config`, `core`, `hardware`, `workflows` and `viz`; **nothing below it
imports `gui`**, so the package still works, and still tests, with the GUI
uninstalled.

Two further rules exist because this layer replaces a cv2 window, not a
library:

* No `cv2.imshow`, `cv2.namedWindow` or `cv2.waitKey` anywhere under `gui`.
  OpenCV is used here as an array library and nothing else; the windows and the
  keys are Qt's.
* Only `gui/session.py` and `gui/workers/` talk to `openapi.*`. Pages reach the
  robot through the session and through workers, never directly, so there is
  one place that knows whether the robot is real.

Importing this package pulls in nothing from Qt. The submodules do.
"""
